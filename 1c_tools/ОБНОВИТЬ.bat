@echo off
chcp 65001 >nul
rem ==================================================================
rem  ОБНОВИТЬ.bat - ставит и обновляет обработки 1С одним щелчком.
rem
rem  Первый запуск: копирует себя в %LOCALAPPDATA%\Obrabotki1C, спрашивает
rem  рабочую базу и создаёт на рабочем столе ярлыки:
rem    "Обновить обработки 1С", "Ежедневный отчёт", "Накладная по фото",
rem    "Сервис учёта", "ГОЗ затраты ПП 874".
rem  Дальше: сохраните архив из письма в "Загрузки" и щёлкните ярлык
rem  "Обновить обработки 1С" - он сам найдёт свежий архив, распакует,
rem  соберёт .epf и обновит ярлыки. Архив можно и перетащить на ярлык.
rem
rem  Вся логика - в PowerShell ниже метки PS-START.
rem ==================================================================
set "OBN_SELF=%~f0"
set "OBN_ARG=%~1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:OBN_SELF,[Text.Encoding]::UTF8); $i=$t.IndexOf('#'+'PS-START'); Invoke-Expression $t.Substring($i)" & exit /b

#PS-START
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$HomeDir   = Join-Path $env:LOCALAPPDATA 'Obrabotki1C'
$EpfDir    = Join-Path $HomeDir 'epf'
$Installed = Join-Path $HomeDir 'ОБНОВИТЬ.bat'
$SettingsFile = Join-Path $HomeDir 'nastroyki.txt'
$Work      = Join-Path $env:TEMP 'obrabotki1c_build'
$Desktop   = [Environment]::GetFolderPath('Desktop')

# Что умеем собирать: имя описания в src\*.xml -> название и имя файла .epf
$Products = @{
    'ОтчетРуководителю'       = @{ Title = 'Ежедневный отчёт';   Epf = 'Otchet_rukovoditelyu.epf' }
    'ЗагрузкаНакладнойПоФото' = @{ Title = 'Накладная по фото';  Epf = 'Nakladnaya_po_foto.epf' }
    'СервисУчета'             = @{ Title = 'Сервис учёта';       Epf = 'Servis_ucheta.epf' }
    'ЗатратыГОЗ874'           = @{ Title = 'ГОЗ затраты ПП 874'; Epf = 'GOZ_zatraty_874.epf' }
}
$ArchivePatterns = @('Obrabotki_1C*.zip', 'Otchet_rukovoditelyu*.zip', 'Nakladnaya_po_foto*.zip')

