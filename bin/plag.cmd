@echo off
rem plag: build the dashboard and start the PLAG desktop app. If it's already open, it comes to the front.
setlocal
set "SHELL_DIR=%~dp0..\apps\shell"
set "LOG=%LOCALAPPDATA%\PLAG\logs\launch.log"
if not exist "%LOCALAPPDATA%\PLAG\logs" mkdir "%LOCALAPPDATA%\PLAG\logs"

pushd "%SHELL_DIR%" || (echo PLAG: can't find the app folder at "%SHELL_DIR%" & exit /b 1)
if not exist node_modules (
  echo PLAG: first run, installing the dashboard...
  call npm install >"%LOG%" 2>&1 || (echo PLAG: install failed, see "%LOG%" & popd & exit /b 1)
)
"%~dp0..\core\.venv\Scripts\python.exe" -c "pass" >nul 2>&1 || (echo PLAG: the core's Python can't start. Run "uv sync" in "%~dp0..\core", then try again. & popd & exit /b 1)
echo PLAG: building...
call npm run build >"%LOG%" 2>&1
if errorlevel 1 (
  rem a failed build never replaces the last good one (the type check stops it before anything is written)
  if not exist "dist\index.html" (echo PLAG: build failed, see "%LOG%" & popd & exit /b 1)
  echo PLAG: the new build failed ^(details in "%LOG%"^), so the last working version is starting.
)
start "" "%SHELL_DIR%\node_modules\electron\dist\electron.exe" "%SHELL_DIR%"
popd
echo PLAG is starting. Say "Hey PLAG".

