[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('start', 'stop')]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [Parameter(Mandatory = $true)]
    [string]$StateDir,
    [int]$Port = 49151
)

$ErrorActionPreference = 'Stop'

$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'pymobiledevice3 tunneld management requires an elevated process.'
}

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$pidPath = Join-Path $StateDir 'tunneld.pid'
$logDir = Join-Path $StateDir 'logs'

function Get-TunneldListener {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Test-TunneldReady {
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:$Port/" -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

if ($Action -eq 'stop') {
    if (-not (Test-Path $pidPath)) {
        Write-Host 'tunneld is not managed by phone-harness.'
        exit 0
    }

    $managedPid = [int](Get-Content $pidPath -Raw).Trim()
    $listener = Get-TunneldListener
    if ($listener -and $listener.OwningProcess -eq $managedPid) {
        Stop-Process -Id $managedPid -Force -ErrorAction Stop
        Start-Sleep -Milliseconds 500
    }
    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    Write-Host 'phone-harness tunneld stopped.'
    exit 0
}

if (-not (Test-Path $PythonPath -PathType Leaf)) {
    throw "pymobiledevice3 tunneld Python executable not found: $PythonPath"
}

if (Test-TunneldReady) {
    $listener = Get-TunneldListener
    if (-not $listener) { throw 'tunneld health endpoint is ready but no listener PID could be resolved.' }
    $listener.OwningProcess | Set-Content $pidPath -Encoding ascii
    Write-Host "phone-harness tunneld already ready; adopted PID $($listener.OwningProcess)."
    exit 0
}

$occupied = Get-TunneldListener
if ($occupied) {
    throw "Port $Port is already listening but does not answer as pymobiledevice3 tunneld (PID $($occupied.OwningProcess))."
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutPath = Join-Path $logDir 'pymobiledevice3-tunneld.stdout.log'
$stderrPath = Join-Path $logDir 'pymobiledevice3-tunneld.stderr.log'
$process = Start-Process `
    -FilePath $PythonPath `
    -ArgumentList @('-m', 'pymobiledevice3', 'remote', 'tunneld') `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru
$process.Id | Set-Content $pidPath -Encoding ascii

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if (Test-TunneldReady) {
        Write-Host "phone-harness tunneld ready; PID $($process.Id)."
        exit 0
    }
    if ($process.HasExited) { break }
    Start-Sleep -Seconds 1
}

$stderr = if (Test-Path $stderrPath) { (Get-Content $stderrPath -Tail 40) -join "`n" } else { '' }
Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
throw "pymobiledevice3 tunneld did not become ready.`n$stderr"
