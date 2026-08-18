[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$stateDir = Join-Path $env:LOCALAPPDATA 'phone-harness-mcp'
$configPath = Join-Path $stateDir 'config.json'
$config = if (Test-Path $configPath) { Get-Content $configPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }

$tunnelClientPath = [string]$config.tunnelClientPath
$tunnelProfilePath = [string]$config.tunnelProfilePath
$port = if ($config.port) { [int]$config.port } else { 17677 }
$localMetadataUrl = "http://127.0.0.1:$port/.well-known/oauth-protected-resource/mcp"
$localResourceUrl = "http://127.0.0.1:$port/mcp"

function Get-ConfiguredTunnelProcesses {
    $matches = @()
    $candidates = Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" -ErrorAction SilentlyContinue
    foreach ($candidate in $candidates) {
        $isMatch = $tunnelClientPath -and $tunnelProfilePath -and
            $candidate.ExecutablePath -eq $tunnelClientPath -and
            $candidate.CommandLine -like "*$tunnelProfilePath*"
        if (-not $isMatch) {
            $listeners = Get-NetTCPConnection -OwningProcess $candidate.ProcessId -State Listen -ErrorAction SilentlyContinue |
                Where-Object { $_.LocalAddress -in @('127.0.0.1', '::1') }
            foreach ($listener in $listeners) {
                try {
                    $oauthState = Invoke-RestMethod "http://127.0.0.1:$($listener.LocalPort)/api/oauth" -TimeoutSec 1
                    if (@($oauthState.discovery_urls).Contains($localMetadataUrl) -or
                        [string]$oauthState.metadata.body.resource -eq $localResourceUrl) {
                        $isMatch = $true
                        break
                    }
                } catch {
                    continue
                }
            }
        }
        if ($isMatch) { $matches += $candidate }
    }
    return @($matches)
}

$configuredTunnelProcesses = @(Get-ConfiguredTunnelProcesses)
$elevatedStopIds = @()
foreach ($candidate in $configuredTunnelProcesses) {
    try {
        Stop-Process -Id $candidate.ProcessId -Force -ErrorAction Stop
        Write-Host "Secure MCP Tunnel stopped. PID $($candidate.ProcessId)"
    } catch {
        $elevatedStopIds += [int]$candidate.ProcessId
    }
}
if ($elevatedStopIds.Count -gt 0) {
    $helper = Join-Path $PSScriptRoot 'manage_phone_harness_tunnel_client.ps1'
    $argumentList = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', "`"$helper`"",
        '-ProcessIds', "`"$($elevatedStopIds -join ',')`""
    )
    try {
        $null = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argumentList -PassThru
    } catch {
        throw "Administrator approval to stop stale Secure MCP Tunnel processes was cancelled or failed: $($_.Exception.Message)"
    }
}
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if (@(Get-ConfiguredTunnelProcesses).Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
}
$remainingTunnelProcesses = @(Get-ConfiguredTunnelProcesses)
if ($remainingTunnelProcesses.Count -gt 0) {
    throw "Secure MCP Tunnel processes are still running: $($remainingTunnelProcesses.ProcessId -join ', ')"
}
Remove-Item (Join-Path $stateDir 'tunnel-client.pid') -Force -ErrorAction SilentlyContinue

foreach ($entry in @(
    @{ Name = 'phone-harness MCP'; Path = (Join-Path $stateDir 'mcp.pid'); Expected = 'node' }
)) {
    if (-not (Test-Path $entry.Path)) {
        Write-Host "$($entry.Name) already stopped/unmanaged."
        continue
    }
    $pidValue = [int](Get-Content $entry.Path -Raw).Trim()
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq $entry.Expected) {
        Stop-Process -Id $pidValue -Force
        Write-Host "$($entry.Name) stopped. PID $pidValue"
    } else {
        Write-Host "$($entry.Name) PID file was stale; no process stopped."
    }
    Remove-Item $entry.Path -Force -ErrorAction SilentlyContinue
}

$tunneldPidPath = Join-Path $stateDir 'tunneld.pid'
if (Test-Path $tunneldPidPath) {
    $tunneldPythonPath = [string]$config.tunneldPythonPath
    if (-not $tunneldPythonPath) {
        $tunneldPythonPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Intelligence Works\products\phone-harness-windows\.venv-tunneld\Scripts\python.exe'
    }
    $tunneldPort = if ($config.tunneldPort) { [int]$config.tunneldPort } else { 49151 }
    $helper = Join-Path $PSScriptRoot 'manage_phone_harness_tunneld.ps1'
    $argumentList = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', "`"$helper`"",
        '-Action', 'stop',
        '-PythonPath', "`"$tunneldPythonPath`"",
        '-StateDir', "`"$stateDir`"",
        '-Port', [string]$tunneldPort
    )
    try {
        $null = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argumentList -PassThru
    } catch {
        throw "Administrator approval for pymobiledevice3 tunneld stop was cancelled or failed: $($_.Exception.Message)"
    }
    $stopped = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $listener = Get-NetTCPConnection -LocalPort $tunneldPort -State Listen -ErrorAction SilentlyContinue
        if (-not $listener) {
            $stopped = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $stopped) { throw "pymobiledevice3 tunneld is still listening on port $tunneldPort after elevated stop." }
} else {
    Write-Host 'pymobiledevice3 tunneld already stopped/unmanaged.'
}

Write-Host 'STOPPED: phone-harness managed runtime is stopped.'
