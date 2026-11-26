#!/usr/bin/env python3
"""Вставить (или обновить) одинаковый блок «ОбезличиваниеДляЧата» во все обработки.

Источник блока — 1c_tools/obezlichivanie_template.bsl. В модулях форм методы получают
директиву &НаСервереБезКонтекста, в модуле объекта — без директив, внешние функции с «Экспорт».
  python3 1c_tools/insert_obezl.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (Path(__file__).resolve().parent / "obezlichivanie_template.bsl").read_text(encoding="utf-8")
TARGETS = [
    (ROOT / "1c_goz/src/ЗатратыГОЗ874/Ext/ObjectModule.bsl", "object"),
    (ROOT / "1c_service/src/СервисУчета/Forms/Форма/Ext/Form/Module.bsl", "form"),
    (ROOT / "production/1c/src/ОтчетРуководителю/Forms/Форма/Ext/Form/Module.bsl", "form"),
    (ROOT / "1c_invoice_photo/src/ЗагрузкаНакладнойПоФото/Forms/Форма/Ext/Form/Module.bsl", "form"),
]
BLOCK_RE = re.compile(r"\n#Область ОбезличиваниеДляЧата\n.*?\n#КонецОбласти\n", re.S)


def render(kind):
    if kind == "form":
        text = TEMPLATE.replace("{D}\n", "&НаСервереБезКонтекста\n").replace("{E}", "")
    else:
        text = TEMPLATE.replace("{D}\n", "").replace("{E}", " Экспорт")
    return "\n" + text.rstrip("\n") + "\n"


def main():
    for path, kind in TARGETS:
        s = path.read_text(encoding="utf-8")
        block = render(kind)
        if BLOCK_RE.search(s):
            s = BLOCK_RE.sub(lambda m: block, s, count=1)
        elif kind == "object":
            i = s.rstrip().rfind("#КонецЕсли")
            s = s[:i] + block.lstrip("\n") + "\n" + s[i:]
        else:
            s = s.rstrip("\n") + "\n" + block
        path.write_text(s, encoding="utf-8")
        print("ok", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
