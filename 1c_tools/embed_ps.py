#!/usr/bin/env python3
"""Встроить скрипт распознавания договоров (production/1c/dogovory_pdf.ps1) в модуль формы
«Ежедневного отчёта» функцией ТекстСкриптаРаспознавания() между маркерами
«// >>> СКРИПТ РАСПОЗНАВАНИЯ» и «// <<< СКРИПТ РАСПОЗНАВАНИЯ». Запускается из make_zip.py.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PS = ROOT / "production" / "1c" / "dogovory_pdf.ps1"
MODULE = ROOT / "production" / "1c" / "src" / "ОтчетРуководителю" / "Forms" / "Форма" / "Ext" / "Form" / "Module.bsl"
BEGIN = "// >>> СКРИПТ РАСПОЗНАВАНИЯ"
END = "// <<< СКРИПТ РАСПОЗНАВАНИЯ"


def main() -> int:
    lines = PS.read_text(encoding="utf-8").splitlines()
    body = ["&НаСервере", "Функция ТекстСкриптаРаспознавания()", "", "\tСтроки = Новый Массив;"]
    for line in lines:
        body.append('\tСтроки.Добавить("' + line.replace('"', '""') + '");')
    body += ['\tВозврат СтрСоединить(Строки, Символы.ВК + Символы.ПС);', "", "КонецФункции"]
    raw = MODULE.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    a = text.index(BEGIN)
    a = text.index("\n", a) + 1
    b = text.index(END)
    new = text[:a] + "\n".join(body) + "\n" + text[b:]
    if new != text:
        MODULE.write_text(new, encoding="utf-8-sig" if bom else "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
