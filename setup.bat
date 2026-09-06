@echo off
:: Market Monitor - one-click setup for Windows. Double-click this file.
:: Installs Python, Node.js, Ollama + local AI model, dependencies and config.
:: Extra options:  setup.bat -Scheduler     (also register twice-daily scans)
::                 setup.bat -SkipOllama    (no local AI model)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
