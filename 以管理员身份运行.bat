@echo off
rem ============================================================
rem  vrmmon - start WITH administrator rights.
rem  Required for CPU / motherboard sensors (MSR + port I/O are
rem  ring0 operations; Windows denies them to normal processes).
rem
rem  NOTE: this file must stay ASCII-only. cmd.exe reads .bat
rem  files in the OEM code page, so UTF-8 Chinese text inside a
rem  .bat gets mis-decoded and breaks batch parsing.
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

"%PY%" "%~dp0tools\shortcut_launch.py" %*
endlocal
