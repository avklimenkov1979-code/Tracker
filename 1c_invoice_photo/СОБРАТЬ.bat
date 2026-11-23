@echo off
rem ============================================================
rem  Builds the ready-to-use 1C file  Nakladnaya_po_foto.epf
rem  Just double-click this file. 1C:Enterprise 8.3 must be installed.
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "V8="
for %%P in ("%ProgramFiles%\1cv8" "%ProgramFiles(x86)%\1cv8") do (
  if exist "%%~P" (
    for /f "delims=" %%D in ('dir /b /ad /o:n "%%~P\8.3.*" 2^>nul') do (
      if exist "%%~P\%%D\bin\1cv8.exe" set "V8=%%~P\%%D\bin\1cv8.exe"
    )
  )
)
if "%V8%"=="" (
  echo.
  echo  [X] 1C:Enterprise 8.3 not found on this computer.
  echo      Run this file on a computer where 1C is installed.
  echo.
  pause
  exit /b 1
)
echo  1C found: %V8%

set "SRC="
for %%F in ("%~dp0src\*.xml") do set "SRC=%%~fF"
set "OUT=%~dp0Nakladnaya_po_foto.epf"
set "BASE=%TEMP%\nakladnaya_build_base"
set "LOG=%TEMP%\nakladnaya_build.log"

if exist "%BASE%" rmdir /s /q "%BASE%"
if exist "%OUT%" del /q "%OUT%"
if exist "%LOG%" del /q "%LOG%"

echo  Step 1 of 2: preparing a temporary empty base...
start "" /wait "%V8%" CREATEINFOBASE File="%BASE%" /DisableStartupDialogs /Out "%LOG%"

echo  Step 2 of 2: building the processing file...
start "" /wait "%V8%" DESIGNER /F "%BASE%" /DisableStartupDialogs /DisableStartupMessages /LoadExternalDataProcessorOrReportFromFiles "%SRC%" "%OUT%" /Out "%LOG%"

rmdir /s /q "%BASE%" 2>nul

echo.
if exist "%OUT%" (
  echo  ==========================================================
  echo   DONE. File created:
  echo   !OUT!
  echo.
  echo   Open it in 1C:ERP via  File - Open.
  echo  ==========================================================
  explorer /select,"!OUT!"
) else (
  echo  [X] Build failed. Send this log to your 1C specialist:
  echo      !LOG!
  echo.
  type "!LOG!"
)
echo.
pause
