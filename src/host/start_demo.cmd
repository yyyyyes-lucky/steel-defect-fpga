@echo off
setlocal
if "%~1"=="--check-environment" goto check_only
call "%~dp0bootstrap.cmd" --run %*
set "STEEL_EXIT=%errorlevel%"
if not "%STEEL_EXIT%"=="0" pause
exit /b %STEEL_EXIT%
:check_only
call "%~dp0bootstrap.cmd" --check
exit /b %errorlevel%
