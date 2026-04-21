@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo Prvni spusteni - instaluji zavislosti...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

streamlit run app_quality.py
pause
