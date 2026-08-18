[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

if (-not $IsWindows) {
    throw 'phone-harness MCP startup is currently supported on Windows only.'
}

$stateDir = Join-Path $env:LOCALAPPDATA 'phone-harness-mcp'
$configPath = Join-Path $stateDir 'config.json'
$secretPath = Join-Path $stateDir 'owner-token.dpapi'
$pidPath = Join-Path $stateDir 'mcp.pid'
$tunnelPidPath = Join-Path $stateDir 'tunnel-client.pid'
if (-not (Test-Path $configPath) -or -not (Test-Path $secretPath)) {
    throw 'MCP configuration is missing. Run tools\configure_phone_harness_mcp.ps1 first.'
}

$config = Get-Content $configPath -Raw | ConvertFrom-Json
$encryptedOwnerToken = (Get-Content $secretPath -Raw).Trim()
$secure = ConvertTo-SecureString $encryptedOwnerToken
$ownerToken = [System.Net.NetworkCredential]::new('', $secure).Password
if ($ownerToken.Length -lt 16) { throw 'Stored owner token is invalid.' }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$mcpDir = Join-Path $repoRoot 'mcp-server'
$node = (Get-Command node.exe -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$tunnelClientPath = [string]$config.tunnelClientPath
$tunnelProfilePath = [string]$config.tunnelProfilePath
$controlPlaneApiKeyEnvName = [string]$config.controlPlaneApiKeyEnvName
if (-not $tunnelClientPath -or -not $tunnelProfilePath -or -not $controlPlaneApiKeyEnvName) {
    throw 'Tunnel startup configuration is missing. Run tools\configure_phone_harness_mcp.ps1 once to migrate the configuration.'
}
if (-not (Test-Path $tunnelClientPath -PathType Leaf)) { throw "Configured tunnel-client executable not found: $tunnelClientPath" }
if (-not (Test-Path $tunnelProfilePath -PathType Leaf)) { throw "Configured tunnel-client profile not found: $tunnelProfilePath" }
$controlPlaneApiKey = [Environment]::GetEnvironmentVariable($controlPlaneApiKeyEnvName, 'Process')
if (-not $controlPlaneApiKey) { $controlPlaneApiKey = [Environment]::GetEnvironmentVariable($controlPlaneApiKeyEnvName, 'User') }
if (-not $controlPlaneApiKey) { $controlPlaneApiKey = [Environment]::GetEnvironmentVariable($controlPlaneApiKeyEnvName, 'Machine') }
if (-not $controlPlaneApiKey) {
    throw "Control-plane API key environment variable is missing: $controlPlaneApiKeyEnvName. Persist it with tools\configure_phone_harness_mcp.ps1 -PersistControlPlaneApiKey."
}
[Environment]::SetEnvironmentVariable($controlPlaneApiKeyEnvName, $controlPlaneApiKey, 'Process')
$profileText = Get-Content $tunnelProfilePath -Raw
if ($profileText -notmatch [regex]::Escape("env:$controlPlaneApiKeyEnvName")) {
    throw "Tunnel profile does not reference env:$controlPlaneApiKeyEnvName"
}

$env:PHONE_HARNESS_MCP_PUBLIC_BASE_URL = [string]$config.publicBaseUrl
$env:PHONE_HARNESS_MCP_OAUTH_ISSUER_URL = [string]$config.oauthIssuerUrl
$env:PHONE_HARNESS_MCP_OAUTH_RESOURCE_URL = [string]$config.oauthResourceUrl
$env:PHONE_HARNESS_MCP_OWNER_TOKEN = $ownerToken
$env:PHONE_HARNESS_MCP_PORT = [string]$config.port
$env:PHONE_HARNESS_PYTHON = [string]$config.pythonPath
$env:PHONE_HARNESS_TRANSPORT = [string]$config.transport
if (-not (Test-Path $env:PHONE_HARNESS_PYTHON -PathType Leaf)) {
    throw "Configured Python executable not found: $env:PHONE_HARNESS_PYTHON"
}

Push-Location $mcpDir
try {
    & $npm run build
    if ($LASTEXITCODE -ne 0) { throw "MCP build failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}

$port = [int]$config.port
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
    $managedPid = if (Test-Path $pidPath) { [int](Get-Content $pidPath -Raw).Trim() } else { 0 }
    if ($process.ProcessName -ne 'node' -or $managedPid -ne $process.Id) {
        throw "Port $port is already owned by an unmanaged process $($process.ProcessName) (PID $($process.Id)). Stop it once, then rerun this script."
    }
    Stop-Process -Id $process.Id -Force
    Start-Sleep -Milliseconds 500
} elseif (Test-Path $pidPath) {
    Remove-Item $pidPath -Force
}

$process = Start-Process -FilePath $node -ArgumentList 'dist/index.js' -WorkingDirectory $mcpDir -PassThru
$process.Id | Set-Content $pidPath -Encoding ascii
$startedTunnelProcess = $null
$tunnelProcess = $null
$tunnelMode = $null

try {
$localBase = "http://127.0.0.1:$port"
$health = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $health = Invoke-RestMethod "$localBase/healthz" -TimeoutSec 2
        if ($health.ok) { break }
    } catch {
        Start-Sleep -Milliseconds 250
    }
}
if (-not $health.ok) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    throw 'MCP health check did not become ready.'
}

function Get-ProtectedResourceMetadataPath([Uri]$Resource) {
    $path = $Resource.AbsolutePath
    if ($path -eq '/') { return '/.well-known/oauth-protected-resource' }
    return "/.well-known/oauth-protected-resource$path"
}

$localResource = [Uri]::new([Uri][string]$config.publicBaseUrl, '/mcp')
$localPath = Get-ProtectedResourceMetadataPath $localResource
$localMetadata = Invoke-RestMethod "$localBase$localPath" -TimeoutSec 5
if ([string]$localMetadata.resource -ne $localResource.AbsoluteUri) {
    throw "Local OAuth resource mismatch: $($localMetadata.resource)"
}
if (-not @($localMetadata.authorization_servers).Contains(([Uri][string]$config.oauthIssuerUrl).AbsoluteUri)) {
    throw 'Local OAuth metadata does not advertise the configured authorization server.'
}

$denied = Invoke-WebRequest "$localBase/mcp" `
    -Method Post `
    -ContentType 'application/json' `
    -Headers @{ 'mcp-protocol-version' = '2026-07-28' } `
    -Body '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}' `
    -SkipHttpErrorCheck `
    -TimeoutSec 5
if ($denied.StatusCode -ne 401) { throw "Expected unauthenticated MCP request to return 401, got $($denied.StatusCode)." }

$localMetadataUrl = "{0}://{1}{2}" -f $localResource.Scheme, $localResource.Authority, $localPath
$challenge = [string]$denied.Headers['WWW-Authenticate']
if ($challenge -notlike "*resource_metadata=`"$localMetadataUrl`"*") {
    throw "OAuth challenge does not advertise local resource metadata: $challenge"
}

$doctorOutput = (& $tunnelClientPath doctor --profile-file $tunnelProfilePath --explain 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Secure MCP Tunnel doctor failed.`n$doctorOutput"
}

$matchingTunnel = Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $tunnelClientPath -and $_.CommandLine -like "*$tunnelProfilePath*" } |
    Select-Object -First 1
