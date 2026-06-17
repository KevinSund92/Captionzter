@echo off
echo ============================================================
echo  CaptionStudio — First-time setup
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

echo [1/4] Creating virtual environment...
if exist .venv (
    echo   .venv already exists, skipping.
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo.
echo [2/4] Installing PyTorch (CPU version)...
.venv\Scripts\pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo ERROR: PyTorch install failed.
    pause
    exit /b 1
)

echo.
echo [3/4] Installing remaining dependencies...
.venv\Scripts\pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency install failed.
    pause
    exit /b 1
)

echo.
echo [4/4] Pre-downloading the default Whisper model (small, ~244 MB)...
.venv\Scripts\python -c "import whisper; from pathlib import Path; cache=Path('models','whisper'); cache.mkdir(parents=True,exist_ok=True); whisper.load_model('small', download_root=str(cache)); print('Model ready.')"

echo.
echo ============================================================
echo  Setup complete! Run the app with:  run.bat
echo ============================================================
pause
