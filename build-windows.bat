@echo off
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is needed to build My Pics. Install the LTS version from https://nodejs.org and run this file again.
  pause
  exit /b 1
)
call npm install
if errorlevel 1 (
  echo Install failed. Check your internet connection and try again.
  pause
  exit /b 1
)
call npm run dist
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo.
echo Done. Your installer is in the dist folder.
start "" "%~dp0dist"
pause
