@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo Python was not found.
  echo Install 64-bit Python 3.11 or newer from https://www.python.org/downloads/
  echo Select "Add Python to PATH" during installation, then run Setup.cmd again.
  pause
  exit /b 1
)

echo Creating the local Python environment...
py -3 -m venv .venv
if errorlevel 1 goto :failed

echo Installing the transcription and document packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Setup is complete. Drag an MP4 onto "Create Transcript.cmd".
pause
exit /b 0

:failed
echo.
echo Setup did not complete. Review the error above.
pause
exit /b 1

