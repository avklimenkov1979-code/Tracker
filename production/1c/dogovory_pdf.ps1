param([string]$Dir, [int]$MaxPages = 10, [int]$MaxSeconds = 180)
# Распознавание договоров аренды в PDF без интернета: Windows сам рисует страницы PDF
# (Windows.Data.Pdf) и распознаёт текст (Windows.Media.Ocr). Работает и для PDF из Word, и для сканов.
# Для каждого файла *.pdf в папке пишет рядом *.pdf.txt (текст) и *.pdf.json (найденные показатели).
# Этот файл встраивается в обработку «Ежедневный отчёт» (1c_tools/embed_ps.py), отдельно запускать не нужно.
$ErrorActionPreference = 'Stop'
$log = Join-Path $Dir 'log.txt'
function Log([string]$t) { [IO.File]::AppendAllText($log, $t + "`r`n", [Text.Encoding]::UTF8) }

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
    $null = [Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
} catch {
    Log ('ОШИБКА: в этой Windows нет встроенного распознавания PDF (нужна Windows 10/11 или Server 2019+): ' + $_.Exception.Message)
    exit 2
}

$ext = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTaskOp = $ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$asTaskAct = $ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
function Await($op, [Type]$type) { $t = $asTaskOp.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
function AwaitAction($op) { $t = $asTaskAct.Invoke($null, @($op)); $t.Wait(-1) | Out-Null }

$engine = $null
foreach ($tag in @('ru', 'ru-RU')) {
    if ($engine -eq $null) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language($tag))) }
}
if ($engine -eq $null) {
    Log 'ОШИБКА: в Windows не установлено распознавание текста для русского языка. Параметры - Время и язык - Язык - Русский - Параметры - Распознавание текста (или администратор: Add-WindowsCapability -Online -Name Language.OCR~~~ru-RU~0.0.1.0).'
    exit 3
}

# ---------- поиск показателей в тексте ----------
$NUM = '(\d{1,3}(?:[  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)'
$M2 = '(?:кв\.?\s*м\b\.?|кв\.?\s*м\.|м\s?2\b|м²|квадратн\w*\s+метр\w*)'
$RUB = '(?:руб\w*\.?|₽|р\.)'
$MONTH = '(\d{1,2}|январ[ья]|феврал[ья]|марта?|апрел[ья]|мая|май|июн[ья]|июл[ья]|августа?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])'
$DATE = '«?(\d{1,2})»?\s*(?:\.|\s)\s*' + $MONTH + '\s*(?:\.|\s)\s*(\d{4})'

