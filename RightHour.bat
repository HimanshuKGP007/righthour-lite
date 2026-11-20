@echo off
REM Double-click this file. First run installs what it needs (about 2 minutes), then opens the app in your browser.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is not installed. Install it from https://www.python.org/downloads/ and tick "Add Python to PATH", then double-click this file again.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo First run: setting things up. This takes about 2 minutes, once.
  python -m venv .venv
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -r requirements.txt
  if errorlevel 1 ( echo Install failed. Check your internet and try again. & pause & exit /b 1 )
)
echo Starting RightHour Lite at http://localhost:8501 . Close this window to stop it.
start "" /b cmd /c "timeout /t 4 >nul & start http://localhost:8501"
.venv\Scripts\python -m streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false
