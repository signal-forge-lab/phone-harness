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

$canonicalResource = [Uri][string]$config.oauthResourceUrl
$canonicalPath = Get-ProtectedResourceMetadataPath $canonicalResource
$canonicalMetadata = Invoke-RestMethod "$localBase$canonicalPath" -TimeoutSec 5
if ([string]$canonicalMetadata.resource -ne $canonicalResource.AbsoluteUri) {
    throw "Canonical OAuth resource mismatch: $($canonicalMetadata.resource)"
}
if (-not @($canonicalMetadata.authorization_servers).Contains(([Uri][string]$config.oauthIssuerUrl).AbsoluteUri)) {
    throw 'Canonical OAuth metadata does not advertise the configured authorization server.'
}

$localResource = [Uri]::new([Uri][string]$config.publicBaseUrl, '/mcp')
$localPath = Get-ProtectedResourceMetadataPath $localResource
$localMetadata = Invoke-RestMethod "$localBase$localPath" -TimeoutSec 5
if ([string]$localMetadata.resource -ne $localResource.AbsoluteUri) {
    throw "Local compatibility OAuth resource mismatch: $($localMetadata.resource)"
}

$denied = Invoke-WebRequest "$localBase/mcp" `
    -Method Post `
    -ContentType 'application/json' `
    -Headers @{ 'mcp-protocol-version' = '2026-07-28' } `
    -Body '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}' `
    -SkipHttpErrorCheck `
    -TimeoutSec 5
if ($denied.StatusCode -ne 401) { throw "Expected unauthenticated MCP request to return 401, got $($denied.StatusCode)." }

$externalMetadataUrl = "{0}://{1}{2}" -f $canonicalResource.Scheme, $canonicalResource.Authority, $canonicalPath
$challenge = [string]$denied.Headers['WWW-Authenticate']
if ($challenge -notlike "*resource_metadata=`"$externalMetadataUrl`"*") {
    throw "OAuth challenge does not advertise canonical resource metadata: $challenge"
}
} catch {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host "phone-harness MCP PID = $($process.Id)"
Write-Host "healthz                 PASS  $localBase/healthz"
Write-Host "canonical OAuth metadata PASS  $externalMetadataUrl"
Write-Host "local OAuth compatibility PASS $($localResource.AbsoluteUri)"
Write-Host '401 OAuth challenge       PASS'
Write-Host 'READY: ChatGPT can now scan/reconnect the MCP App.'
