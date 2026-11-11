@echo off
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 goto nonode
if exist update-config.json goto build
echo.
echo To let this copy of My Pics check GitHub for updates, enter your GitHub details.
echo (Press Enter to skip. You can run this file again later.)
set /p GHUSER=Your GitHub username: 
if "%GHUSER%"=="" goto build
set /p GHREPO=Repository name (press Enter for my-pics): 
if "%GHREPO%"=="" set GHREPO=my-pics
> update-config.json echo {"owner":"%GHUSER%","repo":"%GHREPO%"}
:build
call npm install
if errorlevel 1 goto failinstall
call npm run dist
if errorlevel 1 goto failbuild
echo.
echo Done. Your installer is in the dist folder.
start "" "%~dp0dist"
pause
exit /b 0
:nonode
echo Node.js is needed to build My Pics. Install the LTS version from https://nodejs.org and run this file again.
pause
exit /b 1
:failinstall
echo Install failed. Check your internet connection and try again.
pause
exit /b 1
:failbuild
echo Build failed.
pause
exit /b 1
