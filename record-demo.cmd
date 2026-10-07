@echo off
setlocal EnableExtensions
rem Records a demo video of Private Agent on a Windows PC, with the app's exe alone (no Node.js or Python):
rem the app opens a demo Wiki of its own and empties it, imports every file, folder and link of a list through
rem its own screens while its window is recorded, and makes the video with captions.
rem
rem   record-demo.cmd [list file]                Record. The list defaults to demo-list.txt beside this script;
rem                                              one that does not exist yet is created to fill in.
rem   record-demo.cmd render [folder or list]    Make a recording's video again after editing its captions
rem                                              (the newest recording of the list when no folder is given).
rem
rem The app is looked for beside this script (Private-Agent-*.exe, or Private Agent.exe), then in
rem win-unpacked of a build, then where the exe unpacked it (%LOCALAPPDATA%\Private Agent).
rem Set PRIVATE_AGENT_EXE to use another one.

set "HERE=%~dp0"
set "MODE=record"
if /i "%~1"=="render" (
  set "MODE=render"
  shift
)
set "TARGET=%~1"
if not defined TARGET set "TARGET=%HERE%demo-list.txt"
for %%I in ("%TARGET%") do set "TARGET=%%~fI"

call :find_app
if not defined APP goto no_app
if "%MODE%"=="render" goto render

tasklist /fi "imagename eq Private Agent.exe" /nh 2>nul | find /i "Private Agent.exe" >nul
if not errorlevel 1 goto running

set "NEW_LIST="
if not exist "%TARGET%" set "NEW_LIST=1"
echo Private Agent demo recording
echo   list: %TARGET%
echo   app:  %APP%
echo.
echo The app opens in full screen and works through the list by itself. Leave the PC alone until it
echo ends. The Stop button of the small recording panel ends it early and still makes the video.
start "" /wait "%APP%" --demo "%TARGET%"
call :pause 3
tasklist /fi "imagename eq Private Agent.exe" /nh 2>nul | find /i "Private Agent.exe" >nul
if errorlevel 1 goto ended
echo.
echo Recording... this window waits until the app closes.
:wait_app
call :pause 5
tasklist /fi "imagename eq Private Agent.exe" /nh 2>nul | find /i "Private Agent.exe" >nul
if not errorlevel 1 goto wait_app
:ended
echo.
if defined NEW_LIST goto new_list
echo The app has closed. Explorer shows the video, its captions (.srt) and the report of the run;
echo a message from the app said so if something went wrong.
echo To edit the captions and make the video again: record-demo.cmd render
pause
exit /b 0

:new_list
echo The list did not exist yet: the app created it and opened it in Notepad.
echo Write the files, folders and links to import in it, one per line, save it,
echo and run record-demo.cmd again.
pause
exit /b 0

:render
echo Making the video again with the captions as they are now:
echo   %TARGET%
echo A window shows the video as it is made; Explorer opens when it is done.
start "" "%APP%" --demo-render "%TARGET%"
exit /b 0

:find_app
set "APP="
if defined PRIVATE_AGENT_EXE if exist "%PRIVATE_AGENT_EXE%" set "APP=%PRIVATE_AGENT_EXE%"
if defined APP exit /b 0
for /f "delims=" %%F in ('dir /b /a:-d /o:n "%HERE%Private-Agent-*.exe" 2^>nul') do set "APP=%HERE%%%F"
if defined APP exit /b 0
if exist "%HERE%Private Agent.exe" set "APP=%HERE%Private Agent.exe"
if defined APP exit /b 0
if exist "%HERE%win-unpacked\Private Agent.exe" set "APP=%HERE%win-unpacked\Private Agent.exe"
if defined APP exit /b 0
if exist "%HERE%release\win-unpacked\Private Agent.exe" set "APP=%HERE%release\win-unpacked\Private Agent.exe"
if defined APP exit /b 0
for /f "delims=" %%D in ('dir /b /a:d /o:n "%LOCALAPPDATA%\Private Agent\app-*" 2^>nul') do if exist "%LOCALAPPDATA%\Private Agent\%%D\Private Agent.exe" set "APP=%LOCALAPPDATA%\Private Agent\%%D\Private Agent.exe"
exit /b 0

:pause
rem Waits %1 seconds; timeout.exe refuses to run when the input is redirected.
ping -n %~1 127.0.0.1 >nul 2>&1
exit /b 0

:no_app
echo Private Agent was not found. Put this script beside Private-Agent-*.exe,
echo or set PRIVATE_AGENT_EXE to the app's exe.
pause
exit /b 1

:running
echo Private Agent is open. Close it first: a demo runs the app on a Wiki of its own.
pause
exit /b 1
