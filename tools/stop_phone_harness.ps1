[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$stateDir = Join-Path $env:LOCALAPPDATA 'phone-harness-mcp'
$configPath = Join-Path $stateDir 'config.json'
$config = if (Test-Path $configPath) { Get-Content $configPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonBridgePath = Join-Path $repoRoot 'mcp-server\python_bridge.py'
$tunnelClientPath = [string]$config.tunnelClientPath
$tunnelProfilePath = [string]$config.tunnelProfilePath
$port = if ($config.port) { [int]$config.port } else { 17677 }
$monitorPort = 17678
$tunneldPort = if ($config.tunneldPort) { [int]$config.tunneldPort } else { 49151 }
$localMetadataUrl = "http://127.0.0.1:$port/.well-known/oauth-protected-resource/mcp"
$localResourceUrl = "http://127.0.0.1:$port/mcp"

function Get-CimProcessById {
    param([int]$ProcessId)
    Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

function Test-CommandLineContainsLiteral {
    param(
        [object]$Process,
        [string]$Text
    )
    if (-not $Process -or -not $Process.CommandLine -or -not $Text) { return $false }
    return $Process.CommandLine.IndexOf($Text, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-CommandLineRegex {
    param(
        [object]$Process,
        [string]$Pattern
    )
    if (-not $Process -or -not $Process.CommandLine) { return $false }
    return [regex]::IsMatch($Process.CommandLine, $Pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

function Get-PhoneHarnessAuxiliaryProcesses {
    $matches = @()
    $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') }
    foreach ($candidate in $candidates) {
        $isBridge = Test-CommandLineContainsLiteral $candidate $pythonBridgePath
        $isWda = Test-CommandLineRegex $candidate '(^|\s)-m\s+pymobiledevice3\s+developer\s+wda\s+run-xctrunner\s+com\.iw\.phoneharness\.wda\.'
        $isMonitor = Test-CommandLineRegex $candidate '(^|\s)-m\s+phone_harness\.monitor(?:_web)?(?:\s|$)'
        if ($isBridge -or $isWda -or $isMonitor) {
            $matches += $candidate
        }
    }
    return @($matches)
}

function Stop-PhoneHarnessAuxiliaryProcesses {
    $candidates = @(Get-PhoneHarnessAuxiliaryProcesses)
    if ($candidates.Count -eq 0) {
        Write-Host 'phone-harness Python bridge/WDA/monitor processes already stopped.'
        return
    }

    foreach ($candidate in $candidates) {
        $kind = if (Test-CommandLineContainsLiteral $candidate $pythonBridgePath) {
            'Python bridge'
        } elseif (Test-CommandLineRegex $candidate '(^|\s)-m\s+pymobiledevice3\s+developer\s+wda\s+run-xctrunner\s+com\.iw\.phoneharness\.wda\.') {
            'WDA runner'
        } else {
            'Monitor'
        }
        try {
            Stop-Process -Id $candidate.ProcessId -Force -ErrorAction Stop
            Write-Host "$kind stopped. PID $($candidate.ProcessId)"
        } catch {
            throw "Failed to stop verified phone-harness $kind process PID $($candidate.ProcessId): $($_.Exception.Message)"
        }
    }

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (@(Get-PhoneHarnessAuxiliaryProcesses).Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    }
    $remaining = @(Get-PhoneHarnessAuxiliaryProcesses)
    throw "Verified phone-harness Python bridge/WDA/monitor processes are still running: $($remaining.ProcessId -join ', ')"
}

function Test-McpProcessSignature {
    param([object]$Process)
    if (-not $Process -or $Process.Name -ne 'node.exe') { return $false }
    return Test-CommandLineRegex $Process '(^|[\\/\s])dist[\\/]index\.js(?:\s|$)'
}

function Test-McpListenerProcess {
    param([object]$Process)
    if (-not (Test-McpProcessSignature $Process)) { return $false }
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -eq $Process.ProcessId } |
        Select-Object -First 1
    return $null -ne $listener
}

function Get-McpListenerProcess {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    $process = Get-CimProcessById $listener.OwningProcess
    if (Test-McpListenerProcess $process) { return $process }
    return $null
}

function Stop-ManagedMcp {
    $pidPath = Join-Path $stateDir 'mcp.pid'
    $stopped = $false
    if (Test-Path $pidPath) {
        $rawPid = (Get-Content $pidPath -Raw).Trim()
        if ($rawPid -match '^\d+$') {
            $candidate = Get-CimProcessById ([int]$rawPid)
            if (Test-McpProcessSignature $candidate) {
                Stop-Process -Id $candidate.ProcessId -Force -ErrorAction Stop
                Write-Host "phone-harness MCP stopped. PID $($candidate.ProcessId)"
                $stopped = $true
            } elseif ($candidate) {
                Write-Host 'phone-harness MCP PID file was stale or no longer matched the configured listener; no unrelated process was stopped.'
            }
        }
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
    }

    if (-not $stopped) {
        $listenerProcess = Get-McpListenerProcess
        if ($listenerProcess) {
            Stop-Process -Id $listenerProcess.ProcessId -Force -ErrorAction Stop
            Write-Host "orphaned phone-harness MCP stopped by verified listener signature. PID $($listenerProcess.ProcessId)"
            $stopped = $true
        }
    }

    if (-not $stopped) { Write-Host 'phone-harness MCP already stopped/unmanaged.' }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 250
    }
}

function Test-TunneldProcess {
    param([object]$Process)
    if (-not $Process -or $Process.Name -notin @('python.exe', 'pythonw.exe')) { return $false }
    return Test-CommandLineRegex $Process '(^|\s)-m\s+pymobiledevice3\s+remote\s+tunneld(?:\s|$)'
}

function Test-TunneldEndpointReady {
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:$tunneldPort/" -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

function Get-TunneldListenerProcess {
    $listener = Get-NetTCPConnection -LocalPort $tunneldPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    $process = Get-CimProcessById $listener.OwningProcess
    if (Test-TunneldProcess $process) { return $process }

    # Elevated tunneld processes can expose Name/PID to a non-elevated caller
    # while withholding CommandLine/ExecutablePath. In that case, accept the
    # listener only when it is exactly the PID previously adopted/managed by
    # phone-harness and the local tunneld HTTP endpoint is healthy.
    if (Test-Path $tunneldPidPath) {
        $rawPid = (Get-Content $tunneldPidPath -Raw).Trim()
        if ($rawPid -match '^\d+$' -and [int]$rawPid -eq $listener.OwningProcess -and (Test-TunneldEndpointReady)) {
            return $process
        }
    }
    return $null
}

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

Stop-ManagedMcp
Stop-PhoneHarnessAuxiliaryProcesses

$tunneldPidPath = Join-Path $stateDir 'tunneld.pid'
if (Get-NetTCPConnection -LocalPort $tunneldPort -State Listen -ErrorAction SilentlyContinue) {
    $tunneldPythonPath = [string]$config.tunneldPythonPath
    if (-not $tunneldPythonPath) {
        $tunneldPythonPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Intelligence Works\products\phone-harness-windows\.venv-tunneld\Scripts\python.exe'
    }
    $tunneldProcess = Get-TunneldListenerProcess
    if (-not $tunneldProcess) {
        $owner = (Get-NetTCPConnection -LocalPort $tunneldPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
        throw "Port $tunneldPort is listening, but owner PID $owner does not match a pymobiledevice3 remote tunneld command. Refusing to stop an unrelated process."
    }
    # The start wrapper may have adopted an already-running tunneld, or an old
    # PID file may be stale. Re-bind the managed PID only after command-line and
    # listener verification so the elevated helper stops the actual listener.
    $tunneldProcess.ProcessId | Set-Content $tunneldPidPath -Encoding ascii
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
    Remove-Item $tunneldPidPath -Force -ErrorAction SilentlyContinue
    Write-Host 'pymobiledevice3 tunneld already stopped/unmanaged.'
}

$monitorStatePath = Join-Path $env:TEMP 'phone-harness\monitor\server.json'
Remove-Item $monitorStatePath -Force -ErrorAction SilentlyContinue

$verificationErrors = @()
$remainingAuxiliary = @(Get-PhoneHarnessAuxiliaryProcesses)
if ($remainingAuxiliary.Count -gt 0) {
    $verificationErrors += "phone-harness Python bridge/WDA/monitor PIDs remain: $($remainingAuxiliary.ProcessId -join ', ')"
}
$remainingConfiguredTunnel = @(Get-ConfiguredTunnelProcesses)
if ($remainingConfiguredTunnel.Count -gt 0) {
    $verificationErrors += "Secure MCP Tunnel PIDs remain: $($remainingConfiguredTunnel.ProcessId -join ', ')"
}
foreach ($checkedPort in @($port, $monitorPort, $tunneldPort)) {
    $listeners = @(Get-NetTCPConnection -LocalPort $checkedPort -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        $verificationErrors += "TCP $checkedPort is still listening (PID $($listeners.OwningProcess -join ', '))"
    }
}
foreach ($pidFile in @('mcp.pid', 'tunnel-client.pid', 'tunneld.pid')) {
    $path = Join-Path $stateDir $pidFile
    if (Test-Path $path) {
        $verificationErrors += "stale managed PID file remains: $pidFile"
    }
}

if ($verificationErrors.Count -gt 0) {
    throw "phone-harness stop verification failed:`n - $($verificationErrors -join "`n - ")"
}

Write-Host 'STOPPED: phone-harness runtime fully stopped; MCP/Secure Tunnel/tunneld/WDA/bridge/monitor processes and managed listeners are clear.'
