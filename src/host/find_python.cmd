@echo off
set "DEMO_PYTHON="
for %%P in ("%~dp0.venv\Scripts\python.exe" "%~dp0..\.venv-train\Scripts\python.exe" "%~dp0..\..\.venv-train\Scripts\python.exe" "%~dp0..\..\..\.venv-train\Scripts\python.exe") do (
  if exist "%%~fP" (
    "%%~fP" -c "import torch, torchvision, ultralytics, cv2, requests, numpy" >nul 2>&1
    if not errorlevel 1 (
      set "DEMO_PYTHON=%%~fP"
      exit /b 0
    )
  )
)
exit /b 1
