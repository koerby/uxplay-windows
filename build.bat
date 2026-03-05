@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo uxplay-windows build helper
echo ==========================================

if not exist "uxplay.ico" (
  echo ERROR: uxplay.ico not found in repo root.
  exit /b 1
)

set "APP_VERSION=%~1"
if "%APP_VERSION%"=="" set /p APP_VERSION=Enter app version ^(example: 1.0.0^): 
if "%APP_VERSION%"=="" (
  echo ERROR: Version is required.
  exit /b 1
)

>"version.txt" echo %APP_VERSION%
echo Wrote version.txt with %APP_VERSION%

set "RUNTIME_SOURCE=%~2"
if "%RUNTIME_SOURCE%"=="" set /p RUNTIME_SOURCE=Runtime _internal path ^(optional, for uxplay.exe + libs^): 
if /I "%RUNTIME_SOURCE%"=="auto" set "RUNTIME_SOURCE="

if "%RUNTIME_SOURCE%"=="" (
  echo No runtime path entered. Trying auto-detect...
  for %%P in (
    "%ProgramFiles(x86)%\uxplay-windows\_internal"
    "%ProgramFiles%\uxplay-windows\_internal"
    "%cd%\runtime\_internal"
  ) do (
    if exist "%%~P\bin\uxplay.exe" (
      set "RUNTIME_SOURCE=%%~P"
      goto :runtime_detected
    )
  )
)

:runtime_detected
if not "%RUNTIME_SOURCE%"=="" (
  echo Using runtime source: %RUNTIME_SOURCE%
) else (
  echo WARNING: Runtime source not found automatically. Build continues without runtime.
)

set "BUILD_INSTALLER=%~3"
if "%BUILD_INSTALLER%"=="" set /p BUILD_INSTALLER=Build installer too? ^(y/n^): 
if /I not "%BUILD_INSTALLER%"=="y" if /I not "%BUILD_INSTALLER%"=="n" (
  echo ERROR: Please answer y or n.
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: python not found in PATH.
  exit /b 1
)

echo Stopping running uxplay processes...
taskkill /F /IM uxplay-windows.exe >nul 2>nul
taskkill /F /IM uxplay.exe >nul 2>nul

echo Installing Python build dependencies...
python -m pip install --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install pyinstaller pystray pillow
if errorlevel 1 exit /b 1

echo Cleaning old artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Output rmdir /s /q Output
if exist uxplay-windows.spec del /q uxplay-windows.spec

echo Building tray executable...
python -m PyInstaller -y --noconsole --onedir --clean --name uxplay-windows --icon=uxplay.ico tray.py
if errorlevel 1 exit /b 1

if not exist "dist\uxplay-windows\_internal" mkdir "dist\uxplay-windows\_internal"
copy /y "version.txt" "dist\uxplay-windows\_internal\version.txt" >nul
copy /y "uxplay.ico" "dist\uxplay-windows\_internal\uxplay.ico" >nul

if not "%RUNTIME_SOURCE%"=="" (
  if exist "%RUNTIME_SOURCE%\bin\uxplay.exe" (
    echo Copying runtime payload from %RUNTIME_SOURCE%
    xcopy "%RUNTIME_SOURCE%\bin" "dist\uxplay-windows\_internal\bin\" /E /I /Y >nul
    xcopy "%RUNTIME_SOURCE%\lib" "dist\uxplay-windows\_internal\lib\" /E /I /Y >nul
    if exist "%RUNTIME_SOURCE%\uxplay.ico" (
      copy /y "%RUNTIME_SOURCE%\uxplay.ico" "dist\uxplay-windows\_internal\uxplay.ico" >nul
    ) else (
      echo WARNING: %RUNTIME_SOURCE%\uxplay.ico not found. App icon in _internal may be missing.
    )
  ) else (
    echo WARNING: Runtime source does not contain bin\uxplay.exe. Skipping runtime copy.
  )
) else (
  echo WARNING: No runtime source provided. AirPlay runtime not included.
)

if /I "%BUILD_INSTALLER%"=="y" (
  if not exist "dist\uxplay-windows\_internal\bin\uxplay.exe" (
    echo ERROR: Installer build requires uxplay runtime, but dist\uxplay-windows\_internal\bin\uxplay.exe is missing.
    echo Provide a valid runtime path when running build.bat, e.g.:
    echo build.bat %APP_VERSION% "C:\Program Files ^(x86^ )\uxplay-windows\_internal" y
    echo NOTE: FDH2/UxPlay GitHub releases currently do not provide Windows runtime binaries ^(only uxplay.spec^).
    exit /b 1
  )

  set "ISCC="
  for /f "delims=" %%I in ('where iscc.exe 2^>nul') do (
    if not defined ISCC set "ISCC=%%~fI"
  )
  if not defined ISCC if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
  if not defined ISCC if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
  if not defined ISCC (
    echo ERROR: ISCC.exe not found. Install Inno Setup 6.
    exit /b 1
  )

  echo Updating MyAppVersion in script.iss...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='script.iss'; $v='%APP_VERSION%'; $lines=Get-Content $p; for($i=0; $i -lt $lines.Count; $i++){ if($lines[$i] -like '#define MyAppVersion *'){ $lines[$i] = '#define MyAppVersion ' + [char]34 + $v + [char]34 } }; Set-Content -Path $p -Value $lines -Encoding UTF8"
  if errorlevel 1 exit /b 1

  echo Building installer with Inno Setup...
  "!ISCC!" "script.iss"
  if errorlevel 1 exit /b 1
)

echo.
echo Build complete.
echo Portable output: dist\uxplay-windows\uxplay-windows.exe
if /I "%BUILD_INSTALLER%"=="y" echo Installer output: Output\uxplay-win_setup_v%APP_VERSION%.exe

echo.
echo Optional firewall rule ^(run as Admin^):
echo netsh advfirewall firewall add rule name="uxplay-windows" dir=in action=allow program="%cd%\dist\uxplay-windows\_internal\bin\uxplay.exe" enable=yes profile=any

exit /b 0
