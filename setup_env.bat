@echo off
echo ========================================
echo   Setting up Python Virtual Environment
echo ========================================

REM Create venv if it doesn't exist
IF NOT EXIST venv (
    echo Creating virtual environment...
    python -m venv venv
) ELSE (
    echo Virtual environment already exists.
)

REM Activate and install dependencies
call venv\Scripts\activate
echo Installing required packages...
pip install --upgrade pip
pip install PyQt5 matplotlib

echo.
echo ========================================
echo   Setup complete!
echo   To run your test, use: run_test.bat
echo ========================================
pause
