[CmdletBinding()]
param(
    [string]$PublicBaseUrl,
    [string]$OAuthIssuerUrl,
    [string]$OAuthResourceUrl,
    [int]$Port,
    [string]$PythonPath,
    [string]$TunneldPythonPath,
    [int]$TunneldPort,
    [string]$TunnelClientPath,
    [string]$TunnelProfilePath,
    [string]$ControlPlaneApiKeyEnvName,
    [ValidateSet('usb', 'wifi', 'auto')]
    [string]$Transport,
    [switch]$RotateOwnerToken,
    [switch]$PersistControlPlaneApiKey
)

$ErrorActionPreference = 'Stop'

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'phone-harness MCP configuration requires PowerShell 7 or later. Run this script with pwsh.'
}
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

$defaultTunneldPython = if ($env:PHONE_HARNESS_TUNNELD_PYTHON) {
    $env:PHONE_HARNESS_TUNNELD_PYTHON
} else {
    Join-Path $repoRoot '.venv-tunneld\Scripts\python.exe'
}
if (-not (Test-Path $defaultTunneldPython -PathType Leaf)) { $defaultTunneldPython = $null }

$defaultTunnelClient = if ($env:PHONE_HARNESS_TUNNEL_CLIENT_PATH) {
    $env:PHONE_HARNESS_TUNNEL_CLIENT_PATH
} else {
    $null
}
if ($defaultTunnelClient -and -not (Test-Path $defaultTunnelClient -PathType Leaf)) { $defaultTunnelClient = $null }

$defaultTunnelProfile = if ($env:PHONE_HARNESS_TUNNEL_PROFILE_PATH) {
    $env:PHONE_HARNESS_TUNNEL_PROFILE_PATH
} else {
    $null
}
if ($defaultTunnelProfile -and -not (Test-Path $defaultTunnelProfile -PathType Leaf)) { $defaultTunnelProfile = $null }

$resolvedPublicBaseUrl = Select-Value $PublicBaseUrl $existing.publicBaseUrl 'http://127.0.0.1:17677/' 'MCP public base URL'
$resolvedOAuthIssuerUrl = Select-Value $OAuthIssuerUrl $existing.oauthIssuerUrl $null 'OAuth issuer URL'
$resolvedOAuthResourceUrl = Select-Value $OAuthResourceUrl $existing.oauthResourceUrl $null 'Secure MCP Tunnel resource URL'
$resolvedPort = if ($PSBoundParameters.ContainsKey('Port')) { $Port } elseif ($existing.port) { [int]$existing.port } else { 17677 }
$resolvedPythonPath = Select-Value $PythonPath $existing.pythonPath $defaultPython 'phone-harness Python executable'
$resolvedTunneldPythonPath = Select-Value $TunneldPythonPath $existing.tunneldPythonPath $defaultTunneldPython 'pymobiledevice3 tunneld Python executable'
$resolvedTunneldPort = if ($PSBoundParameters.ContainsKey('TunneldPort')) { $TunneldPort } elseif ($existing.tunneldPort) { [int]$existing.tunneldPort } else { 49151 }
$resolvedTunnelClientPath = Select-Value $TunnelClientPath $existing.tunnelClientPath $defaultTunnelClient 'tunnel-client executable'
$resolvedTunnelProfilePath = Select-Value $TunnelProfilePath $existing.tunnelProfilePath $defaultTunnelProfile 'tunnel-client profile'
$resolvedControlPlaneApiKeyEnvName = if ($ControlPlaneApiKeyEnvName) {
    $ControlPlaneApiKeyEnvName
} elseif ($existing.controlPlaneApiKeyEnvName) {
    [string]$existing.controlPlaneApiKeyEnvName
} elseif ($env:PHONE_HARNESS_CONTROL_PLANE_API_KEY_ENV_NAME) {
    [string]$env:PHONE_HARNESS_CONTROL_PLANE_API_KEY_ENV_NAME
} else {
    'CONTROL_PLANE_API_KEY'
}
$resolvedTransport = if ($Transport) { $Transport } elseif ($existing.transport) { [string]$existing.transport } else { 'auto' }

if ($resolvedPort -lt 1 -or $resolvedPort -gt 65535) { throw 'Port must be between 1 and 65535.' }
if (-not (Test-Path $resolvedPythonPath -PathType Leaf)) { throw "Python executable not found: $resolvedPythonPath" }
if (-not (Test-Path $resolvedTunneldPythonPath -PathType Leaf)) { throw "tunneld Python executable not found: $resolvedTunneldPythonPath" }
if ($resolvedTunneldPort -lt 1 -or $resolvedTunneldPort -gt 65535) { throw 'TunneldPort must be between 1 and 65535.' }
if (-not (Test-Path $resolvedTunnelClientPath -PathType Leaf)) { throw "tunnel-client executable not found: $resolvedTunnelClientPath" }
if (-not (Test-Path $resolvedTunnelProfilePath -PathType Leaf)) { throw "tunnel-client profile not found: $resolvedTunnelProfilePath" }
if ($resolvedControlPlaneApiKeyEnvName -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
    throw 'Control-plane API key environment variable name must contain only letters, numbers, and underscores and cannot start with a number.'
}

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

if ($PersistControlPlaneApiKey) {
    $controlPlaneApiKey = [Environment]::GetEnvironmentVariable($resolvedControlPlaneApiKeyEnvName, 'Process')
    if (-not $controlPlaneApiKey) {
        $secure = Read-Host "$resolvedControlPlaneApiKeyEnvName (stored as a Windows User environment variable)" -AsSecureString
        $controlPlaneApiKey = [System.Net.NetworkCredential]::new('', $secure).Password.Trim()
    }
    if (-not $controlPlaneApiKey) { throw "$resolvedControlPlaneApiKeyEnvName is empty." }
    [Environment]::SetEnvironmentVariable($resolvedControlPlaneApiKeyEnvName, $controlPlaneApiKey, 'User')
    [Environment]::SetEnvironmentVariable($resolvedControlPlaneApiKeyEnvName, $controlPlaneApiKey, 'Process')
}

[ordered]@{
    publicBaseUrl = ([Uri]$resolvedPublicBaseUrl).AbsoluteUri
    oauthIssuerUrl = ([Uri]$resolvedOAuthIssuerUrl).AbsoluteUri
    oauthResourceUrl = ([Uri]$resolvedOAuthResourceUrl).AbsoluteUri
    port = $resolvedPort
    pythonPath = (Resolve-Path $resolvedPythonPath).Path
    tunneldPythonPath = (Resolve-Path $resolvedTunneldPythonPath).Path
    tunneldPort = $resolvedTunneldPort
    tunnelClientPath = (Resolve-Path $resolvedTunnelClientPath).Path
    tunnelProfilePath = (Resolve-Path $resolvedTunnelProfilePath).Path
    controlPlaneApiKeyEnvName = $resolvedControlPlaneApiKeyEnvName
    transport = $resolvedTransport
} | ConvertTo-Json | Set-Content $configPath -Encoding utf8NoBOM

Write-Host "Saved MCP configuration: $configPath"
Write-Host "Saved encrypted owner token: $secretPath"
if ([Environment]::GetEnvironmentVariable($resolvedControlPlaneApiKeyEnvName, 'User')) {
    Write-Host "Control-plane API key source: User env:$resolvedControlPlaneApiKeyEnvName"
} else {
    Write-Warning "$resolvedControlPlaneApiKeyEnvName is not persisted in the Windows User environment. PC-reboot startup will require it; rerun with -PersistControlPlaneApiKey."
}
Write-Host 'Run tools\start_phone_harness.ps1 for the complete phone-harness stack.'