function ToNum([string]$s) {
    $t = ($s -replace '[\s ]', '') -replace ',', '.'
    $v = 0.0
    if ([double]::TryParse($t, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$v)) { return $v }
    return $null
}
function ToDate([string]$d, [string]$m, [string]$y) {
    $names = @('январ', 'феврал', 'март', 'апрел', 'ма', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр')
    $mm = 0
    if ($m -match '^\d+$') { $mm = [int]$m } else {
        $low = $m.ToLower()
        for ($i = 0; $i -lt 12; $i++) { if ($low.StartsWith($names[$i])) { $mm = $i + 1; break } }
        if ($low.StartsWith('июн')) { $mm = 6 } elseif ($low.StartsWith('июл')) { $mm = 7 }
    }
    if ($mm -lt 1 -or $mm -gt 12 -or [int]$d -lt 1 -or [int]$d -gt 31) { return '' }
    try { return (Get-Date -Year ([int]$y) -Month $mm -Day ([int]$d)).ToString('yyyy-MM-dd') } catch { return '' }
}
function Quote([string]$flat, $m) {
    $a = [Math]::Max(0, $m.Index - 60)
    $b = [Math]::Min($flat.Length, $m.Index + $m.Length + 40)
    return ('…' + $flat.Substring($a, $b - $a).Trim() + '…')
}
function Find([string]$text) {
    $r = [ordered]@{}
    $flat = ($text -replace '\s+', ' ')
    $opt = [Text.RegularExpressions.RegexOptions]::IgnoreCase

    # номер и дата договора
    $m = [regex]::Match($flat, 'договор\w*(?:\s+аренды)?[^№]{0,60}№\s*([\w\-/.]+)', $opt)
    if ($m.Success) { $r['Номер'] = $m.Groups[1].Value.TrimEnd('.') }
    $head = $flat.Substring(0, [Math]::Min(800, $flat.Length))
    $m = [regex]::Match($head, $DATE, $opt)
    if ($m.Success) { $r['ДатаДоговора'] = ToDate $m.Groups[1].Value $m.Groups[2].Value $m.Groups[3].Value }

    # площадь: сначала та, что рядом со словами «аренд», «передава», «помещени»
    $best = $null
    foreach ($m in [regex]::Matches($flat, 'площад\w*[^\d]{0,80}?' + $NUM + '\s*' + $M2, $opt)) {
        $before = $flat.Substring([Math]::Max(0, $m.Index - 120), [Math]::Min(120, $m.Index)).ToLower()
        if ($best -eq $null) { $best = $m }
        if ($before -match 'аренд|передава|помещени') { $best = $m; break }
    }
    if ($best -ne $null) { $r['Площадь'] = ToNum $best.Groups[1].Value; $r['ЦитатаПлощадь'] = Quote $flat $best }

    # ставка за 1 м²
    $m = [regex]::Match($flat, $NUM + '\s*(?:\([^)]{0,150}\)\s*)?' + $RUB + '\s*(?:\d{1,2}\s*коп\w*\.?\s*)?(?:\([^)]{0,80}\)\s*)?(?:за|/|в)\s*(?:1\s*|один\s+)?' + $M2, $opt)
    if ($m.Success) { $r['Ставка'] = ToNum $m.Groups[1].Value; $r['ЦитатаСтавка'] = Quote $flat $m }

    # арендная плата в месяц: не ставка за м², лучше рядом со словом «месяц»
    $best = $null
    foreach ($m in [regex]::Matches($flat, '(?:арендн\w*\s+плат\w*|плат\w*\s+за\s+(?:аренд|пользован)\w*)[^\d]{0,150}?' + $NUM + '\s*(?:\([^)]{0,200}\)\s*)?' + $RUB, $opt)) {
        $after = $flat.Substring($m.Index + $m.Length, [Math]::Min(60, $flat.Length - $m.Index - $m.Length))
        if ([regex]::IsMatch($after, '^\s*(?:\d{1,2}\s*коп\w*\.?\s*)?(?:\([^)]*\)\s*)?(?:за|/|в)\s*(?:1\s*)?' + $M2, $opt)) { continue }
        $near = $flat.Substring([Math]::Max(0, $m.Index - 60), [Math]::Min($flat.Length - [Math]::Max(0, $m.Index - 60), $m.Length + 200)).ToLower()
        if ($best -eq $null) { $best = $m }
        if ($near -match 'месяц|ежемесячн') { $best = $m; break }
    }
    if ($best -ne $null) {
        $r['ВМесяц'] = ToNum $best.Groups[1].Value
        $r['ЦитатаВМесяц'] = Quote $flat $best
        $tail = $flat.Substring($best.Index, [Math]::Min(300, $flat.Length - $best.Index)).ToLower()
        if ($tail -match 'без\s+(?:учета\s+|учёта\s+)?ндс|ндс\s+не\s+облага|не\s+облага\w*\s+ндс') { $r['НДС'] = 'без НДС' }
        elseif ($tail -match 'в\s+том\s+числе\s+ндс|включая\s+ндс|с\s+учетом\s+ндс|с\s+учётом\s+ндс|с\s+ндс|т\.\s*ч\.\s*ндс') { $r['НДС'] = 'с НДС' }
    }
    if (-not $r.Contains('НДС')) {
        if ($flat -match '(?i)ндс\s+не\s+облага|не\s+облага\w*\s+ндс|упрощенн\w*\s+систем') { $r['НДС'] = 'без НДС' }
    }

    # срок аренды: «срок … по/до <дата>»
    $m = [regex]::Match($flat, 'срок\w*.{0,200}?(?<![а-яё])(?:по|до)\s+' + $DATE, $opt)
    if ($m.Success) { $r['СрокДо'] = ToDate $m.Groups[1].Value $m.Groups[2].Value $m.Groups[3].Value; $r['ЦитатаСрок'] = Quote $flat $m }

    # индексация, %
    $m = [regex]::Match($flat, 'индексац\w*[^%]{0,150}?(\d{1,2}(?:[.,]\d{1,2})?)\s*%', $opt)
    if ($m.Success) { $r['Индексация'] = ToNum $m.Groups[1].Value; $r['ЦитатаИндексация'] = Quote $flat $m }

    # адрес помещения
    $m = [regex]::Match($flat, 'по\s+адресу\s*:?\s*(.{10,160}?)(?:;|,\s*(?:общ|кадастр|именуем|для\s|этаж\w*\s+\d|пом\.?\s*\d)|\s\(далее|\.\s+\d+\.\d)', $opt)
    if ($m.Success) { $r['Адрес'] = $m.Groups[1].Value.Trim() }
    return $r
}

# ---------- файлы ----------
foreach ($pdf in Get-ChildItem -LiteralPath $Dir -Filter '*.pdf') {
    $res = [ordered]@{}
    try {
        $sf = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($pdf.FullName)) ([Windows.Storage.StorageFile])
        $doc = Await ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($sf)) ([Windows.Data.Pdf.PdfDocument])
        $sb = New-Object System.Text.StringBuilder
        $pages = [Math]::Min([int]$doc.PageCount, $MaxPages)
        $watch = [Diagnostics.Stopwatch]::StartNew()
        for ($i = 0; $i -lt $pages; $i++) {
            if ($watch.Elapsed.TotalSeconds -gt $MaxSeconds) { $res['Обрезано'] = 'распознано ' + $i + ' стр. за отведённое время'; break }
            $page = $doc.GetPage([uint32]$i)
            $scale = [Math]::Min(2400.0 / [Math]::Max(1.0, $page.Size.Width), 3400.0 / [Math]::Max(1.0, $page.Size.Height))
            $opt = New-Object Windows.Data.Pdf.PdfPageRenderOptions
            $opt.DestinationWidth = [uint32][Math]::Floor($page.Size.Width * $scale)
            $opt.DestinationHeight = [uint32][Math]::Floor($page.Size.Height * $scale)
            $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
            AwaitAction ($page.RenderToStreamAsync($stream, $opt))
            $stream.Seek([uint64]0)
            $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $ocr = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
            foreach ($line in $ocr.Lines) { [void]$sb.AppendLine($line.Text) }
            [void]$sb.AppendLine('')
            $page.Dispose()
            $stream.Dispose()
        }
        $text = $sb.ToString()
        [IO.File]::WriteAllText($pdf.FullName + '.txt', $text, [Text.Encoding]::UTF8)
        $cut = $res['Обрезано']
        $res = Find $text
        if ($cut) { $res['Обрезано'] = $cut }
        $res['Страниц'] = [int]$doc.PageCount
        $res['Символов'] = $text.Length
    } catch {
        $res['Ошибка'] = $_.Exception.Message
    }
    [IO.File]::WriteAllText($pdf.FullName + '.json', ($res | ConvertTo-Json -Depth 3), (New-Object Text.UTF8Encoding($false)))
}
Log 'ГОТОВО'
exit 0
