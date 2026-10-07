# The Windows .exe given to users (package.json build.nsis.script): Private Agent, unpacked once per build.
#
# electron-builder's portable exe unpacked the whole app (about 950 MB: Electron, Playwright's Chromium and the Python
# backend) into %TEMP% at every launch, then copied it once more, and deleted it when the app ended: the dashboard took
# 73-89 seconds on a fast PC (2026-10-02, i9 + NVMe), against 2 seconds for the same app already unpacked. This exe
# keeps the unpacked app in %LOCALAPPDATA%\Private Agent\app-<version>-<build time>: the first launch of a build
# unpacks it there (a notice shows the progress), and every later launch starts it at once, once the folder still holds
# all of the build. Nothing is installed: no registry entry, shortcut or uninstaller. A build older than this one has
# its folder removed once no copy of the app runs; a newer one keeps it for its own exe. The build time in the folder's
# name tells which is newer, so the version need not change from one build to the next.

!include "common.nsh"
!include "LogicLib.nsh"
!include "WinMessages.nsh"
!include "FileFunc.nsh"
!include "nsProcess.nsh"

!ifndef APP_64
  !error "The launcher needs electron-builder's 7z package of the x64 app (nsis target, x64, useZip off)."
!endif

!ifdef MUI_ICON
  Icon "${MUI_ICON}" # The app's icon; electron-builder hands it to installer scripts under this name.
!endif
Caption "${PRODUCT_NAME}" # The title of its messages, instead of NSIS's "… Setup".
CRCCheck off # A CRC check would read all of this file (about 250 MB) at every launch.
WindowIcon off
AutoCloseWindow true
SilentInstall silent
RequestExecutionLevel user
ManifestDPIAware true

!define /date BUILD_TIME "%Y%m%d%H%M%S"
!define BUILD "app-${VERSION}-${BUILD_TIME}"
!define BACKEND_EXECUTABLE "private-agent-backend.exe"

Var root # %LOCALAPPDATA%\Private Agent
Var app # This build's folder in it.
Var others # 1 while a copy of the app runs, or when that could not be told: other builds' folders stay.
Var banner # The progress notice's text, while unpacking.
Var shown # The percentage it shows.

# 1 in $0 when the folder in $0 holds this build's app: the files the app cannot start without, and all of the build's
# size. A file removed since it was unpacked (by antivirus, say) leaves the folder smaller, so the app is unpacked
# again; files the app wrote there only make it larger. This reads the files' sizes, not what they hold.
!macro isComplete
  ${If} ${FileExists} "$0\${APP_EXECUTABLE_FILENAME}"
  ${AndIf} ${FileExists} "$0\resources\app.asar"
  ${AndIf} ${FileExists} "$0\resources\backend\private-agent-backend\${BACKEND_EXECUTABLE}"
    Call folderKiB
    ${If} $0 < ${APP_64_UNPACKED_SIZE}
      StrCpy $0 0
    ${Else}
      StrCpy $0 1
    ${EndIf}
  ${Else}
    StrCpy $0 0
  ${EndIf}
!macroend

Function fail # The message on the stack, shown before the launcher ends.
  Exch $0
  Banner::destroy
  MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST|MB_SETFOREGROUND $0 # In front, though other windows took the focus while it unpacked.
  SetErrorLevel 1
  Quit
FunctionEnd

Function progress # Nsis7z's callback: bytes unpacked, then the total.
  Exch $0
  Exch
  Exch $1
  Push $2
  ${If} $1 != 0
    System::Int64Op $0 * 100
    Pop $2
    System::Int64Op $2 / $1
    Pop $2
    ${If} $2 != $shown
      StrCpy $shown $2
      SendMessage $banner ${WM_SETTEXT} 0 "STR:Private Agent 첫 실행 준비 중… $2%"
    ${EndIf}
  ${EndIf}
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