$managedTunnelPid = if (Test-Path $tunnelPidPath) { [int](Get-Content $tunnelPidPath -Raw).Trim() } else { 0 }
if ($matchingTunnel -and $managedTunnelPid -eq $matchingTunnel.ProcessId) {
    Stop-Process -Id $matchingTunnel.ProcessId -Force
    Start-Sleep -Milliseconds 500
    $matchingTunnel = $null
} elseif (-not $matchingTunnel -and (Test-Path $tunnelPidPath)) {
    Remove-Item $tunnelPidPath -Force
}

if ($matchingTunnel) {
    $tunnelProcess = Get-Process -Id $matchingTunnel.ProcessId -ErrorAction Stop
    $tunnelMode = 'reused existing process'
} else {
    $logDir = Join-Path $stateDir 'logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stdoutPath = Join-Path $logDir 'tunnel-client.stdout.log'
    $stderrPath = Join-Path $logDir 'tunnel-client.stderr.log'
    $startedTunnelProcess = Start-Process `
        -FilePath $tunnelClientPath `
        -ArgumentList @('run', '--profile-file', "`"$tunnelProfilePath`"") `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    $startedTunnelProcess.Id | Set-Content $tunnelPidPath -Encoding ascii
    Start-Sleep -Seconds 2
    $tunnelProcess = Get-Process -Id $startedTunnelProcess.Id -ErrorAction SilentlyContinue
    if (-not $tunnelProcess) {
        $stderr = if (Test-Path $stderrPath) { (Get-Content $stderrPath -Tail 30) -join "`n" } else { '' }
        throw "Secure MCP Tunnel client exited during startup.`n$stderr"
    }
    $tunnelMode = 'started by wrapper'
}

$tunnelHealthUrl = $null
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $healthListener = Get-NetTCPConnection -OwningProcess $tunnelProcess.Id -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @('127.0.0.1', '::1') } |
        Select-Object -First 1
    if ($healthListener) {
        $tunnelHealthUrl = "http://127.0.0.1:$($healthListener.LocalPort)"
        break
    }
    Start-Sleep -Milliseconds 250
}
if (-not $tunnelHealthUrl) {
    throw 'Secure MCP Tunnel health listener did not become available.'
}

$ready = $null
$lastReadyBody = ''
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $ready = Invoke-WebRequest "$tunnelHealthUrl/readyz" -SkipHttpErrorCheck -TimeoutSec 2
        $lastReadyBody = [string]$ready.Content
        if ($ready.StatusCode -eq 200) { break }
    } catch {
        $lastReadyBody = $_.Exception.Message
    }
    Start-Sleep -Milliseconds 500
}
if (-not $ready -or $ready.StatusCode -ne 200) {
    $oauthDiagnostic = ''
    try {
        $oauthDiagnostic = Invoke-RestMethod "$tunnelHealthUrl/api/oauth" -TimeoutSec 2 | ConvertTo-Json -Depth 8
    } catch {
        $oauthDiagnostic = $_.Exception.Message
    }
    throw "Secure MCP Tunnel is live but not ready. /readyz: $lastReadyBody`nOAuth diagnostics:`n$oauthDiagnostic"
}
} catch {
    if ($startedTunnelProcess) {
        Stop-Process -Id $startedTunnelProcess.Id -Force -ErrorAction SilentlyContinue
        Remove-Item $tunnelPidPath -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host "phone-harness MCP PID = $($process.Id)"
Write-Host "healthz                 PASS  $localBase/healthz"
Write-Host "local OAuth metadata      PASS  $localMetadataUrl"
Write-Host '401 OAuth challenge       PASS'
Write-Host "Secure MCP tunnel doctor PASS  env:$controlPlaneApiKeyEnvName"
Write-Host "Secure MCP tunnel client PASS  PID $($tunnelProcess.Id) ($tunnelMode)"
Write-Host "Secure MCP tunnel ready  PASS  $tunnelHealthUrl/readyz"
Write-Host 'READY: ChatGPT can now scan/reconnect the MCP App.'