function Say($text, $color = 'Gray') { Write-Host "  $text" -ForegroundColor $color }
function Ok($text)   { Say $text 'Green' }
function Warn($text) { Say $text 'Yellow' }
function Fail($text) { Say "[X] $text" 'Red' }
# В файлы для чата не попадают имя пользователя Windows, компьютер, путь к профилю, адрес базы и длинные номера
function Mask-Private([string]$t) {
    if (-not $t) { return $t }
    foreach ($v in @($env:USERPROFILE, $env:USERNAME, $env:COMPUTERNAME, $env:USERDOMAIN, $env:LOGONSERVER)) {
        if ($v -and $v.Trim('\').Length -ge 3) { $t = $t.Replace($v.Trim('\'), '[скрыто]') }
    }
    if ($settings -and $settings['Base']) {
        foreach ($part in ($settings['Base'] -split ';')) {
            $val = ($part -split '=', 2)[1]
            if ($val) { $val = $val.Trim().Trim('"'); if ($val.Length -ge 3) { $t = $t.Replace($val, '[база]') } }
        }
    }
    return [regex]::Replace($t, '\d{9,}', { param($m) '*' * $m.Value.Length })
}

function Finish($code) {
    Write-Host ''
    Read-Host '  Нажмите Enter, чтобы закрыть окно' | Out-Null
    exit $code
}

function Load-Settings {
    $s = @{}
    if (Test-Path $SettingsFile) {
        foreach ($line in [IO.File]::ReadAllLines($SettingsFile, [Text.Encoding]::UTF8)) {
            $p = $line.IndexOf('=')
            if ($p -gt 0) { $s[$line.Substring(0, $p)] = $line.Substring($p + 1) }
        }
    }
    return $s
}

function Save-Settings($s) {
    $lines = @()
    foreach ($k in ($s.Keys | Sort-Object)) { $lines += "$k=$($s[$k])" }
    [IO.File]::WriteAllLines($SettingsFile, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
}

function Find-1C {
    $found = @()
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        $dir = Join-Path $root '1cv8'
        if (-not (Test-Path $dir)) { continue }
        foreach ($d in Get-ChildItem $dir -Directory -Filter '8.3.*' -ErrorAction SilentlyContinue) {
            $exe = Join-Path $d.FullName 'bin\1cv8.exe'
            if (Test-Path $exe) {
                $ver = $null
                if (-not [version]::TryParse($d.Name, [ref]$ver)) { $ver = [version]'0.0' }
                $found += [pscustomobject]@{ Version = $ver; Bin = (Join-Path $d.FullName 'bin') }
            }
        }
    }
    if ($found.Count -eq 0) { return $null }
    return ($found | Sort-Object Version -Descending | Select-Object -First 1)
}

function Read-Bases {
    $f = Join-Path $env:APPDATA '1C\1CEStart\ibases.v8i'
    $list = @()
    if (Test-Path $f) {
        $name = ''
        foreach ($l in [IO.File]::ReadAllLines($f, [Text.Encoding]::UTF8)) {
            if ($l -match '^\[(.+)\]\s*$') { $name = $Matches[1] }
            elseif ($l -match '^Connect=(.+)$') { $list += [pscustomobject]@{ Name = $name; Conn = $Matches[1].Trim() } }
        }
    }
    return $list
}

function Choose-Base($settings) {
    if ($settings['Base']) { return }
    $bases = @(Read-Bases)
    if ($bases.Count -eq 0) {
        Warn 'Не нашёл список баз 1С на этом компьютере. Ярлыки для запуска не создам, только файлы .epf.'
        return
    }
    if ($bases.Count -eq 1) {
        $pick = $bases[0]
    } else {
        Write-Host ''
        Say 'Выберите вашу рабочую базу 1С (спрошу один раз и запомню):' 'White'
        for ($n = 0; $n -lt $bases.Count; $n++) { Say ("{0}. {1}" -f ($n + 1), $bases[$n].Name) 'White' }
        $pick = $null
        while (-not $pick) {
            $answer = Read-Host '  Номер базы'
            $num = 0
            if ([int]::TryParse($answer, [ref]$num) -and $num -ge 1 -and $num -le $bases.Count) { $pick = $bases[$num - 1] }
            else { Warn 'Нет базы с таким номером, введите ещё раз.' }
        }
    }
    $settings['Base'] = $pick.Conn
    $settings['BaseName'] = $pick.Name
    Ok "База: $($pick.Name)"
}

function Quote-Conn($conn) { return '"' + ($conn -replace '"', '""') + '"' }

function Run-1C($exe, $arguments, $timeoutSec) {
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -PassThru
    if (-not $p.WaitForExit($timeoutSec * 1000)) {
        try { $p.Kill() } catch { }
        return $false
    }
    return $true
}

function Read-Log($path) {
    if (-not (Test-Path $path)) { return '' }
    $bytes = [IO.File]::ReadAllBytes($path)
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if ($text.IndexOf([char]0xFFFD) -ge 0) { $text = [Text.Encoding]::GetEncoding(1251).GetString($bytes) }
    return $text.Trim()
}

function Build-Epf($v8, $xml, $epf, $settings) {
    $log = Join-Path $Work 'build.log'
    if (Test-Path $epf) { Remove-Item $epf -Force }
    $load = "/LoadExternalDataProcessorOrReportFromFiles `"$xml`" `"$epf`" /Out `"$log`""

    # Способ 1: временная пустая база - не трогает рабочую, но нужна локальная лицензия.
    if ($settings['Mode'] -ne 'base') {
        $tmpBase = Join-Path $HomeDir 'pustaya_baza'
        if (-not (Test-Path (Join-Path $tmpBase '1Cv8.1CD'))) {
            Run-1C $v8 "CREATEINFOBASE File=`"$tmpBase`" /DisableStartupDialogs /Out `"$log`"" 300 | Out-Null
        }
        if (Test-Path (Join-Path $tmpBase '1Cv8.1CD')) {
            Run-1C $v8 "DESIGNER /F `"$tmpBase`" /DisableStartupDialogs /DisableStartupMessages $load" 600 | Out-Null
            if (Test-Path $epf) { $settings['Mode'] = 'temp'; return $true }
        }
        if (-not $settings['Base']) { return $false }
        Say 'Временной базе не хватило лицензии - собираю через рабочую базу (в ней ничего не меняется).'
    }

    # Способ 2: рабочая база в режиме Конфигуратора (лицензию выдаёт сервер 1С).
    if (-not $settings['Base']) { return $false }
    Say 'Если 1С спросит пользователя и пароль - введите те же, что для обычной работы.'
    Run-1C $v8 "DESIGNER /IBConnectionString $(Quote-Conn $settings['Base']) /DisableStartupMessages $load" 900 | Out-Null
    if (Test-Path $epf) { $settings['Mode'] = 'base'; return $true }
    return $false
}

function Hash-Folder($dir) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $sb = New-Object Text.StringBuilder
    foreach ($f in (Get-ChildItem $dir -Recurse -File | Sort-Object FullName)) {
        $rel = $f.FullName.Substring($dir.Length)
        $h = [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($f.FullName)))
        [void]$sb.Append("$rel=$h;")
    }
    return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sb.ToString()))).Replace('-', '')
}

function Make-Shortcut($path, $target, $arguments, $icon, $description) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($path)
    $lnk.TargetPath = $target
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = Split-Path $target
    if ($icon) { $lnk.IconLocation = $icon }
    $lnk.Description = $description
    $lnk.Save()
}

function Expand-Zip($zip, $dest) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    # Имена без признака UTF-8 - в кодировке 866, как их пишет Проводник и наши архивы.
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest, [Text.Encoding]::GetEncoding(866))
}

function Find-Source {
    # 1) архив или папка, перетащенные на ярлык
    if ($env:OBN_ARG -and (Test-Path $env:OBN_ARG)) { return (Get-Item $env:OBN_ARG) }
    # 2) скрипт запущен из распакованного архива (первый раз): берём эту папку
    $selfDir = Split-Path $env:OBN_SELF
    if ($env:OBN_SELF -ne $Installed -and (Get-ChildItem $selfDir -Recurse -Filter '*.xml' -ErrorAction SilentlyContinue |
            Where-Object { $_.Directory.Name -eq 'src' } | Select-Object -First 1)) {
        return (Get-Item $selfDir)
    }
    # 3) самый свежий архив в Загрузках, на рабочем столе или в Документах
    $places = @()
    try { $places += (New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path } catch { }
    $places += (Join-Path $env:USERPROFILE 'Downloads'), $Desktop, [Environment]::GetFolderPath('MyDocuments')
    $zips = @()
    foreach ($pl in ($places | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
        foreach ($pat in $ArchivePatterns) { $zips += Get-ChildItem $pl -Filter $pat -File -ErrorAction SilentlyContinue }
    }
    if ($zips.Count -gt 0) { return ($zips | Sort-Object LastWriteTime -Descending | Select-Object -First 1) }
    # 4) не нашли - спросим
    Warn 'Не нашёл архив с обработками в «Загрузках». Укажите его сами.'
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Title = 'Архив с обработками 1С'
    $dlg.Filter = 'Архив ZIP (*.zip)|*.zip'
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return (Get-Item $dlg.FileName) }
    return $null
}

# ------------------------------------------------------------------
Write-Host ''
Say 'ОБНОВЛЕНИЕ ОБРАБОТОК 1С' 'White'
Write-Host ''

try {
    New-Item -ItemType Directory -Force -Path $HomeDir, $EpfDir | Out-Null
    $settings = Load-Settings

    $v8 = Find-1C
    if (-not $v8) { Fail 'На этом компьютере не найдена 1С:Предприятие 8.3. Запустите на компьютере, где стоит 1С.'; Finish 1 }
    $v8exe = Join-Path $v8.Bin '1cv8.exe'
    $v8client = Join-Path $v8.Bin '1cv8c.exe'
    if (-not (Test-Path $v8client)) { $v8client = $v8exe }
    Say "1С: $($v8.Version)"

    $src = Find-Source
    if (-not $src) { Fail 'Архив не выбран.'; Finish 1 }
    Say "Беру: $($src.FullName)  (от $($src.LastWriteTime.ToString('dd.MM.yyyy HH:mm')))"

    # распаковка или копирование во временную папку на диске C:
    if (Test-Path $Work) { Remove-Item $Work -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    $unpacked = Join-Path $Work 'src_all'
    if ($src.PSIsContainer) { Copy-Item $src.FullName $unpacked -Recurse }
    else { Expand-Zip $src.FullName $unpacked }

    # этот скрипт (или его новая версия из архива) ставится в постоянную папку
    $newSelf = Get-ChildItem $unpacked -Recurse -File | Where-Object { $_.Name -in @('ОБНОВИТЬ.txt', 'ОБНОВИТЬ.bat') } | Select-Object -First 1
    $selfSource = if ($newSelf) { $newSelf.FullName } else { $env:OBN_SELF }
    if ($selfSource -ne $Installed) { Copy-Item $selfSource $Installed -Force }

    Choose-Base $settings
    Save-Settings $settings

    $xmls = @(Get-ChildItem $unpacked -Recurse -Filter '*.xml' -File | Where-Object { $_.Directory.Name -eq 'src' })
    if ($xmls.Count -eq 0) { Fail 'В архиве нет папки src с обработкой. Пришлите архив целиком.'; Finish 1 }

    $ready = @()
    $failed = @()
    foreach ($xml in $xmls) {
        $key = $xml.BaseName
        $info = $Products[$key]
        if (-not $info) { $info = @{ Title = $key; Epf = "$key.epf" } }
        $title = $info.Title
        $epf = Join-Path $EpfDir $info.Epf
        $hash = Hash-Folder $xml.Directory.FullName
        Write-Host ''
        if ($settings["Hash_$key"] -eq $hash -and (Test-Path $epf)) {
            Ok "«$title»: уже последняя версия, пересобирать не нужно."
            $ready += [pscustomobject]@{ Title = $title; Epf = $epf }
            continue
        }
        Say "Собираю «$title»... (около минуты)" 'White'
        $tmpEpf = Join-Path $Work $info.Epf
        if (Build-Epf $v8exe $xml.FullName $tmpEpf $settings) {
            try {
                Copy-Item $tmpEpf $epf -Force
            } catch {
                Fail "«$title» собрана, но файл занят. Закройте эту обработку в 1С и запустите обновление ещё раз."
                $failed += $title
                continue
            }
            $settings["Hash_$key"] = $hash
            Save-Settings $settings
            Ok "«$title»: готово."
            $ready += [pscustomobject]@{ Title = $title; Epf = $epf }
        } else {
            $logText = Read-Log (Join-Path $Work 'build.log')
            Fail "«$title» собрать не удалось. Сообщение 1С:"
            Write-Host $logText
            $report = Join-Path $Desktop ("Ошибка_обновления_1С_" + (Get-Date -Format 'yyyyMMdd_HHmm') + '.txt')
            $lines = @("ОШИБКА СБОРКИ: $title", "Архив: $($src.FullName)", "1С: $($v8.Version)",
                       "Способ: " + $(if ($settings['Mode'] -eq 'base') { 'через рабочую базу' } else { 'временная база' }),
                       "Компьютер: $([Environment]::OSVersion.VersionString)", '', 'СООБЩЕНИЕ 1С:', $logText)
            $lines = @($lines | ForEach-Object { Mask-Private ([string]$_) })
            [IO.File]::WriteAllLines($report, [string[]]$lines, (New-Object Text.UTF8Encoding $true))
            Warn "Пришлите в чат файл с рабочего стола: $(Split-Path $report -Leaf)"
            if ($logText -match 'прав|Конфигуратор|rights') {
                Warn 'Похоже, у вашей учётной записи нет доступа к режиму «Конфигуратор». Попросите специалиста 1С запустить обновление один раз под своим пользователем.'
            }
            $failed += $title
        }
    }

    # ярлыки на рабочем столе
    Make-Shortcut (Join-Path $Desktop 'Обновить обработки 1С.lnk') $Installed '' "$env:SystemRoot\System32\shell32.dll,238" 'Найти свежий архив в Загрузках и обновить обработки 1С'
    foreach ($r in $ready) {
        if ($settings['Base']) {
            Make-Shortcut (Join-Path $Desktop "$($r.Title).lnk") $v8client ("ENTERPRISE /IBConnectionString " + (Quote-Conn $settings['Base']) + " /Execute `"$($r.Epf)`"") "$v8client,0" "Открыть «$($r.Title)» в 1С"
        }
    }
    if (-not $settings['Base']) {
        Make-Shortcut (Join-Path $Desktop 'Обработки 1С (файлы).lnk') $EpfDir '' "$env:SystemRoot\System32\shell32.dll,3" 'Файлы .epf: открывать в 1С через Файл - Открыть'
    }
    try { Remove-Item $Work -Recurse -Force } catch { }

    Write-Host ''
    Write-Host '  ==========================================================' -ForegroundColor Green
    if ($ready.Count -gt 0) {
        Ok 'ГОТОВО. На рабочем столе ярлыки:'
        foreach ($r in $ready) {
            if ($settings['Base']) { Ok "  «$($r.Title)» - открывает обработку в 1С" }
            else { Ok "  «$($r.Title)»: файл $($r.Epf) - откройте в 1С через Файл - Открыть" }
        }
        Ok '  «Обновить обработки 1С» - когда придёт новый архив.'
        Say 'Если 1С уже открыта, ярлык откроет ещё одно окно 1С. Можно и по-старому: Файл - Открыть,'
        Say "файлы лежат в папке $EpfDir"
        Say 'Обработка, открытая до обновления, остаётся старой: закройте её и откройте ярлыком заново.'
    }
    if ($failed.Count -gt 0) { Fail ('Не обновлено: ' + ($failed -join ', ')) }
    Write-Host '  ==========================================================' -ForegroundColor Green
    if ($failed.Count -gt 0) { Finish 1 } else { Finish 0 }
}
catch {
    Fail $_.Exception.Message
    Say 'Сфотографируйте этот экран и пришлите в чат.'
    Finish 1
}
