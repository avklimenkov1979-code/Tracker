@echo off
chcp 65001 >nul
rem ============================================================
rem  Builds Nakladnaya_po_foto.epf next to this file.
rem  Works from network folders too (Desktop on a server, \\server\share).
rem ============================================================
setlocal enabledelayedexpansion

rem pushd maps a temporary drive letter when the folder is on a network share.
pushd "%~dp0"
if errorlevel 1 (
  echo.
  echo  [X] Не удалось открыть папку со скриптом: %~dp0
  echo      Скопируйте папку Nakladnaya_po_foto на диск C: и запустите скрипт оттуда.
  echo.
  pause
  exit /b 1
)
set "HERE=%CD%"

set "V8="
for %%P in ("%ProgramFiles%\1cv8" "%ProgramFiles(x86)%\1cv8") do (
  if exist "%%~P" (
    for /f "delims=" %%D in ('dir /b /ad /o:n "%%~P\8.3.*" 2^>nul') do (
      if exist "%%~P\%%D\bin\1cv8.exe" set "V8=%%~P\%%D\bin\1cv8.exe"
    )
  )
)
if not defined V8 (
  echo.
  echo  [X] На этом компьютере не найдена 1С:Предприятие 8.3.
  echo      Запустите скрипт на компьютере, где установлена 1С.
  echo.
  pause
  exit /b 1
)
echo  Найдена 1С: %V8%

rem Work on the local disk: 1C and cmd are unreliable with network paths.
set "WORK=%TEMP%\nakladnaya_build"
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%"
xcopy "%HERE%\src" "%WORK%\src\" /E /I /Q /Y >nul
if errorlevel 1 (
  echo.
  echo  [X] Не найдена папка src рядом со скриптом. Распакуйте архив целиком.
  echo.
  pause
  exit /b 1
)

set "SRC="
for %%F in ("%WORK%\src\*.xml") do set "SRC=%%~fF"
if not defined SRC (
  echo.
  echo  [X] В папке src нет файла описания обработки ^(.xml^). Распакуйте архив целиком.
  echo.
  pause
  exit /b 1
)

set "BASE=%WORK%\base"
set "OUT=%WORK%\Nakladnaya_po_foto.epf"
set "LOG1=%WORK%\step1.log"
set "LOG2=%WORK%\step2.log"

echo  Шаг 1 из 2: создаю временную пустую базу...
start "" /wait "%V8%" CREATEINFOBASE File="%BASE%" /DisableStartupDialogs /Out "%LOG1%"
if not exist "%BASE%\1Cv8.1CD" (
  echo.
  echo  [X] Не удалось создать временную базу. Сообщение 1С:
  echo  ----------------------------------------------------------
  if exist "%LOG1%" type "%LOG1%"
  echo.
  echo  ----------------------------------------------------------
  echo  Сфотографируйте этот экран и пришлите.
  echo.
  pause
  exit /b 1
)

echo  Шаг 2 из 2: собираю программу...
start "" /wait "%V8%" DESIGNER /F "%BASE%" /DisableStartupDialogs /DisableStartupMessages /LoadExternalDataProcessorOrReportFromFiles "%SRC%" "%OUT%" /Out "%LOG2%"
if not exist "%OUT%" (
  echo.
  echo  [X] Сборка не удалась. Сообщение 1С:
  echo  ----------------------------------------------------------
  if exist "%LOG2%" type "%LOG2%"
  echo.
  echo  ----------------------------------------------------------
  echo  Сфотографируйте этот экран и пришлите.
  echo.
  pause
  exit /b 1
)

set "RESULT=%HERE%\Nakladnaya_po_foto.epf"
copy /Y "%OUT%" "%RESULT%" >nul
if errorlevel 1 (
  rem Could not write next to the script - leave it in Documents.
  set "RESULT=%USERPROFILE%\Documents\Nakladnaya_po_foto.epf"
  copy /Y "%OUT%" "!RESULT!" >nul
)
rmdir /s /q "%WORK%" 2>nul

echo.
echo  ==========================================================
echo   ГОТОВО. Программа создана:
echo   !RESULT!
echo.
echo   Откройте её в 1С:ERP через  Файл - Открыть.
echo  ==========================================================
explorer /select,"!RESULT!"
popd
echo.
pause
