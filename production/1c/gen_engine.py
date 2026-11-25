#!/usr/bin/env python3
"""Скопировать серверную часть «Отчёта руководителю» из модуля формы в модуль объекта.

Зачем: отправка отчёта на почту по расписанию идёт в регламентном задании — на сервере, без формы.
Чтобы письмо было тем же отчётом, что на экране, и чтобы не вести две копии кода руками,
все серверные процедуры формы (кроме построения самой формы) копируются в модуль объекта
между метками «>>> ДВИЖОК» и «<<< ДВИЖОК». Обращения ЭтаФорма["Х"] заменяются на
НастройкиОтчета["Х"] — структуру настроек, которую модуль объекта заполняет сам.

Запускается из 1c_tools/make_zip.py перед проверкой модулей. Модуль объекта между метками руками не править.
  python3 production/1c/gen_engine.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORM = HERE / "src/ОтчетРуководителю/Forms/Форма/Ext/Form/Module.bsl"
OBJECT = HERE / "src/ОтчетРуководителю/Ext/ObjectModule.bsl"
START = "// >>> ДВИЖОК: сгенерировано gen_engine.py из модуля формы — не править здесь"
END = "// <<< ДВИЖОК"

# только построение формы, чтение/запись настроек формы и то, что работает с файлами для клиента
EXCLUDE = {
    "ПриСозданииНаСервере", "СоздатьРеквизитыФормы", "СоздатьЭлементыФормы", "ДобавитьПоле",
    "ВерсияПрограммы", "ОтчетОбОшибкеНаСервере", "ДиагностикаНаСервере",
    "ЗагрузитьНастройки", "СохранитьНастройки",
    "ОтправитьСейчасНаСервере", "СохранитьРассылкуНаСервере", "НастройкиРассылкиИзФормы",
}
SERVER = {"НаСервере", "НаСервереБезКонтекста", "НаКлиентеНаСервереБезКонтекста"}
FORBIDDEN = [
    (re.compile(r"ЭтаФорма"), "ЭтаФорма"),
    (re.compile(r"(?<![\wА-Яа-яЁё.])Элементы\."), "Элементы"),
    (re.compile(r"РеквизитФормыВЗначение"), "РеквизитФормыВЗначение"),
    (re.compile(r"(?<![\wА-Яа-яЁё.])(?<!Новый )УникальныйИдентификатор(?![\wА-Яа-яЁё(])"), "УникальныйИдентификатор формы"),
]


def methods(lines):
    """(директива, имя, строки метода без директивы)."""
    i = 0
    directive = None
    while i < len(lines):
        line = lines[i]
        m = re.match(r"&([\wА-Яа-яЁё]+)\s*$", line)
        if m:
            directive = m.group(1)
            i += 1
            continue
        m = re.match(r"(Процедура|Функция)\s+([\wА-Яа-яЁё]+)", line)
        if m:
            end_word = "КонецПроцедуры" if m.group(1) == "Процедура" else "КонецФункции"
            j = i
            while not re.match(end_word + r"\b", lines[j]):
                j += 1
            yield directive, m.group(2), lines[i:j + 1]
            directive = None
            i = j + 1
            continue
        if line.strip() and not line.strip().startswith("//") and not line.startswith("#"):
            directive = None
        i += 1


def main():
    form_lines = FORM.read_text(encoding="utf-8").split("\n")
    out = [START, "", "#Область ДвижокОтчета", ""]
    count = 0
    problems = []
    for directive, name, body in methods(form_lines):
        if directive not in SERVER or name in EXCLUDE:
            continue
        text = "\n".join(body).replace("ЭтаФорма[", "НастройкиОтчета[")
        if name == "СформироватьНаСервере":
            # настройки рассылки сохраняются отдельно, письмо их не перезаписывает
            text = text.replace("\tСохранитьНастройки();\n", "\t// СохранитьНастройки() — только в форме\n")
        for rx, what in FORBIDDEN:
            for n, line in enumerate(text.split("\n")):
                code = line.split("//")[0]
                if rx.search(code):
                    problems.append(f"{name}: {what} — «{line.strip()}»")
        out.append(text)
        out.append("")
        count += 1
    out += ["#КонецОбласти", "", END]
    if problems:
        print("Движок не собран — в серверных процедурах формы есть обращения к форме:")
        print("\n".join("  " + p for p in problems))
        print("Добавьте процедуру в EXCLUDE или уберите обращение к форме.")
        return 1

    obj = OBJECT.read_text(encoding="utf-8")
    block = "\n".join(out)
    if START in obj:
        a = obj.index(START)
        b = obj.index(END) + len(END)
        obj = obj[:a] + block + obj[b:]
    else:
        i = obj.rstrip().rfind("#КонецЕсли")
        obj = obj[:i] + block + "\n\n" + obj[i:]
    OBJECT.write_text(obj, encoding="utf-8")
    print(f"Движок отчёта: скопировано процедур {count} в {OBJECT.relative_to(HERE.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
