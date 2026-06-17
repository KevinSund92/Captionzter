@echo off
if not exist .venv (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
.venv\Scripts\python main.py
