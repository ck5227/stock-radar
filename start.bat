@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\pan\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python"
start "" http://127.0.0.1:5057
"%PY%" app.py
