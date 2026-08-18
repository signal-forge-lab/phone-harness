[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Start phone-harness from PowerShell 7 or later.'
}

$stateDir = Join-Path $env:LOCALAPPDATA 'phone-harness-mcp'
$configPath = Join-Path $stateDir 'config.json'
if (-not (Test-Path $configPath)) {
    throw 'MCP configuration is missing. Run tools\configure_phone_harness_mcp.ps1 first.'
}

$config = Get-Content $configPath -Raw | ConvertFrom-Json
$tunneldPythonPath = [string]$config.tunneldPythonPath
$tunneldPort = if ($config.tunneldPort) { [int]$config.tunneldPort } else { 49151 }
if (-not $tunneldPythonPath) {
    $tunneldPythonPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Intelligence Works\products\phone-harness-windows\.venv-tunneld\Scripts\python.exe'
}
if (-not (Test-Path $tunneldPythonPath -PathType Leaf)) {
    throw "Configured pymobiledevice3 tunneld Python executable not found: $tunneldPythonPath"
}

$tunneldPidPath = Join-Path $stateDir 'tunneld.pid'
function Test-TunneldReady {
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:$tunneldPort/" -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

if (Test-TunneldReady) {
    $listener = Get-NetTCPConnection -LocalPort $tunneldPort -State Listen -ErrorAction Stop | Select-Object -First 1
    $listener.OwningProcess | Set-Content $tunneldPidPath -Encoding ascii
    Write-Host "pymobiledevice3 tunneld PASS  PID $($listener.OwningProcess) (reused/adopted)"
} else {
    $helper = Join-Path $PSScriptRoot 'manage_phone_harness_tunneld.ps1'
    $argumentList = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', "`"$helper`"",
        '-Action', 'start',
        '-PythonPath', "`"$tunneldPythonPath`"",
        '-StateDir', "`"$stateDir`"",
        '-Port', [string]$tunneldPort
    )
    try {
        $null = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argumentList -PassThru
    } catch {
        throw "Administrator approval for pymobiledevice3 tunneld startup was cancelled or failed: $($_.Exception.Message)"
    }
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-TunneldReady) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'pymobiledevice3 tunneld did not become ready after elevated startup.' }
    $listener = Get-NetTCPConnection -LocalPort $tunneldPort -State Listen -ErrorAction Stop | Select-Object -First 1
    Write-Host "pymobiledevice3 tunneld PASS  PID $($listener.OwningProcess) (started elevated)"
}

& (Join-Path $PSScriptRoot 'start_phone_harness_mcp.ps1')
if (-not $?) { throw 'phone-harness MCP startup failed.' }

Write-Host 'READY: phone-harness, pymobiledevice3 tunneld, MCP, and Secure MCP Tunnel are running.'
