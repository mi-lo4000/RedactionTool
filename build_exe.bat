@echo off
setlocal
cd /d "%~dp0"

echo Stopping DedactTool if it is still running...
taskkill /F /IM DedactTool.exe >nul 2>&1
taskkill /F /IM DedactTool_build.exe >nul 2>&1

echo Waiting 2s so Windows releases the file...
timeout /t 2 /nobreak >nul

if exist "dist\DedactTool.exe" (
  attrib -r "dist\DedactTool.exe" >nul 2>&1
  del /f /q "dist\DedactTool.exe" >nul 2>&1
)

if exist "dist\DedactTool_build.exe" (
  attrib -r "dist\DedactTool_build.exe" >nul 2>&1
  del /f /q "dist\DedactTool_build.exe" >nul 2>&1
)

set NAME=DedactTool
if exist "dist\DedactTool.exe" (
  echo.
  echo dist\DedactTool.exe is still locked ^(OneDrive, antivirus, or another app^).
  echo Building as DedactTool_build.exe — rename it when nothing is using the old file.
  echo.
  set NAME=DedactTool_build
)

if not exist ".venv" (
  echo Creating virtual environment...
  py -m venv .venv
)

call .venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing dependencies...
pip install pymupdf pyinstaller

echo Cleaning old PyInstaller artifacts...
if exist build rmdir /s /q build
if exist "%NAME%.spec" del /f /q "%NAME%.spec"

echo Building exe...
pyinstaller --onefile --windowed --name %NAME% --collect-all pymupdf main.py

echo.
if "%NAME%"=="DedactTool" (
  if exist "dist\DedactTool.exe" (
    echo SUCCESS: dist\DedactTool.exe
  ) else (
    echo BUILD FAILED
  )
) else (
  if exist "dist\DedactTool_build.exe" (
    echo SUCCESS: dist\DedactTool_build.exe
  ) else (
    echo BUILD FAILED
  )
)

pause