# The size of the files under the folder in $0, in KiB rounded up as electron-builder counts APP_64_UNPACKED_SIZE.
Function folderKiB
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  StrCpy $8 0
  System::Alloc 36 # WIN32_FILE_ATTRIBUTE_DATA
  Pop $7
  Push "|" # Below the folders still to read.
  Push $0
  next_folder:
    Pop $1
    StrCmp $1 "|" counted
    ClearErrors
    FindFirst $2 $3 "$1\*"
    entry:
      IfErrors listed
      StrCmp $3 "." skip
      StrCmp $3 ".." skip
      System::Call 'kernel32::GetFileAttributesExW(w "$1\$3", i 0, p r7) i .r4'
      StrCmp $4 0 skip # Unreadable: left out, so the size cannot match.
      System::Call '*$7(i .r4, l, l, l, i .r5, i .r6)'
      IntOp $4 $4 & 0x10 # FILE_ATTRIBUTE_DIRECTORY
      ${If} $4 <> 0
        Push "$1\$3"
      ${Else}
        System::Int64Op $5 * 4294967296
        Pop $5
        System::Int64Op $6 & 0xFFFFFFFF
        Pop $6
        System::Int64Op $5 + $6
        Pop $5
        System::Int64Op $8 + $5
        Pop $8
      ${EndIf}
    skip:
      ClearErrors
      FindNext $2 $3
      Goto entry
    listed:
      FindClose $2
      Goto next_folder
  counted:
  System::Free $7
  System::Int64Op $8 + 1023
  Pop $8
  System::Int64Op $8 / 1024
  Pop $0
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
FunctionEnd

# Renames the folder $0 to $1, trying again for about ten seconds: antivirus scanning the files just unpacked holds
# them for a while, and Windows refuses to rename a folder while a file in it is held. The error flag is set when it
# never worked.
Function renameFolder
  Push $2
  StrCpy $2 1
  ${Do}
    ClearErrors
    Rename $0 $1
    ${IfNot} ${Errors}
      StrCpy $2 0
      ${ExitDo}
    ${EndIf}
    ${If} $2 >= 20
      ${ExitDo}
    ${EndIf}
    IntOp $2 $2 + 1
    Sleep 500
  ${Loop}
  ${If} $2 <> 0
    SetErrors
  ${EndIf}
  Pop $2
FunctionEnd

Function unpack
  # The app with the 7z package still beside it needs about 1.3 times its size.
  ${GetRoot} $root $0
  ClearErrors
  ${DriveSpace} "$0\" "/D=F /S=M" $1
  ${IfNot} ${Errors}
    IntOp $2 ${APP_64_UNPACKED_SIZE} / 1024
    IntOp $2 $2 * 13
    IntOp $2 $2 / 10
    ${If} $1 < $2
      Push "Private Agent를 처음 실행하려면 $0 드라이브에 여유 공간이 약 $2MB 필요합니다(현재 $1MB). 공간을 확보한 뒤 다시 실행해 주세요."
      Call fail
    ${EndIf}
  ${EndIf}

  Banner::show "Private Agent 첫 실행 준비 중…"
  Banner::getWindow
  Pop $0
  GetDlgItem $banner $0 1030
  StrCpy $shown ""

  # Left by a launch that was ended while unpacking; launches run one at a time (see the mutex), so none uses it now.
  RMDir /r "$root\unpacking"
  ClearErrors
  CreateDirectory "$root\unpacking"
  ${If} ${Errors}
    Push "Private Agent 파일을 풀 폴더($root)를 만들지 못했습니다. 폴더 권한을 확인해 주세요."
    Call fail
  ${EndIf}
  InitPluginsDir
  !ifdef COMPRESS
    SetCompress off # The 7z package is compressed already.
  !endif
  File /oname=$PLUGINSDIR\app.7z "${APP_64}"
  !ifdef COMPRESS
    SetCompress "${COMPRESS}"
  !endif
  SetOutPath "$root\unpacking"
  GetFunctionAddress $0 progress
  Nsis7z::ExtractWithCallback "$PLUGINSDIR\app.7z" $0
  Delete "$PLUGINSDIR\app.7z"
  # Nsis7z reports success even when a file could not be written (disk full, a file held or removed by antivirus), so
  # the unpacked size must be the app's.
  SetOutPath $root # The folder this process is in cannot be renamed.
  StrCpy $0 "$root\unpacking"
  Call folderKiB
  ${If} $0 != ${APP_64_UNPACKED_SIZE}
    RMDir /r "$root\unpacking"
    Push "Private Agent 파일을 모두 풀지 못했습니다. 디스크 여유 공간과 백신의 검역(차단) 기록을 확인한 뒤 다시 실행해 주세요."
    Call fail
  ${EndIf}

  # A damaged copy of this build (a file removed by antivirus, say) is replaced.
  ${If} ${FileExists} "$app\*.*"
    RMDir /r "$root\delete-${BUILD}"
    StrCpy $0 $app
    StrCpy $1 "$root\delete-${BUILD}"
    Call renameFolder
    ${If} ${Errors}
      RMDir /r "$root\unpacking"
      Push "이전에 실행한 Private Agent가 아직 끝나지 않아 앱 파일을 고치지 못했습니다. Private Agent를 모두 닫은 뒤 다시 실행해 주세요."
      Call fail
    ${EndIf}
    RMDir /r "$root\delete-${BUILD}"
  ${EndIf}
  StrCpy $0 "$root\unpacking"
  StrCpy $1 $app
  Call renameFolder
  ${If} ${Errors}
    RMDir /r "$root\unpacking"
    Push "Private Agent 파일을 $app 폴더로 옮기지 못했습니다. 폴더 권한과 백신 기록을 확인해 주세요."
    Call fail
  ${EndIf}
  Banner::destroy
