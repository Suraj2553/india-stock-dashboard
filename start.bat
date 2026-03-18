@echo off
setlocal EnableDelayedExpansion
set ROOT=%~dp0
set VENV=%ROOT%.venv
set BACKEND=%ROOT%backend
set SCRIPTS=%ROOT%scripts
set IMPORTS=%ROOT%imports

echo.
echo   +------------------------------------------+
echo   ^|   MARKET MONITOR  v2.0                  ^|
echo   ^|   Real-time ^| Charts ^| AI Analysis      ^|
echo   +------------------------------------------+
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (echo [ERROR] Python not found & pause & exit /b 1)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER%

if not exist "%VENV%\Scripts\python.exe" (
    echo [.] Creating virtual environment...
    python -m venv "%VENV%"
    if %errorlevel% neq 0 (echo [ERROR] venv failed & pause & exit /b 1)
)

echo [.] Checking dependencies...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%VENV%\Scripts\pip.exe" install fastapi "uvicorn[standard]" httpx "pydantic>=2.11.0" --quiet
if %errorlevel% neq 0 (echo [ERROR] pip install failed & pause & exit /b 1)
echo [OK] Dependencies ready

if not exist "%ROOT%.env" (echo [ERROR] .env file missing & pause & exit /b 1)
findstr /C:"your_github_pat_here" "%ROOT%.env" >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo [!] GITHUB_TOKEN not set in .env - AI chat will not work
    echo     Edit .env and paste your GitHub Personal Access Token
    echo.
)

if not exist "%IMPORTS%" mkdir "%IMPORTS%"
set CSV_COUNT=0
for %%f in ("%IMPORTS%\*.csv" "%IMPORTS%\*.CSV") do set /a CSV_COUNT+=1
if %CSV_COUNT% GTR 0 (
    echo [.] Found CSV files in imports/ - importing...
    "%VENV%\Scripts\python.exe" "%SCRIPTS%\parse_groww.py" --auto
    echo.
)

echo.
echo   Dashboard  -^> http://localhost:8080
echo   Portfolio  -^> http://localhost:8080  (Portfolio tab)
echo   Charts     -^> http://localhost:8080  (Charts tab)
echo   AI Chat    -^> http://localhost:8080  (AI Chat tab)
echo.
echo   Prices update every 5 seconds automatically.
echo   Press Ctrl+C to stop.
echo.

cd "%BACKEND%"
"%VENV%\Scripts\python.exe" main.py
pause
