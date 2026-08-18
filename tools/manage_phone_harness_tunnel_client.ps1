[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProcessIds
)

$ErrorActionPreference = 'Stop'

foreach ($value in ($ProcessIds -split ',')) {
    if (-not $value) { continue }
    $processId = [int]$value
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    if ($process.ProcessName -ne 'tunnel-client') {
        throw "Refusing to stop PID $processId because it is $($process.ProcessName), not tunnel-client."
    }
    Stop-Process -Id $processId -Force
}
