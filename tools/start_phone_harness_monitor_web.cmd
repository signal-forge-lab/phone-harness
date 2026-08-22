@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%\src"
set "PYTHON_EXE=%ROOT%\..\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python.exe"
"%PYTHON_EXE%" -m phone_harness.monitor_web --bind 0.0.0.0 --port 17678
endlocal
