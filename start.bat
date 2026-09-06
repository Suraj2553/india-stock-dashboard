@echo off
setlocal EnableDelayedExpansion
set ROOT=%~dp0
set VENV=%ROOT%.venv
set BACKEND=%ROOT%backend
set SCRIPTS=%ROOT%scripts
set IMPORTS=%ROOT%imports

echo.
echo   +------------------------------------------+
echo   ^|   MARKET MONITOR  v4.0                  ^|
echo   ^|   Engine v4 ^| Buy Ideas ^| E-mail scans  ^|
echo   +------------------------------------------+
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python from https://python.org
    pause & exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER%

:: Check if venv exists but is broken (e.g. after a system format / Python reinstall)
if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! neq 0 (
        echo [!] Virtual environment is broken ^(Python was reinstalled^). Recreating...
        rmdir /s /q "%VENV%"
    )
)

if not exist "%VENV%\Scripts\python.exe" (
    echo [.] Creating virtual environment...
    python -m venv "%VENV%"
    if %errorlevel% neq 0 (echo [ERROR] venv creation failed & pause & exit /b 1)
    echo [OK] Virtual environment created
)

echo [.] Checking dependencies...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%VENV%\Scripts\python.exe" -m pip install fastapi "uvicorn[standard]" httpx "pydantic>=2.11.0" tradingview-screener --quiet
if %errorlevel% neq 0 (echo [ERROR] pip install failed & pause & exit /b 1)
echo [OK] Dependencies ready

if not exist "%ROOT%.env" (copy "%ROOT%.env.example" "%ROOT%.env" >nul & echo [OK] created .env from .env.example)
if not exist "%ROOT%data\holdings.json" (copy "%ROOT%data\holdings.sample.json" "%ROOT%data\holdings.json" >nul & echo [OK] created data\holdings.json from sample - add your holdings in Settings)
if not exist "%ROOT%.mcp.json" if exist "%ROOT%.mcp.template.json" (
    powershell -NoProfile -Command "$j=(Get-Content '%ROOT%.mcp.template.json' -Raw) -replace '__TRADINGVIEW_MCP__', ('%ROOT%.venv\Scripts\tradingview-mcp.exe' -replace '\\','/'); [IO.File]::WriteAllText('%ROOT%.mcp.json', $j, (New-Object System.Text.UTF8Encoding $false))" >nul 2>&1
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
echo   Prices update every 5s in market hours. Scheduled scans + e-mail run while this window is open.
echo   Press Ctrl+C to stop.
echo.

cd "%BACKEND%"
"%VENV%\Scripts\python.exe" main.py
pause
