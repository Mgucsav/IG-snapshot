@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist logs mkdir logs
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
echo [%date% %time%] snapshot basliyor >> logs\task.log
"%PY%" -m ig_snapshot snapshot >> logs\task.log 2>&1
echo [%date% %time%] bitti, cikis kodu %ERRORLEVEL% >> logs\task.log
