"""Provision, sign, and optionally install WebDriverAgent on Windows."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
IPASIDE_ENGINE = ROOT / "ipaside-src" / "src" / "iPASide.Engine"
if not IPASIDE_ENGINE.exists():
    raise SystemExit("iPASide is not set up; run setup_tools.ps1 first")
sys.path.insert(0, str(IPASIDE_ENGINE))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs12  # noqa: E402
from ipaside_engine import device, provision  # noqa: E402


def run_sign_wda(args: list[str], env: dict[str, str]) -> None:
    script = ROOT / "node_modules" / "appium-xcuitest-driver" / "scripts" / "sign-wda.mjs"
    proc = subprocess.run(
        ["node", str(script), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode:
        raise SystemExit(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true", help="install the signed WDA on the connected iPhone")
    args = parser.parse_args()

    udid = device.resolve_serial(None)
    bundle_id = provision.team_scoped_bundle_id("com.iw.phoneharness.wda")
    print("Provisioning one free-development App ID/profile for WDA...")
    bundle = provision.ensure_signing_assets(
        bundle_id,
        udid,
        app_name="IW Phone Harness WDA",
        device_name="Phone Harness iPhone",
    )

    unsigned = ROOT / "node_modules" / "appium-xcuitest-driver" / "wda-real" / "WebDriverAgentRunner-Runner.app"
    if not unsigned.exists():
        raise SystemExit("Appium WDA is missing; run setup_tools.ps1 first")

    signed = ROOT / "signed-wda" / "WebDriverAgentRunner-Runner.app"
    if signed.parent.exists():
        shutil.rmtree(signed.parent)
    signed.parent.mkdir(parents=True)
    shutil.copytree(unsigned, signed)

    profile_dir = ROOT / "profiles"
    if profile_dir.exists():
        shutil.rmtree(profile_dir)
    profile_dir.mkdir()
    shutil.copy2(bundle["profile_path"], profile_dir / "wda.mobileprovision")

    resigner_dir = ROOT / "bin" / "resigner" / "windows-amd64"
    if not (resigner_dir / "resigner.exe").exists():
        raise SystemExit("resigner.exe is missing; run setup_tools.ps1 first")
    env = os.environ.copy()
    env["PATH"] = str(resigner_dir) + os.pathsep + env.get("PATH", "")
    env["P12_PASSWORD"] = bundle["p12_password"]

    password = bundle["p12_password"].encode("utf-8")
    key, cert, cas = pkcs12.load_key_and_certificates(Path(bundle["p12_path"]).read_bytes(), password)
    if key is None or cert is None:
        raise RuntimeError("signing identity is missing its private key or certificate")
    compatible_encryption = (
        serialization.PrivateFormat.PKCS12.encryption_builder()
        .kdf_rounds(50_000)
        .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
        .hmac_hash(hashes.SHA1())
        .build(password)
    )

    print("Signing WDA on Windows...")
    with tempfile.TemporaryDirectory(prefix="phone-harness-wda-") as temp_dir:
        compatible_p12 = Path(temp_dir) / "identity-compatible.p12"
        compatible_p12.write_bytes(
            pkcs12.serialize_key_and_certificates(
                b"IW Phone Harness WDA", key, cert, cas, compatible_encryption
            )
        )
        run_sign_wda(
            [
                f"--wda-path={signed}",
                f"--p12-file={compatible_p12}",
                f"--profile-dir={profile_dir}",
                f"--bundle-id={bundle_id}",
            ],
            env,
        )

    print("Inspecting signed WDA...")
    run_sign_wda([f"--wda-path={signed}", "--inspect"], env)
    if args.install:
        print("Installing signed WDA...")
        subprocess.run(
            [sys.executable, "-m", "pymobiledevice3", "apps", "install", "--developer", str(signed)],
            check=True,
        )
    print("SIGNED_WDA_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
