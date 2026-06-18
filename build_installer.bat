@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  CaptionStudio - Build Installer
echo ============================================================
echo.

:: Check PyInstaller is available
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pyinstaller not found. Run: pip install pyinstaller
    exit /b 1
)

:: Step 1: Build with PyInstaller
echo [1/3] Building app bundle with PyInstaller...
echo.
pyinstaller build.spec --clean -y
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)
echo.
echo [1/3] Done - dist\CaptionStudio\CaptionStudio.exe

:: Step 2: Find and run Inno Setup
echo.
echo [2/3] Compiling installer with Inno Setup...

set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set ISCC=C:\Program Files\Inno Setup 6\ISCC.exe

if not defined ISCC (
    echo.
    echo [ERROR] Inno Setup 6 not found.
    echo         Download from: https://jrsoftware.org/isinfo.php
    exit /b 1
)

"%ISCC%" installer\captionsudio.iss
if errorlevel 1 (
    echo.
    echo [ERROR] Inno Setup compilation failed.
    exit /b 1
)

:: Step 3: Done
echo.
echo ============================================================
echo  [3/3] Installer ready!
echo.
echo  Output: dist\installer\CaptionStudio_Setup_1.2.4.exe
echo.
echo  Upload this file to GitHub Releases.
echo ============================================================
echo.

endlocal
