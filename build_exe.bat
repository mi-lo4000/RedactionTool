@echo off
setlocal
cd /d "%~dp0"

echo Stopping DedactTool if it is still running...
taskkill /F /IM DedactTool.exe >nul 2>&1
echo Waiting 2s so Windows releases the file...
timeout /t 2 /nobreak >nul

if exist "dist\DedactTool.exe" (
  attrib -r "dist\DedactTool.exe" >nul 2>&1
  del /f /q "dist\DedactTool.exe" >nul 2>&1
)

set NAME=DedactTool
if exist "dist\DedactTool.exe" (
  echo.
  echo dist\DedactTool.exe is still locked ^(OneDrive, antivirus, or another app^).
  echo Building as DedactTool_build.exe — rename it when nothing is using the old file.
  echo.
  set NAME=DedactTool_build
)

where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
  uv sync --group dev
  uv run pyinstaller --onefile --windowed --name %NAME% --collect-all pymupdf main.py
) else (
  if exist ".venv\Scripts\activate.bat" call .venv\Scripts\activate.bat
  pyinstaller --onefile --windowed --name %NAME% --collect-all pymupdf main.py
)

echo.
if "%NAME%"=="DedactTool" (
  echo If successful: dist\DedactTool.exe
) else (
  echo If successful: dist\DedactTool_build.exe
)
pause