FunctionEnd

# Older builds' folders, which no copy of the app runs from once none runs. A folder that cannot be renamed is in use
# (a process started in it, an open file) and stays for a later launch. A newer build's folder stays for its own exe:
# running an older exe must not cost the newer one its unpacking. The last 14 characters of an app-<version>-<build
# time> folder are its build time (yyyymmddHHMMSS), so comparing them as text tells which build is older.
Function removeOtherBuilds
  ClearErrors
  FindFirst $1 $2 "$root\*"
  ${DoUntil} ${Errors}
    ${If} $2 != "."
    ${AndIf} $2 != ".."
    ${AndIf} $2 != "${BUILD}"
    ${AndIf} ${FileExists} "$root\$2\*.*"
      StrCpy $3 $2 7
      ${If} $3 == "delete-"
        RMDir /r "$root\$2"
      ${Else}
        StrCpy $3 $2 4
        StrCpy $4 $2 "" -14
        ${If} $3 == "app-"
        ${AndIf} $4 S< "${BUILD_TIME}"
          RMDir /r "$root\delete-$2"
          ClearErrors
          Rename "$root\$2" "$root\delete-$2"
          ${IfNot} ${Errors}
            RMDir /r "$root\delete-$2"
          ${EndIf}
        ${EndIf}
      ${EndIf}
    ${EndIf}
    ClearErrors
    FindNext $1 $2
  ${Loop}
  FindClose $1
FunctionEnd

Section
  StrCpy $root "$LOCALAPPDATA\${PRODUCT_FILENAME}"
  StrCpy $app "$root\${BUILD}"

  # One launch at a time: a second double-click while the first launch unpacks waits for it, then starts the app.
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\${APP_GUID}-launcher") p .r9'
  System::Call 'kernel32::WaitForSingleObject(p r9, i -1) i .r0'

  # nsProcess answers 0 when it finds the process, 603 when none runs.
  StrCpy $others 1
  ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $0
  ${If} $0 = 603
    ${nsProcess::FindProcess} "${BACKEND_EXECUTABLE}" $0
    ${If} $0 = 603
      StrCpy $others 0
    ${EndIf}
  ${EndIf}
  ${nsProcess::Unload}

  # Every launch: the files of this build are all there still (their sizes, about 700 files).
  StrCpy $0 $app
  !insertmacro isComplete
  ${If} $0 != 1
    Call unpack
  ${EndIf}

  # The app runs in its folder, which keeps that folder from being renamed, so removed, by a later launch.
  ${StdUtils.GetAllParameters} $R0 0
  SetOutPath $app
  ClearErrors
  Exec '"$app\${APP_EXECUTABLE_FILENAME}" $R0'
  ${If} ${Errors}
    Push "Private Agent($app\${APP_EXECUTABLE_FILENAME})를 실행하지 못했습니다. 백신의 차단 기록을 확인해 주세요."
    Call fail
  ${EndIf}

  ${If} $others = 0
    Call removeOtherBuilds
  ${EndIf}
  System::Call 'kernel32::ReleaseMutex(p r9)'
  System::Call 'kernel32::CloseHandle(p r9)'
SectionEnd
