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

echo [1/3] Installing PyTorch (CPU version)...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo ERROR: PyTorch install failed.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing remaining dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency install failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Pre-downloading the default Whisper model (small, ~244 MB)...
python -c "import whisper, os; from pathlib import Path; cache=Path('models','whisper'); cache.mkdir(parents=True,exist_ok=True); whisper.load_model('small', download_root=str(cache)); print('Model ready.')"

echo.
echo ============================================================
echo  Setup complete! Run the app with:  python main.py
echo ============================================================
pause
