@echo off
setlocal
pushd "%~dp0" || exit /b 1

where node.exe >nul 2>&1
if errorlevel 1 goto missing_tools
where npm.cmd >nul 2>&1
if errorlevel 1 goto missing_tools

call npm.cmd run dev:ready
set "result=%errorlevel%"
if not "%result%"=="0" pause
popd
exit /b %result%

:missing_tools
echo Node.js 22.12+ and npm are required. Install them and reopen this script.
pause
popd
exit /b 1
