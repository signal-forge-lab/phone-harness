$ErrorActionPreference = 'Stop'

$ruleName = 'Phone Harness Monitor LAN'
$port = 17678

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $MyInvocation.MyCommand.Path + '"')
    )
    exit
}

Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

New-NetFirewallRule `
    -DisplayName $ruleName `
    -Description 'Allow the responsive Phone Harness monitor from devices on the local private subnet only.' `
    -Direction Inbound `
    -Action Allow `
    -Enabled True `
    -Profile Private `
    -Protocol TCP `
    -LocalPort $port `
    -RemoteAddress LocalSubnet | Out-Null

Write-Host "Enabled '$ruleName' for TCP $port, Private profile, LocalSubnet only (program-independent)."

