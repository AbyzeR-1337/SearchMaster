@echo off
rem SearchMaster for Windows: double-click, type what to search, the report opens in the browser.
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Python not found. Install it from https://www.python.org/downloads/ & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: устанавливаю зависимости...
  python -m venv .venv || (pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (pause & exit /b 1)
)
set "Q=%*"
if "%Q%"=="" set /p "Q=Что искать: "
if "%Q%"=="" exit /b 0
".venv\Scripts\python.exe" -m searchmaster --open %SM_ARGS% -- "%Q%"
pause
