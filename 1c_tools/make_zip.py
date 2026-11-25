#!/usr/bin/env python3
"""Собрать общий архив обработок для отправки по почте: Obrabotki_1C_dlya_pochty.zip.

Внутри обе обработки и ОБНОВИТЬ.txt (почта не пропускает .bat). Имена файлов пишутся
в кодировке cp866: так их правильно показывает распаковщик Windows и читает ОБНОВИТЬ.bat.
Перед сборкой модули проверяются 1c_tools/check_bsl.py; при замечаниях архив не создаётся.

  python3 1c_tools/make_zip.py [папка_для_архива]
"""
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOP = "Obrabotki_1C"
PROJECTS = [
    (ROOT / "production" / "1c", "Otchet_rukovoditelyu"),
    (ROOT / "1c_invoice_photo", "Nakladnaya_po_foto"),
    (ROOT / "1c_service", "Servis_ucheta"),
    (ROOT / "1c_goz", "GOZ_zatraty_874"),
]
README = """ОБРАБОТКИ 1С: ЕЖЕДНЕВНЫЙ ОТЧЁТ, НАКЛАДНАЯ ПО ФОТО, СЕРВИС УЧЁТА, ГОЗ ЗАТРАТЫ ПП 874

ПЕРВЫЙ РАЗ (один раз на этом компьютере):
1. Распакуйте архив целиком (правой кнопкой - «Извлечь всё»).
2. Переименуйте ОБНОВИТЬ.txt в ОБНОВИТЬ.bat
   (почта не пропускает .bat, поэтому он приходит с расширением .txt).
   Если расширения не видны: в Проводнике «Вид» - отметьте «Расширения имён файлов».
3. Дважды щёлкните ОБНОВИТЬ.bat. Он спросит вашу рабочую базу 1С (один раз),
   соберёт обработки и положит на рабочий стол ярлыки:
     «Ежедневный отчёт», «Накладная по фото», «Сервис учёта»,
     «ГОЗ затраты ПП 874», «Обновить обработки 1С».

КАЖДОЕ СЛЕДУЮЩЕЕ ОБНОВЛЕНИЕ:
1. Сохраните архив из письма в «Загрузки» (распаковывать и переименовывать не нужно).
2. Щёлкните ярлык «Обновить обработки 1С» на рабочем столе.

ЧТОБЫ ОБРАБОТКИ БЫЛИ В 1С НАВСЕГДА (в меню разделов, у всех пользователей,
с расписанием) - см. ПОДКЛЮЧИТЬ_НАВСЕГДА.txt.

Если что-то не получилось - на рабочем столе появится файл Ошибка_обновления_1С_....txt,
пришлите его в чат. Ошибку внутри обработки присылайте кнопкой «Сообщить об ошибке».
"""


def _cp866(self):
    return self.filename.encode("cp866"), self.flag_bits & ~0x800


def main() -> int:
    # скрипт распознавания договоров встраивается в модуль «Ежедневного отчёта»
    subprocess.run([sys.executable, str(HERE / "embed_ps.py")], check=True)
    check = subprocess.run([sys.executable, str(HERE / "check_bsl.py")], capture_output=True, text=True)
    print(check.stdout.strip())
    if check.returncode != 0:
        print("Архив не собран: исправьте замечания проверки.")
        return 1

    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    out = out_dir / f"{TOP}_dlya_pochty.zip"
    zipfile.ZipInfo._encodeFilenameFlags = _cp866
    stamp = (2026, 11, 24, 12, 0, 0)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo(f"{TOP}/ПРОЧТИ_МЕНЯ.txt", stamp), "﻿" + README.replace("\n", "\r\n"))
        z.write(HERE / "ОБНОВИТЬ.bat", f"{TOP}/ОБНОВИТЬ.txt")
        z.writestr(zipfile.ZipInfo(f"{TOP}/ПОДКЛЮЧИТЬ_НАВСЕГДА.txt", stamp),
                   "\ufeff" + (HERE / "ПОДКЛЮЧИТЬ_НАВСЕГДА.txt").read_text(encoding="utf-8").replace("\n", "\r\n"))
        for src, name in PROJECTS:
            for path in sorted(src.rglob("*")):
                if path.is_dir() or path.suffix in (".zip", ".epf", ".py") or "__pycache__" in path.parts:
                    continue
                rel = path.relative_to(src).as_posix()
                if rel == "СОБРАТЬ.bat":
                    rel = "СОБРАТЬ.txt"
                z.write(path, f"{TOP}/{name}/{rel}")
    with zipfile.ZipFile(out) as z:
        bad = z.testzip()
        names = z.namelist()
    if bad:
        print("повреждён файл в архиве:", bad)
        return 1
    print(out)
    for n in names:
        print("  ", n.encode("cp437").decode("cp866"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
