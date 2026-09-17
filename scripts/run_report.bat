@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
rem Kullanim: run_report.bat            -> bu ayin raporu
rem          run_report.bat --month 2026-08
rem          run_report.bat --all       -> tum aylar
"%PY%" -m ig_snapshot report %*
