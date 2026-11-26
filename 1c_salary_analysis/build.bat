@echo off
chcp 65001 > nul
rem Сборка АнализЗарплатыСотрудников.epf из XML-исходников.
rem
rem Использование:
rem   build.bat "C:\Program Files\1cv8\8.3.25.1374\bin\1cv8.exe" "D:\Bases\ZUP_copy"
rem   build.bat "...\1cv8.exe" "Srvr=server1c;Ref=zup_copy" Администратор Пароль
rem
rem Второй параметр — каталог файловой базы или строка подключения к серверной.
rem Берите копию рабочей базы ЗУП/БП: в форме есть тип СправочникСсылка.Организации,
rem и в пустой базе загрузка его не найдет.

setlocal
set V8=%~1
set IB=%~2
set USR=%~3
set PWD=%~4
if "%V8%"=="" goto usage
if "%IB%"=="" goto usage

set ROOT=%~dp0
set SRC=%ROOT%src\АнализЗарплатыСотрудников.xml
set OUT=%ROOT%АнализЗарплатыСотрудников.epf
set LOG=%ROOT%build.log

echo %IB% | find "=" > nul
if errorlevel 1 (set CONN=/F"%IB%") else (set CONN=/IBConnectionString "%IB%")

set AUTH=
if not "%USR%"=="" set AUTH=/N"%USR%" /P"%PWD%"

"%V8%" DESIGNER %CONN% %AUTH% /DisableStartupDialogs /DisableStartupMessages /LoadExternalDataProcessorOrReportFromFiles "%SRC%" "%OUT%" /Out "%LOG%"
type "%LOG%"
if exist "%OUT%" (echo Готово: %OUT%) else (echo Сборка не удалась, см. %LOG% & exit /b 1)
exit /b 0

:usage
echo build.bat "путь\к\1cv8.exe" "каталог_базы или строка подключения" [пользователь] [пароль]
exit /b 2
