@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Run Setup.cmd once before creating a transcript.
  pause
  exit /b 1
)

if "%~1"=="" (
  set /p "LECTURE_VIDEO=Paste the full path to the MP4: "
) else (
  set "LECTURE_VIDEO=%~1"
)

if not exist "%LECTURE_VIDEO%" (
  echo The video was not found:
  echo %LECTURE_VIDEO%
  pause
  exit /b 1
)

for %%F in ("%LECTURE_VIDEO%") do (
  set "OUTPUT_DOC=%%~dpnF Transcript and Slides.docx"
)

set "TOOL_WORK=%LOCALAPPDATA%\LectureTranscriptTool"
if not exist "%TOOL_WORK%" mkdir "%TOOL_WORK%"

echo.
echo Detecting slides and transcribing the lecture locally...
echo The first run also downloads the Whisper model.
echo.

".venv\Scripts\python.exe" lecture_to_docx.py "%LECTURE_VIDEO%" --output "%OUTPUT_DOC%" --work-dir "%TOOL_WORK%"
if errorlevel 1 (
  echo.
  echo The document was not created. Review the error above.
  pause
  exit /b 1
)

echo.
echo Finished:
echo %OUTPUT_DOC%
pause

