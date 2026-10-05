@echo off
rem ============================================================
rem  vrmmon - start WITHOUT administrator rights.
rem  Works, but only GPU / storage temperatures are readable:
rem  CPU and motherboard sensors need admin (see README).
rem
rem  NOTE: keep this file ASCII-only. cmd.exe reads .bat files in
rem  the OEM code page, so UTF-8 Chinese text breaks parsing.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not exist "%PY%" set "PY="
if not defined PY for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
  echo pythonw.exe not found. Install Python and add it to PATH.
  pause
  exit /b 1
)

"%PY%" "%~dp0tools\shortcut_launch.py" --noadmin %*
endlocal
