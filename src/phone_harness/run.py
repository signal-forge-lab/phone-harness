"""The phone-harness CLI: exec Python with helpers pre-imported."""
import sys
from pathlib import Path

USAGE = """Usage:
  phone-harness -c "print(screen_info())"
  phone-harness < script.py

Unix shells also support:
  phone-harness <<'PY'
  print(screen_info())
  PY

Commands:
  phone-harness --doctor    diagnose permissions, app, and session state
  phone-harness skill       print the phone-harness skill text
"""


def _exec_code(code):
    if not code.strip():
        sys.exit(USAGE)
    from . import helpers
    g = {k: v for k, v in vars(helpers).items() if not k.startswith("_")}
    g["__name__"] = "__main__"
    exec(code, g)


def _configure_stdio():
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def main():
    _configure_stdio()
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help"}:
        print(USAGE)
        return
    if args and args[0] in {"--doctor", "doctor"}:
        from .admin import run_doctor
        sys.exit(run_doctor())
    if args and args[0] == "skill":
        repo_root = Path(__file__).resolve().parent.parent.parent
        # Write bytes so Windows consoles configured for cp932 cannot corrupt
        # or reject the UTF-8 skill text when it is redirected to SKILL.md.
        sys.stdout.buffer.write((repo_root / "SKILL.md").read_bytes())
        return
    if args and args[0] in {"-c", "--command"}:
        if len(args) != 2:
            sys.exit(USAGE)
        _exec_code(args[1])
        return
    if args or sys.stdin.isatty():
        sys.exit(USAGE)
    _exec_code(sys.stdin.read())


if __name__ == "__main__":
    main()
