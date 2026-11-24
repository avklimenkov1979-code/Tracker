@echo off
chcp 65001 >nul
rem ============================================================
rem  Builds Otchet_rukovoditelyu.epf next to this file.
rem  Works from network folders too (Desktop on a server, \\server\share).
rem ============================================================
setlocal enabledelayedexpansion

rem pushd maps a temporary drive letter when the folder is on a network share.
pushd "%~dp0"
if errorlevel 1 (
  echo.
  echo  [X] Не удалось открыть папку со скриптом: %~dp0
  echo      Скопируйте папку Otchet_rukovoditelyu на диск C: и запустите скрипт оттуда.
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
set "WORK=%TEMP%\otchet_rukovoditelyu_build"
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
set "OUT=%WORK%\Otchet_rukovoditelyu.epf"
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
if exist "%OUT%" goto built

rem ---- Plan B: build through the user's own 1C base (license comes from the 1C server) ----
echo.
echo  Временной базе не хватило лицензии 1С - так бывает, когда лицензию выдаёт сервер.
echo  Соберу через вашу рабочую базу 1С. В самой базе НИЧЕГО не меняется,
echo  она нужна только чтобы 1С выдала лицензию.
echo.

set "LIST=%WORK%\bases.txt"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$f=Join-Path $env:APPDATA '1C\1CEStart\ibases.v8i'; $out=@(); if(Test-Path $f){ $n=''; foreach($l in Get-Content -Encoding UTF8 $f){ if($l -match '^\[(.+)\]$'){$n=$Matches[1]} elseif($l -match '^Connect=(.+)$'){ $out += ($n + '|' + $Matches[1]) } } }; [IO.File]::WriteAllLines('%LIST%', [string[]]$out)"

set /a N=0
for /f "usebackq tokens=1* delims=|" %%a in ("%LIST%") do (
  set /a N+=1
  set "NAME!N!=%%a"
  set "CONN!N!=%%b"
  echo   !N!. %%a
)
if %N%==0 (
  echo  [X] Не нашёл список баз 1С на этом компьютере.
  goto specialist
)
echo.
set "CHOICE="
set /p "CHOICE=  Введите номер вашей рабочей базы и нажмите Enter: "
if not defined CONN%CHOICE% (
  echo  [X] Нет базы с таким номером.
  goto specialist
)
set "CONN=!CONN%CHOICE%!"
rem 1C needs quotes inside the connection string doubled.
set "CONN=!CONN:"=""!"
echo.
echo  Подключаюсь к базе "!NAME%CHOICE%!".
echo  Если 1С спросит пользователя и пароль - введите те же, что для обычной работы.
echo.
start "" /wait "%V8%" DESIGNER /IBConnectionString "!CONN!" /DisableStartupMessages /LoadExternalDataProcessorOrReportFromFiles "%SRC%" "%OUT%" /Out "%LOG2%"
if exist "%OUT%" goto built

:specialist
echo.
echo  [X] Собрать не удалось. Сообщение 1С:
echo  ----------------------------------------------------------
if exist "%LOG2%" type "%LOG2%"
echo.
echo  ----------------------------------------------------------
echo  Если написано про права или конфигуратор - у вашей учётной записи нет доступа
echo  к режиму "Конфигуратор". Тогда передайте папку Otchet_rukovoditelyu специалисту 1С:
echo  ему нужно открыть Конфигуратор, выбрать "Файл - Загрузить внешнюю обработку,
echo  отчет из файлов", указать файл src\ОтчетРуководителю.xml и сохранить
echo  результат как Otchet_rukovoditelyu.epf. Это займёт 2 минуты.
echo.
echo  Или сфотографируйте этот экран и пришлите.
echo.
popd
pause
exit /b 1

:built
set "RESULT=%HERE%\Otchet_rukovoditelyu.epf"
copy /Y "%OUT%" "%RESULT%" >nul
if errorlevel 1 (
  rem Could not write next to the script - leave it in Documents.
  set "RESULT=%USERPROFILE%\Documents\Otchet_rukovoditelyu.epf"
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
