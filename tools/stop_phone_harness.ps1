[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$stateDir = Join-Path $env:LOCALAPPDATA 'phone-harness-mcp'
$configPath = Join-Path $stateDir 'config.json'
$config = if (Test-Path $configPath) { Get-Content $configPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }

foreach ($entry in @(
    @{ Name = 'Secure MCP Tunnel'; Path = (Join-Path $stateDir 'tunnel-client.pid'); Expected = 'tunnel-client' },
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
