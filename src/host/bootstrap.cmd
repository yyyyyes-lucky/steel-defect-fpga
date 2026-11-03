@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 "%~dp0environment.py" %*
exit /b %errorlevel%
:try_python
where python >nul 2>&1
if errorlevel 1 goto missing_python
python "%~dp0environment.py" %*
exit /b %errorlevel%
:missing_python
echo Python was not found. Install Python 3.12 64-bit with Python Launcher, reopen PowerShell, and retry.
exit /b 2
