[CmdletBinding()]
param(
    [string]$PublicBaseUrl,
    [string]$OAuthIssuerUrl,
    [string]$OAuthResourceUrl,
    [int]$Port,
    [string]$PythonPath,
    [ValidateSet('usb', 'wifi', 'auto')]
    [string]$Transport,
    [switch]$RotateOwnerToken
)

$ErrorActionPreference = 'Stop'

if (-not $IsWindows) {
    throw 'phone-harness MCP configuration is currently supported on Windows only.'
}

$stateDir = Join-Path $env:LOCALAPPDATA 'phone-harness-mcp'
$configPath = Join-Path $stateDir 'config.json'
$secretPath = Join-Path $stateDir 'owner-token.dpapi'
$existing = if (Test-Path $configPath) {
    Get-Content $configPath -Raw | ConvertFrom-Json
} else {
    [pscustomobject]@{}
}

function Select-Value {
    param(
        [string]$Provided,
        [object]$Existing,
        [string]$Default,
        [string]$Prompt
    )
    if ($Provided) { return $Provided }
    if ($Existing) { return [string]$Existing }
    if ($Default) { return $Default }
    $value = Read-Host $Prompt
    if (-not $value) { throw "$Prompt is required." }
    return $value
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$defaultPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $defaultPython)) { $defaultPython = $null }

$resolvedPublicBaseUrl = Select-Value $PublicBaseUrl $existing.publicBaseUrl 'http://127.0.0.1:17677/' 'MCP public base URL'
$resolvedOAuthIssuerUrl = Select-Value $OAuthIssuerUrl $existing.oauthIssuerUrl $null 'OAuth issuer URL'
$resolvedOAuthResourceUrl = Select-Value $OAuthResourceUrl $existing.oauthResourceUrl $null 'Secure MCP Tunnel resource URL'
$resolvedPort = if ($PSBoundParameters.ContainsKey('Port')) { $Port } elseif ($existing.port) { [int]$existing.port } else { 17677 }
$resolvedPythonPath = Select-Value $PythonPath $existing.pythonPath $defaultPython 'phone-harness Python executable'
$resolvedTransport = if ($Transport) { $Transport } elseif ($existing.transport) { [string]$existing.transport } else { 'wifi' }

if ($resolvedPort -lt 1 -or $resolvedPort -gt 65535) { throw 'Port must be between 1 and 65535.' }
if (-not (Test-Path $resolvedPythonPath -PathType Leaf)) { throw "Python executable not found: $resolvedPythonPath" }

foreach ($item in @(
    @{ Name = 'PublicBaseUrl'; Value = $resolvedPublicBaseUrl },
    @{ Name = 'OAuthIssuerUrl'; Value = $resolvedOAuthIssuerUrl },
    @{ Name = 'OAuthResourceUrl'; Value = $resolvedOAuthResourceUrl }
)) {
    $uri = [Uri]$item.Value
    if (-not $uri.IsAbsoluteUri) { throw "$($item.Name) must be an absolute URL." }
    if ($uri.Scheme -ne 'https' -and $uri.Host -notin @('localhost', '127.0.0.1', '::1')) {
        throw "$($item.Name) must use HTTPS for non-loopback hosts."
    }
}

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

if ($RotateOwnerToken -or -not (Test-Path $secretPath)) {
    $secure = Read-Host 'Owner token (16+ characters; stored with Windows DPAPI)' -AsSecureString
    $plain = [System.Net.NetworkCredential]::new('', $secure).Password
    if ($plain.Length -lt 16) { throw 'Owner token must be at least 16 characters.' }
    $secure | ConvertFrom-SecureString | Set-Content $secretPath -Encoding utf8NoBOM
}

[ordered]@{
    publicBaseUrl = ([Uri]$resolvedPublicBaseUrl).AbsoluteUri
    oauthIssuerUrl = ([Uri]$resolvedOAuthIssuerUrl).AbsoluteUri
    oauthResourceUrl = ([Uri]$resolvedOAuthResourceUrl).AbsoluteUri
    port = $resolvedPort
    pythonPath = (Resolve-Path $resolvedPythonPath).Path
    transport = $resolvedTransport
} | ConvertTo-Json | Set-Content $configPath -Encoding utf8NoBOM

Write-Host "Saved MCP configuration: $configPath"
Write-Host "Saved encrypted owner token: $secretPath"
Write-Host 'Run tools\start_phone_harness_mcp.ps1 to build, restart, and verify the MCP.'
