#!/usr/bin/env python3
"""Проверка модулей 1С (*.bsl) перед отправкой архива.

Ловит ошибки, которые уже встречались в этих обработках и всплывали только
у пользователя при открытии формы:

  * переменная или параметр с именем свойства формы или глобального контекста
    (ИмяФормы, Подсказка, Документы, Элементы...): «Поле объекта недоступно для записи»;
  * несуществующие свойства элементов формы (ВысотаВСтроках);
  * вызов метода прямо у выражения «Новый ...(...).Метод()»: «Неопознанный оператор»;
  * непарные Процедура/КонецПроцедуры, Если/КонецЕсли, Цикл/КонецЦикла,
    Попытка/КонецПопытки, #Область/#КонецОбласти;
  * вызов процедуры этого модуля, которой в модуле нет (опечатка в имени);
  * повторное объявление процедуры или функции.

  python3 1c_tools/check_bsl.py [папка или файлы ...]

Без аргументов проверяет все *.bsl в репозитории. Код возврата 1, если есть ошибки.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Свойства управляемой формы: их нельзя использовать как имена переменных в модуле формы.
FORM_PROPERTIES = """
Заголовок Элементы Команды Параметры Окно Модифицированность ТолькоПросмотр ИмяФормы
КлючНазначенияИспользования ВладелецФормы ЗакрыватьПриВыборе ЗакрыватьПриЗакрытииВладельца
РежимОткрытияОкна УникальныйИдентификатор ТекущийЭлемент КоманднаяПанель Автозаголовок
Ширина Высота Группировка ПоложениеКоманднойПанели КартинкаЗаголовка РазрешитьИзменятьФорму
СохранятьДанныеВНастройках ПроверятьЗаполнениеАвтоматически КлючСохраненияПоложенияОкна
ЭтаФорма ЭтотОбъект
""".split()
# Свойства, которые модуль формы законно меняет (Заголовок = "...").
WRITABLE_FORM_PROPERTIES = {"ТекущийЭлемент", "Заголовок", "Модифицированность", "ТолькоПросмотр",
                            "Автозаголовок", "Ширина", "Высота", "Группировка", "РежимОткрытияОкна",
                            "ЗакрыватьПриВыборе", "ЗакрыватьПриЗакрытииВладельца", "ВладелецФормы",
                            "КлючНазначенияИспользования", "ПоложениеКоманднойПанели", "КартинкаЗаголовка"}

# Глобальный контекст: присваивание переменной с таким именем падает при выполнении.
GLOBAL_CONTEXT = """
Документы Справочники РегистрыСведений РегистрыНакопления РегистрыБухгалтерии РегистрыРасчета
Константы Перечисления Обработки Отчеты ПланыСчетов ПланыВидовХарактеристик ПланыВидовРасчета
БизнесПроцессы Задачи ПланыОбмена ЖурналыДокументов Последовательности ВнешниеОбработки
ВнешниеОтчеты ПользователиИнформационнойБазы ПараметрыСеанса Метаданные РабочаяДата
ХранилищеОбщихНастроек ХранилищеСистемныхНастроек ХранилищеНастроекДанныхФорм
ХранилищеВариантовОтчетов ХранилищеПользовательскихНастроекОтчетов БиблиотекаКартинок
БиблиотекаСтилей ЦветаСтиля ШрифтыСтиля РамкиСтиля Символы ГлавныйИнтерфейс ГлавныйСтиль
ПланыВидовХарактеристик КритерииОтбора РегламентныеЗадания ФоновыеЗадания ПолнотекстовыйПоиск
ФункциональныеОпции ПараметрыФункциональныхОпций ЭлектронныеПодписи
""".split()

# Что недоступно в контексте процедуры («Процедура или функция с указанным именем не определена»
# или ошибка при выполнении). Ключ — регулярное выражение, значение — подсказка.
# На клиенте (тонкий клиент) нет серверных функций и обращений к данным:
SERVER_ONLY = {
    r"ПолучитьСообщенияПользователю\s*\(": "ПолучитьСообщенияПользователю() есть только на сервере",
    r"РеквизитФормыВЗначение\s*\(": "РеквизитФормыВЗначение() — только &НаСервере",
    r"ЗначениеВРеквизитФормы\s*\(": "ЗначениеВРеквизитФормы() — только &НаСервере",
    r"Новый\s+Запрос\b": "запросы — только на сервере",
    r"(?<![\wА-Яа-яЁё.])(Метаданные|Справочники|Документы|РегистрыСведений|РегистрыНакопления|РегистрыБухгалтерии|ПланыСчетов|ХранилищеОбщихНастроек|Перечисления)\.":
        "метаданные и данные базы на клиенте недоступны — вызовите серверную функцию",
    r"ТекущаяДатаСеанса\s*\(": "на клиенте используйте ТекущаяДата()",
}
# На сервере нет клиентских функций (диалоги, оповещения, файлы клиента):
CLIENT_ONLY = {
    r"(?<![\wА-Яа-яЁё.])(ПоказатьПредупреждение|ПоказатьВопрос|ПоказатьОповещениеПользователя|Состояние|ОткрытьФорму|Предупреждение|Вопрос|ОткрытьЗначение)\s*\(":
        "диалоги и оповещения — только &НаКлиенте",
    r"ФайловаяСистемаКлиент\.": "ФайловаяСистемаКлиент — клиентский модуль",
}
# В &НаСервереБезКонтекста нет формы:
NO_FORM_CONTEXT = {
    r"(?<![\wА-Яа-яЁё.])(?<!Новый )(ЭтотОбъект|Элементы|Команды|УникальныйИдентификатор|КоманднаяПанель)(?![\wА-Яа-яЁё(])": "в &НаСервереБезКонтекста нет формы",
    r"РеквизитФормыВЗначение\s*\(": "в &НаСервереБезКонтекста нет формы",
}

LANG_KEYWORDS = set("""ЕСЛИ ТОГДА ИНАЧЕЕСЛИ ИНАЧЕ КОНЕЦЕСЛИ ДЛЯ КАЖДОГО ИЗ ПО ЦИКЛ КОНЕЦЦИКЛА ПОКА ПРОЦЕДУРА ФУНКЦИЯ
КОНЕЦПРОЦЕДУРЫ КОНЕЦФУНКЦИИ ВОЗВРАТ ПРОДОЛЖИТЬ ПРЕРВАТЬ И ИЛИ НЕ ПОПЫТКА ИСКЛЮЧЕНИЕ ВЫЗВАТЬИСКЛЮЧЕНИЕ
КОНЕЦПОПЫТКИ НОВЫЙ ВЫПОЛНИТЬ ПЕРЕЙТИ ЗНАЧ ЭКСПОРТ ПЕРЕМ ИСТИНА ЛОЖЬ НЕОПРЕДЕЛЕНО NULL ДОБАВИТЬОБРАБОТЧИК
УДАЛИТЬОБРАБОТЧИК АСИНХ ЖДАТЬ IF THEN ELSIF ELSE ENDIF FOR EACH IN TO DO ENDDO WHILE PROCEDURE FUNCTION
ENDPROCEDURE ENDFUNCTION RETURN CONTINUE BREAK AND OR NOT TRY EXCEPT RAISE ENDTRY NEW EXECUTE GOTO VAL EXPORT
VAR TRUE FALSE UNDEFINED ASYNC AWAIT""".split())

QUERY_KEYWORDS = set("""В И ИЛИ НЕ ИЗ ПО ГДЕ КАК ДЛЯ ВСЕ ЕСТЬ ТОГДА ИНАЧЕ КОГДА КОНЕЦ МЕЖДУ ПОДОБНО ВЫБОР ВЫБРАТЬ
ОБЪЕДИНИТЬ ИМЕЮЩИЕ ИТОГИ ПЕРВЫЕ РАЗЛИЧНЫЕ УПОРЯДОЧИТЬ СГРУППИРОВАТЬ СОЕДИНЕНИЕ ЛЕВОЕ ПРАВОЕ ПОЛНОЕ ВНУТРЕННЕЕ
ИЕРАРХИИ ВОЗР УБЫВ ПОМЕСТИТЬ УНИЧТОЖИТЬ ИНДЕКСИРОВАТЬ ВЫРАЗИТЬ NULL ИСТИНА ЛОЖЬ НЕОПРЕДЕЛЕНО
IN AND OR NOT FROM BY WHERE AS FOR ALL IS THEN ELSE WHEN END BETWEEN LIKE CASE SELECT""".split())

# Функций с такими именами во встроенном языке 1С нет (ни в русском, ни в английском варианте):
# «Процедура или функция с указанным именем не определена» при открытии обработки.
NOT_1C_FUNCTIONS = {
    "Abs": "модуль числа: Макс(Х, -Х)",
    "Floor": "Цел(Х) для положительных",
    "Ceil": "Цел(Х) + ?(Х > Цел(Х), 1, 0)",
    "Length": "СтрДлина(Х)",
    "Substring": "Сред(Х, Начало, Длина)",
    "Contains": "СтрНайти(Х, Подстрока) > 0",
    "IsNull": "ЕстьNULL в запросе или Х = NULL в коде",
    "Модуль": "Макс(Х, -Х)",
    "Абс": "Макс(Х, -Х)",
}

BAD_PROPERTIES = {
    "ВысотаВСтроках": "у элементов формы нет свойства ВысотаВСтроках, есть Высота",
    "ВысотаВСтрокахТаблицы": "у таблицы формы свойство называется ВысотаВСтрокахТаблицы только в табличном поле — проверьте",
}

PAIRS = [
    (r"Процедура", r"КонецПроцедуры"),
    (r"Функция", r"КонецФункции"),
    (r"Если", r"КонецЕсли"),
    (r"Попытка", r"КонецПопытки"),
]

KEYWORDS = set("""
Если Тогда ИначеЕсли Иначе КонецЕсли Для Каждого Из По Пока Цикл КонецЦикла Процедура Функция
КонецПроцедуры КонецФункции Возврат Продолжить Прервать Попытка Исключение КонецПопытки ВызватьИсключение
Новый Знач Экспорт Перем И Или Не Истина Ложь Неопределено NULL Выполнить Ждать Асинх Перейти
ДобавитьОбработчик УдалитьОбработчик
""".split())


def strip_strings_and_comments(line: str) -> str:
    """Убирает комментарии и содержимое строковых литералов, сохраняя кавычки."""
    out = []
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    i += 2
                    continue
                in_str = False
                out.append('"')
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append('"')
        elif line.startswith("//", i):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def clean_lines(text: str):
    """Строки модуля без комментариев и текста литералов (многострочные литералы с |)."""
    result = []
    in_multiline = False
    for raw in text.splitlines():
        line = raw
        if in_multiline:
            stripped = line.lstrip()
            if stripped.startswith("|"):
                # продолжение многострочного литерала: ищем закрывающую кавычку
                body = stripped[1:]
                closed = False
                j = 0
                while j < len(body):
                    if body[j] == '"':
                        if j + 1 < len(body) and body[j + 1] == '"':
                            j += 2
                            continue
                        closed = True
                        break
                    j += 1
                if closed:
                    in_multiline = False
                    line = '"' + body[j:]
                else:
                    result.append("")
                    continue
            else:
                in_multiline = False
        cleaned = strip_strings_and_comments(line)
        if cleaned.count('"') % 2 == 1:
            in_multiline = True
        result.append(cleaned)
    return result


def check_file(path: Path):
    text = path.read_text(encoding="utf-8-sig")
    lines = clean_lines(text)
    is_form = "Forms" in path.parts
    problems = []

    def add(n, msg):
        problems.append(f"{path.relative_to(ROOT)}:{n}: {msg}")

    # объявленные методы
    declared = {}
    for n, line in enumerate(lines, 1):
        m = re.match(r"\s*(?:Асинх\s+)?(Процедура|Функция)\s+([\wА-Яа-яЁё]+)\s*\(([^)]*)\)?", line)
        if m:
            name = m.group(2)
            if name in declared:
                add(n, f"метод {name} уже объявлен в строке {declared[name]}")
            declared[name] = n
            params = m.group(3) or ""
            for p in params.split(","):
                p = re.sub(r"^\s*Знач\s+", "", p).split("=")[0].strip()
                if p in GLOBAL_CONTEXT:
                    add(n, f"параметр «{p}» совпадает с именем глобального контекста")

    # число аргументов при вызове методов этого модуля (вызов на одной строке)
    arity = {}
    for n, line in enumerate(lines, 1):
        m = re.match(r"\s*(?:Асинх\s+)?(?:Процедура|Функция)\s+([\wА-Яа-яЁё]+)\s*\(([^)]*)\)", line)
        if m:
            prms = [x for x in m.group(2).split(",") if x.strip()]
            required = sum(1 for x in prms if "=" not in x)
            arity[m.group(1)] = (required, len(prms))
    for n, line in enumerate(lines, 1):
        if re.match(r"\s*(?:Асинх\s+)?(?:Процедура|Функция)\s", line):
            continue
        for m in re.finditer(r"(?<![\wА-Яа-яЁё.])([\wА-Яа-яЁё]+)\s*\(", line):
            name = m.group(1)
            if name not in arity:
                continue
            depth, i, args, empty = 1, m.end(), 1, True
            while i < len(line) and depth:
                ch = line[i]
                if ch in "([":
                    depth += 1
                elif ch in ")]":
                    depth -= 1
                elif ch == "," and depth == 1:
                    args += 1
                elif not ch.isspace() and depth >= 1:
                    empty = False
                i += 1
            if depth:
                continue  # вызов продолжается на следующей строке
            count = 0 if empty and args == 1 else args
            lo, hi = arity[name]
            if count > hi:
                add(n, f"{name}(): передано {count} арг., а у метода {hi} параметр(ов)")
            elif count < lo and "," not in line[m.end():i] and count == 0:
                add(n, f"{name}(): не переданы обязательные параметры ({lo})")

    # Свернуть() оставляет только перечисленные колонки: колонка, добавленная до свёртки и не перечисленная,
    # пропадает, и обращение к ней потом даёт «Поле объекта не обнаружено»
    starts = [i for i, l in enumerate(lines) if re.match(r"\s*(?:Асинх\s+)?(?:Процедура|Функция)\s", l)] + [len(lines)]
    for k in range(len(starts) - 1):
        body = range(starts[k], starts[k + 1])
        added = {}
        for i in body:
            raw = text.splitlines()[i] if i < len(text.splitlines()) else ""
            m = re.search(r"([\wА-Яа-яЁё]+)\.Колонки\.Добавить\(\s*\"([\wА-Яа-яЁё]+)\"", raw)
            if m:
                added.setdefault(m.group(1), {})[m.group(2)] = i
            m = re.search(r"([\wА-Яа-яЁё]+)\.Свернуть\(\s*\"([^\"]*)\"\s*(?:,\s*\"([^\"]*)\")?", raw)
            if m and m.group(1) in added:
                kept = {c.strip() for c in (m.group(2) + "," + (m.group(3) or "")).split(",") if c.strip()}
                for col, at in list(added[m.group(1)].items()):
                    if col in kept:
                        continue
                    for j in range(i + 1, starts[k + 1]):
                        later = lines[j]
                        if re.search(r"\.Колонки\.Добавить\(\s*\"" + col + r"\"", text.splitlines()[j]):
                            break
                        if re.search(r"\." + col + r"(?![\wА-Яа-яЁё])", later):
                            add(i + 1, f"{m.group(1)}.Свернуть() выбросит колонку «{col}» (добавлена в строке {at + 1}), а она используется в строке {j + 1}")
                            break
                    del added[m.group(1)][col]

    # переменная с именем встроенной функции перекрывает её: дальше «Тип("...")» вызовет ошибку
    BUILTIN_FUNCS = {"Тип", "ТипЗнч", "Формат", "Строка", "Число", "Дата", "Мин", "Макс", "Окр", "Цел", "Лев", "Прав", "Сред", "СтрНайти", "НРег", "ВРег"}
    for n, line in enumerate(lines, 1):
        m = re.match(r"\s*([\wА-Яа-яЁё]+)\s*=(?!=)", line)
        if m and m.group(1) in BUILTIN_FUNCS:
            add(n, f"переменная «{m.group(1)}» перекрывает встроенную функцию {m.group(1)}()")

    # служебные слова встроенного языка нельзя использовать как имена параметров и переменных
    for n, line in enumerate(lines, 1):
        m = re.match(r"\s*(?:Асинх\s+)?(?:Процедура|Функция)\s+[\wА-Яа-яЁё]+\s*\(([^)]*)\)", line)
        if m:
            for prm in m.group(1).split(","):
                name = re.sub(r"^\s*Знач\s+", "", prm).split("=")[0].strip()
                if name and name.upper() in LANG_KEYWORDS:
                    add(n, f"параметр «{name}» — служебное слово языка 1С: «Ожидается имя формального параметра»")
        m = re.match(r"\s*([\wА-Яа-яЁё]+)\s*=(?!=)", line)
        if m and m.group(1).upper() in LANG_KEYWORDS:
            add(n, f"переменная «{m.group(1)}» — служебное слово языка 1С")
        m = re.match(r"\s*Для\s+Каждого\s+([\wА-Яа-яЁё]+)\s+Из", line)
        if m and m.group(1).upper() in LANG_KEYWORDS:
            add(n, f"переменная цикла «{m.group(1)}» — служебное слово языка 1С")

    # вызовы функций, которых нет в языке 1С
    for n, line in enumerate(lines, 1):
        for name, hint in NOT_1C_FUNCTIONS.items():
            if re.search(r"(?<![\wА-Яа-яЁё.])" + name + r"\s*\(", line) and name not in declared:
                add(n, f"функции {name}() нет в языке 1С — используйте {hint}")

    # удаление с конца по «X[X.Количество() - Индекс]»: после Удалить() индекс уходит за границы массива
    for n, line in enumerate(lines, 1):
        m = re.search(r"([\wА-Яа-яЁё.]+)\[\1\.Количество\(\)\s*-", line)
        if m:
            add(n, f"«{m.group(1)}[{m.group(1)}.Количество() - …]» в цикле с удалением даёт «Индекс находится за границами» — запомните число строк до цикла")
    # удаление из коллекции, которую перебирает «Для Каждого»
    open_loops = []
    for n, line in enumerate(lines, 1):
        m = re.match(r"\s*Для Каждого\s+[\wА-Яа-яЁё]+\s+Из\s+([\wА-Яа-яЁё.]+)\s+Цикл", line)
        if m:
            open_loops.append((m.group(1), n))
            continue
        if re.match(r"\s*КонецЦикла\b", line) and open_loops:
            open_loops.pop()
            continue
        for coll, start in open_loops:
            if re.search(r"(?<![\wА-Яа-яЁё.])" + re.escape(coll) + r"\.Удалить\(", line):
                add(n, f"удаление из «{coll}» внутри «Для Каждого … Из {coll}» (строка {start}): строки пропускаются — перебирайте копию или с конца")

    # псевдоним в запросе не может быть служебным словом языка запросов («КАК В», «КАК И» -> «Ожидается имя»)
    for n, raw in enumerate(text.splitlines(), 1):
        for m in re.finditer(r"КАК\s+([А-Яа-яЁёA-Za-z_]+)(?![\wА-Яа-яЁё])", raw):
            if m.group(1).upper() in QUERY_KEYWORDS:
                add(n, f"псевдоним «{m.group(1)}» в запросе — служебное слово языка запросов, 1С выдаст «Ожидается имя»")

    # доступность по контексту: директива перед методом (&НаКлиенте…), модуль объекта — сервер
    is_object_module = path.name == "ObjectModule.bsl"
    directive = None
    context = None
    for n, line in enumerate(lines, 1):
        st = line.strip()
        m = re.match(r"&([\wА-Яа-яЁё]+)", st)
        if m:
            directive = m.group(1)
            continue
        if re.match(r"(?:Асинх\s+)?(Процедура|Функция)\b", st):
            context = directive if is_form else ("НаСервере" if is_object_module else None)
            directive = None
            continue
        if re.match(r"(КонецПроцедуры|КонецФункции)\b", st):
            context = None
            continue
        if context is None:
            continue
        checks = {}
        if context == "НаКлиенте":
            checks = SERVER_ONLY
        elif context in ("НаСервере", "НаСервереБезКонтекста"):
            checks = dict(CLIENT_ONLY)
            if context == "НаСервереБезКонтекста":
                checks.update(NO_FORM_CONTEXT)
        elif context == "НаКлиентеНаСервереБезКонтекста":
            checks = dict(SERVER_ONLY)
            checks.update(CLIENT_ONLY)
            checks.update(NO_FORM_CONTEXT)
        for rx, hint in checks.items():
            if re.search(rx, line):
                add(n, f"недоступно в контексте &{context}: {hint}")

    # баланс блоков
    depth_stack = []
    region_stack = []
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if re.match(r"#Область\b", s):
            region_stack.append(n)
        elif re.match(r"#КонецОбласти\b", s):
            if not region_stack:
                add(n, "#КонецОбласти без #Область")
            else:
                region_stack.pop()
        if s.startswith("#") or s.startswith("&"):
            continue
        for word in re.findall(r"[\wА-Яа-яЁё]+", s):
            for opener, closer in PAIRS:
                if word == opener:
                    depth_stack.append((opener, n))
                elif word == closer:
                    if not depth_stack or depth_stack[-1][0] != opener:
                        add(n, f"{closer} без {opener}" + (
                            f" (открыт {depth_stack[-1][0]} в строке {depth_stack[-1][1]})" if depth_stack else ""))
                    else:
                        depth_stack.pop()
            if word == "Цикл":
                depth_stack.append(("Цикл", n))
            elif word == "КонецЦикла":
                if not depth_stack or depth_stack[-1][0] != "Цикл":
                    add(n, "КонецЦикла без Цикл")
                else:
                    depth_stack.pop()
    for opener, n in depth_stack:
        add(n, f"{opener} не закрыт")
    for n in region_stack:
        add(n, "#Область не закрыта")

    for n, line in enumerate(lines, 1):
        # присваивание переменной с запрещённым именем
        m = re.match(r"\s*([\wА-Яа-яЁё]+)\s*=(?!=)", line)
        names = []
        if m:
            names.append(m.group(1))
        m = re.match(r"\s*Для\s+Каждого\s+([\wА-Яа-яЁё]+)\s+Из\b", line)
        if m:
            names.append(m.group(1))
        m = re.match(r"\s*Для\s+([\wА-Яа-яЁё]+)\s*=", line)
        if m:
            names.append(m.group(1))
        m = re.match(r"\s*Перем\s+(.+);", line)
        if m:
            names += [x.strip() for x in m.group(1).split(",")]
        for name in names:
            if name in ("Если", "Для", "Каждого", "Пока"):
                continue
            if is_form and name in FORM_PROPERTIES and name not in WRITABLE_FORM_PROPERTIES:
                add(n, f"переменная «{name}» совпадает со свойством формы (переименуйте)")
            if name in GLOBAL_CONTEXT:
                add(n, f"переменная «{name}» совпадает с именем глобального контекста (переименуйте)")

        for prop, why in BAD_PROPERTIES.items():
            if re.search(r"\." + prop + r"\b", line):
                add(n, why)

        for m in re.finditer(r"\bНовый\s+[\wА-Яа-яЁё]+\s*\(", line):
            depth, j = 0, m.end() - 1
            while j < len(line):
                if line[j] == "(":
                    depth += 1
                elif line[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth == 0 and re.match(r"\s*\.\s*[\wА-Яа-яЁё]+", line[j + 1:]):
                add(n, "вызов метода прямо у «Новый ...()» — сначала присвойте объект переменной")

    # «Возврат;» в функции и «Возврат значение» в процедуре
    kind = None
    for n, line in enumerate(lines, 1):
        m = re.match(r"\s*(?:Асинх\s+)?(Процедура|Функция)\s", line)
        if m:
            kind = m.group(1)
            continue
        if re.match(r"\s*Конец(Процедуры|Функции)\b", line):
            kind = None
            continue
        if kind == "Функция" and re.match(r"\s*Возврат\s*;", line):
            add(n, "«Возврат;» без значения внутри функции")
        if kind == "Процедура" and re.match(r"\s*Возврат\s+[^;\s]", line):
            add(n, "«Возврат» со значением внутри процедуры")

    # вызовы несуществующих процедур этого модуля: только для имён, похожих на локальные
    # (с суффиксами НаСервере/НаКлиенте/Завершение или начинающихся с глагола, объявленного рядом)
    for n, line in enumerate(lines, 1):
        for m in re.finditer(r"(?<![.\wА-Яа-яЁё])([\wА-Яа-яЁё]+(?:НаСервере|НаКлиенте|Завершение))\s*\(", line):
            name = m.group(1)
            if name not in declared and name not in KEYWORDS:
                add(n, f"вызывается {name}(), но такого метода в модуле нет")

    return problems


def main(argv):
    targets = [Path(a) for a in argv] or [ROOT]
    files = []
    for t in targets:
        t = t.resolve()
        files += sorted(t.rglob("*.bsl")) if t.is_dir() else [t]
    problems = []
    for f in files:
        problems += check_file(f)
    for p in problems:
        print(p)
    print(f"Проверено модулей: {len(files)}, замечаний: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
