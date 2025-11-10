@echo off
echo ========================================
echo   Running PyQt5 Application
echo ========================================

REM Check if venv exists
IF NOT EXIST venv (
    echo Virtual environment not found! Run setup_env.bat first.
    pause
    exit /b
)

REM Activate environment
call venv\Scripts\activate

REM Optional: check if your script exists
IF NOT EXIST Pcap_Testbench.py (
    echo Pcap_Testbench.py not found!
    call deactivate
    pause
    exit /b
)

REM Run your script
python Pcap_Testbench.py

REM Deactivate environment automatically
call deactivate

echo.
echo ========================================
echo   Environment deactivated.
echo   Run setup_env.bat again only if packages change.
echo ========================================
pause
