@echo off
setlocal
if "%~1"=="--check-environment" goto check_only
call "%~dp0bootstrap.cmd" --install %*
set "STEEL_EXIT=%errorlevel%"
if not "%STEEL_EXIT%"=="0" echo Installation did not complete. Read the explanation above before retrying.
pause
exit /b %STEEL_EXIT%
:check_only
call "%~dp0bootstrap.cmd" --check
exit /b %errorlevel%
