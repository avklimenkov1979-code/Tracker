#!/usr/bin/env python3
"""Собрать архив для отправки по почте: Otchet_rukovoditelyu_dlya_pochty.zip.

Почта часто не пропускает .bat, поэтому СОБРАТЬ.bat кладётся как СОБРАТЬ.txt,
а рядом инструкция ПРОЧТИ_МЕНЯ.txt. Имена файлов пишутся в кодировке cp866:
так их правильно показывает встроенный в Windows распаковщик.

  python3 production/1c/make_zip.py [папка_для_архива]
"""
import os
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = "Otchet_rukovoditelyu"
README = """ОТЧЁТ РУКОВОДИТЕЛЮ ДЛЯ 1С:ERP

1. Распакуйте архив целиком на компьютер, где установлена 1С.
2. Переименуйте файл СОБРАТЬ.txt в СОБРАТЬ.bat
   (почта не пропускает .bat, поэтому он приходит с расширением .txt).
   Если расширения файлов не видны: в Проводнике «Вид» → отметьте «Расширения имён файлов».
3. Дважды щёлкните СОБРАТЬ.bat и подождите около минуты.
   Рядом появится файл Otchet_rukovoditelyu.epf — это программа.
4. В 1С:ERP: Файл → Открыть → Otchet_rukovoditelyu.epf → «Сформировать».

Подробности — в файле README.md.
Если что-то не получилось, сфотографируйте экран и пришлите.
"""


def _cp866(self):
    return self.filename.encode("cp866"), self.flag_bits & ~0x800


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    out = out_dir / f"{ROOT}_dlya_pochty.zip"
    zipfile.ZipInfo._encodeFilenameFlags = _cp866
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo(f"{ROOT}/ПРОЧТИ_МЕНЯ.txt", (2026, 11, 24, 12, 0, 0)),
                   "﻿" + README.replace("\n", "\r\n"))
        for path in sorted(HERE.rglob("*")):
            if path.is_dir() or path.name in ("make_zip.py",) or path.suffix in (".zip", ".epf"):
                continue
            rel = path.relative_to(HERE).as_posix()
            if rel == "СОБРАТЬ.bat":
                rel = "СОБРАТЬ.txt"
            z.write(path, f"{ROOT}/{rel}")
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
