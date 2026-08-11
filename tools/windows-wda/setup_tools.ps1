param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$IpaSideCommit = "a4ab372"
$ResignerVersion = "v0.3.1"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

Require-Command git
Require-Command node
Require-Command npm
Require-Command py

Push-Location $Root
try {
    npm install --package-lock=false --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }

    if (-not (Test-Path "ipaside-src/.git")) {
        git clone https://github.com/pwnapplehat/iPASide.git ipaside-src
        if ($LASTEXITCODE -ne 0) { throw "iPASide clone failed" }
    }
    git -C ipaside-src fetch origin
    if ($LASTEXITCODE -ne 0) { throw "iPASide fetch failed" }
    git -C ipaside-src checkout --detach $IpaSideCommit
    if ($LASTEXITCODE -ne 0) { throw "iPASide checkout failed" }

    $Engine = Join-Path $Root "ipaside-src/src/iPASide.Engine"
    $EnginePython = Join-Path $Engine ".venv/Scripts/python.exe"
    if (-not (Test-Path $EnginePython)) {
        py -3.12 -m venv (Join-Path $Engine ".venv")
        if ($LASTEXITCODE -ne 0) { throw "iPASide venv creation failed" }
    }
    & $EnginePython -m pip install -U pip
    & $EnginePython -m pip install -r (Join-Path $Engine "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "iPASide dependency install failed" }

    $ResignerDir = Join-Path $Root "bin/resigner/windows-amd64"
    $ResignerExe = Join-Path $ResignerDir "resigner.exe"
    if (-not (Test-Path $ResignerExe)) {
        New-Item -ItemType Directory -Force -Path $ResignerDir | Out-Null
        $Release = Invoke-RestMethod "https://api.github.com/repos/appium/resigner/releases/tags/$ResignerVersion"
        $Asset = $Release.assets | Where-Object { $_.name -match '(?i)windows.*amd64|amd64.*windows' } | Select-Object -First 1
        if (-not $Asset) { throw "No Windows AMD64 resigner asset found in $ResignerVersion" }

        $DownloadDir = Join-Path $Root ".downloads"
        New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
        $AssetPath = Join-Path $DownloadDir $Asset.name
        Invoke-WebRequest $Asset.browser_download_url -OutFile $AssetPath
        if ($AssetPath.EndsWith(".zip", [System.StringComparison]::OrdinalIgnoreCase)) {
            $Expanded = Join-Path $DownloadDir "resigner-expanded"
            Remove-Item -Recurse -Force $Expanded -ErrorAction SilentlyContinue
            Expand-Archive $AssetPath -DestinationPath $Expanded
            $Found = Get-ChildItem $Expanded -Recurse -Filter resigner.exe | Select-Object -First 1
            if (-not $Found) { throw "Downloaded resigner archive did not contain resigner.exe" }
            Copy-Item $Found.FullName $ResignerExe
        } elseif ($AssetPath.EndsWith(".exe", [System.StringComparison]::OrdinalIgnoreCase)) {
            Copy-Item $AssetPath $ResignerExe
        } else {
            throw "Unsupported resigner asset: $($Asset.name)"
        }
    }

    Write-Host "WDA tooling ready."
    Write-Host "Next: sign in locally with iPASide, then run provision_and_sign_wda.py --install."
} finally {
    Pop-Location
}
