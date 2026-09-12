# phone-harness インストール

[English](install.md) | [日本語](install.ja.md)

この文書には、再利用可能な公開セットアップ手順だけを記載します。端末識別子、資格情報、Tunnel profile、内部サービスURL、実PC固有path、障害復旧handoffはローカルまたはprivateな資料として管理し、この公開リポジトリへコミットしません。

日常的なエージェント利用では `SKILL.md` も参照してください。

## Windows 要件

- Windows 11
- 通常の phone-harness runtime 用 Python 3.12
- Apple Devices とApple USB/device driver層
- USB/Wi-Fi CoreDevice、RSD、HID、WDA transport用 `pymobiledevice3`
- OCR fallbackを利用する場合は PaddlePaddle CPU + PaddleOCR

project環境を作成します。

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.venv\Scripts\python.exe -m pip install "git+https://github.com/signal-forge-lab/pymobiledevice3.git@master"
.venv\Scripts\python.exe -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv\Scripts\python.exe -m pip install paddleocr==3.7.0
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pip check
.venv\Scripts\phone-harness.exe --doctor
```

最初にUSB discoveryを確認します。

```bat
.venv\Scripts\pymobiledevice3.exe usbmux list
```

端末が見えない場合はPythonやOCR環境を変更する前に、Apple device driver／Trust層を修復してください。iOSがTrustやDeveloper Modeの物理承認を要求した場合はユーザーが端末上で承認し、自動化で回避しません。

## Windowsの任意Wi-Fi transport

初期セットアップはUSBを安全なbootstrap経路とします。Trust・pairing・Developer Mode・必要なautomation backendのprovisioningが完了した後は、Wi-Fi RemotePairing tunnelを利用できます。

`pymobiledevice3`側で新しいPython runtimeが必要な場合は、通常runtimeとは別の環境を使用します。

```powershell
$pymobiledevice3 = "C:\path\to\pymobiledevice3"
py -3.14 -m venv .venv-tunneld
.\.venv-tunneld\Scripts\python.exe -m pip install -U pip
.\.venv-tunneld\Scripts\python.exe -m pip install -e $pymobiledevice3
.\.venv-tunneld\Scripts\python.exe -m pymobiledevice3 remote tunneld
```

phone-harnessを動かすprocess側でtransportを指定します。

```powershell
$env:PHONE_HARNESS_TRANSPORT = "wifi"   # usb | wifi | auto
# 複数端末が存在する場合だけ任意指定:
$env:PHONE_HARNESS_UDID = "YOUR_DEVICE_UDID"
.\.venv\Scripts\phone-harness.exe --doctor
```

実UDIDやpairing/runtime stateはコミットしないでください。

## Windows iOS WDA setup

生成tool、profile、certificate、署名済みapplication、download物はローカルartifactであり、Git管理外です。

repository rootからtoolingを準備します。

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows-wda\setup_tools.ps1
```

Apple sign-inや端末承認はローカルで実施します。Apple password、2FA code、署名鍵、provisioning profile、端末IDを公開source、公開Issue、共有用script引数へ含めないでください。

ローカル署名環境の準備後、付属helperでWDAをprovision/sign/installします。

```powershell
.\tools\windows-wda\ipaside-src\src\iPASide.Engine\.venv\Scripts\python.exe `
  .\tools\windows-wda\provision_and_sign_wda.py --install
```

local development profileの期限切れやrunner再署名が必要になった場合は、provisioningを再実行します。

## MCP server

MCP serverは `mcp-server/` にあります。公開sourceにはtransport／認証実装を含めますが、実資格情報や環境固有Tunnel設定は含めません。

ローカル設定と起動にはrepository wrapperを使用します。

```powershell
.\tools\configure_phone_harness_mcp.ps1
.\tools\start_phone_harness_mcp.ps1
```

環境固有値を要求された場合は、自分のローカル設定を使用します。Owner token、control-plane/API key、OAuth credential、Tunnel resource ID、private URLはGit外で管理してください。Secret実値はchecked-in設定ではなく、外部Secret storeまたはprocess-local environmentから渡します。

全体のlifecycle helperも利用できます。

```powershell
.\tools\start_phone_harness.ps1
.\tools\stop_phone_harness.ps1
```

環境固有のtroubleshooting／recovery記録は公開リポジトリ外に保持します。

## macOS 要件

- iPhone Mirroringがpair済みのmacOS
- Python 3.12+
- runtimeが利用するPyObjC component
- phone-harnessを動かすterminal/appへのAccessibilityとScreen Recording権限

必要componentと本repositoryをインストールします。

```bash
git clone https://github.com/signal-forge-lab/phone-harness ~/.phone-harness
cd ~/.phone-harness
pip install pyobjc-framework-Quartz pyobjc-framework-Vision pyobjc-framework-AppKit
pip install -e . --no-deps
phone-harness --doctor
```

macOSが追加permissionを要求した場合はSystem Settingsから承認します。物理端末での承認はユーザー操作です。

## agent skillとして登録

インストール済みcommandからskill本文を生成し、利用するagent hostのskill directoryへ配置します。

```bash
mkdir -p ~/.claude/skills/phone-harness
phone-harness skill > ~/.claude/skills/phone-harness/SKILL.md
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/phone-harness"
phone-harness skill > "${CODEX_HOME:-$HOME/.codex}/skills/phone-harness/SKILL.md"
```

phone-harness更新後は登録済みskillも再生成してください。

## セットアップ失敗時

`phone-harness --doctor` を使い、上位層を調べる前に最初に失敗しているdependency／permission境界を修正してください。障害logやPC固有の復旧記録は、再利用可能な公開Issue／documentへ十分にsanitizeしない限りローカルで保持します。
