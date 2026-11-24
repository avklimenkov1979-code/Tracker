"""Общая часть производственных отчётов: чтение таблиц, разбор чисел и дат, форматирование.

Таблицы — CSV (как сохраняет Excel или 1С: разделитель «;» или «,», кодировка
UTF-8 или Windows-1251) либо .xlsx (первый лист). Внешние библиотеки не нужны:
.xlsx читается встроенными zipfile и xml, поэтому скрипты работают и на
телефоне (a-Shell, Termux).

Заголовки колонок сопоставляются без учёта регистра, пробелов и знаков
препинания, у каждой колонки есть несколько допустимых названий (см. SCHEMAS).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------- схемы таблиц
# ключ → (тип, обязательна ли, допустимые заголовки)
#   тип: "s" — текст, "n" — число, "d" — дата
SCHEMAS: dict[str, dict[str, tuple[str, bool, tuple[str, ...]]]] = {
    # остатки на счетах и в кассе на конец дня
    "balances": {
        "date": ("d", True, ("Дата",)),
        "account": ("s", True, ("Счёт", "Счет", "Касса/счёт", "Банк")),
        "amount": ("n", True, ("Остаток", "Остаток, руб.", "Сумма", "Остаток на конец")),
    },
    # движение денег: выписка банка и касса
    "payments": {
        "date": ("d", True, ("Дата",)),
        "account": ("s", False, ("Счёт", "Счет", "Банк")),
        "counterparty": ("s", False, ("Контрагент", "Плательщик/получатель")),
        "article": ("s", False, ("Статья", "Статья ДДС", "Статья движения")),
        "income": ("n", False, ("Приход", "Поступление", "Поступило")),
        "expense": ("n", False, ("Расход", "Списание", "Списано", "Оплата")),
        "purpose": ("s", False, ("Назначение", "Назначение платежа", "Комментарий")),
    },
    # дебиторская и кредиторская задолженность по документам
    "debts": {
        "kind": ("s", True, ("Вид", "Тип", "Вид долга")),
        "counterparty": ("s", True, ("Контрагент",)),
        "doc": ("s", False, ("Документ", "Счёт/накладная", "Основание")),
        "doc_date": ("d", False, ("Дата документа", "Дата")),
        "due": ("d", True, ("Срок оплаты", "Оплатить до", "Срок")),
        "amount": ("n", True, ("Сумма", "Сумма документа", "Сумма, руб.")),
        "paid": ("n", False, ("Оплачено", "Оплата", "Погашено")),
        "comment": ("s", False, ("Комментарий", "Примечание")),
    },
    # заказы покупателей
    "orders": {
        "number": ("s", True, ("Номер", "Номер заказа", "Заказ")),
        "date": ("d", True, ("Дата", "Дата заказа")),
        "client": ("s", True, ("Клиент", "Покупатель", "Контрагент")),
        "product": ("s", True, ("Продукт", "Продукция", "Номенклатура")),
        "qty": ("n", True, ("Количество, т", "Количество", "Кол-во, т", "Объём, т")),
        "price": ("n", False, ("Цена, руб./т", "Цена", "Цена за т")),
        "ship_by": ("d", False, ("Срок отгрузки", "Отгрузить до", "Дата отгрузки")),
        "shipped": ("n", False, ("Отгружено, т", "Отгружено")),
        "status": ("s", False, ("Статус", "Состояние")),
    },
    # отгрузки по заказам (необязательно: если есть, «Отгружено» берётся отсюда)
    "shipments": {
        "date": ("d", True, ("Дата", "Дата отгрузки")),
        "order": ("s", True, ("Номер заказа", "Заказ", "Номер")),
        "qty": ("n", True, ("Количество, т", "Количество", "Отгружено, т")),
    },
    # выпуск по сменам
    "output": {
        "date": ("d", True, ("Дата",)),
        "shift": ("s", False, ("Смена",)),
        "product": ("s", True, ("Продукт", "Продукция")),
        "batch": ("s", False, ("Партия", "Номер партии")),
        "plan": ("n", False, ("План, т", "План")),
        "fact": ("n", True, ("Факт, т", "Факт", "Выпуск, т", "Выпущено, т")),
        "defect": ("n", False, ("Брак, т", "Брак")),
    },
    # план выпуска на месяц
    "plan": {
        "month": ("s", True, ("Месяц", "Период")),
        "product": ("s", True, ("Продукт", "Продукция")),
        "plan": ("n", True, ("План, т", "План")),
    },
    # остатки сырья
    "stock": {
        "material": ("s", True, ("Материал", "Сырьё", "Сырье", "Номенклатура")),
        "unit": ("s", False, ("Ед.", "Ед", "Единица", "Ед. изм.")),
        "qty": ("n", True, ("Остаток", "Количество", "Остаток на складе")),
        "min": ("n", False, ("Мин. запас", "Минимальный запас", "Страховой запас")),
        "transit": ("n", False, ("В пути", "Заказано", "Ожидается")),
        "arrival": ("d", False, ("Дата поставки", "Ожидается дата", "Поставка")),
        "price": ("n", False, ("Цена, руб./ед.", "Цена", "Цена за ед.")),
        "lead": ("n", False, ("Срок поставки, дней", "Срок поставки", "Срок, дн.")),
        "supplier": ("s", False, ("Поставщик",)),
    },
    # нормы расхода сырья на 1 т продукции (общие для всех отчётов)
    "norms": {
        "product": ("s", True, ("Продукт", "Продукция")),
        "material": ("s", True, ("Материал", "Сырьё", "Сырье")),
        "norm": ("n", True, ("Норма на 1 т", "Норма", "Расход на 1 т")),
        "unit": ("s", False, ("Ед.", "Ед", "Единица")),
    },
    # остатки готовой продукции
    "finished": {
        "product": ("s", True, ("Продукт", "Продукция")),
        "qty": ("n", True, ("Остаток, т", "Остаток", "Количество, т")),
    },
}


class DataError(Exception):
    """Ошибка во входной таблице — текст понятен человеку, который её готовил."""


@dataclass
class Table:
    name: str
    rows: list[dict] = field(default_factory=list)
    path: Path | None = None
    columns: set[str] = field(default_factory=set)  # какие необязательные колонки нашлись

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)

    def has(self, key: str) -> bool:
        return key in self.columns


# --------------------------------------------------------------- разбор значений
_SPACES = re.compile(r"[\s   ']")


def parse_num(v) -> float | None:
    """«1 234,56», «1234.56», «-1 000», «12%», «₽ 5 000» → число; пусто и «—» → None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _SPACES.sub("", str(v)).replace("₽", "").replace("руб.", "").replace("руб", "").rstrip("%")
    s = s.replace("−", "-").replace("–", "-")
    if s in ("", "-", "—"):
        return None
    if "," in s and "." in s:  # 1.234,56 или 1,234.56
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"не число: «{v}»") from None


_EXCEL_EPOCH = dt.date(1899, 12, 30)


def parse_date(v) -> dt.date | None:
    """«24.11.2026», «24.11.26», «2026-11-24», «24.11.2026 9:30», номер дня Excel → дата."""
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)):
        return _EXCEL_EPOCH + dt.timedelta(days=int(v))
    s = str(v).strip()
    if not s or s in ("-", "—"):
        return None
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", s)
    if m:
        d, mo, y = map(int, m.groups())
        if y < 100:
            y += 2000
        return dt.date(y, mo, d)
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return dt.date(*map(int, m.groups()))
    if re.fullmatch(r"\d{5}(\.\d+)?", s):
        return _EXCEL_EPOCH + dt.timedelta(days=int(float(s)))
    raise ValueError(f"не дата: «{v}»")


def norm_header(s: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", str(s).lower().replace("ё", "е"))


def norm_name(s: str) -> str:
    """Для сопоставления названий продуктов и материалов между таблицами."""
    return " ".join(str(s or "").lower().replace("ё", "е").split())


# ------------------------------------------------------------------ чтение файлов
def _read_csv_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def read_csv(path: Path) -> list[list[str]]:
    text = _read_csv_text(path)
    first = text.split("\n", 1)[0]
    delim = max(";,\t", key=first.count) if any(c in first for c in ";,\t") else ";"
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path: Path) -> list[list]:
    """Первый лист .xlsx без openpyxl. Числа возвращаются числами, текст — текстом."""
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise DataError(f"{path.name}: в файле нет листов")
        first = "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in sheets else sheets[0]
        root = ET.fromstring(z.read(first))
    rows: list[list] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, object] = {}
        for c in row.findall("m:c", _NS):
            t = c.get("t")
            v = c.find("m:v", _NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
            elif t in ("str", "b", "e"):
                val = v.text if v is not None else ""
            elif v is not None and v.text is not None:
                f = float(v.text)
                val = int(f) if f.is_integer() else f
            else:
                val = ""
            cells[_col_index(c.get("r"))] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def find_file(folder: Path, name: str) -> Path | None:
    for ext in (".csv", ".xlsx", ".CSV", ".XLSX"):
        p = folder / f"{name}{ext}"
        if p.exists():
            return p
    return None


def load(folder: Path, name: str, required: bool = False) -> Table:
    """Прочитать таблицу `name` из папки (name.csv или name.xlsx) по схеме SCHEMAS[name]."""
    schema = SCHEMAS[name]
    path = find_file(folder, name)
    if path is None:
        if required:
            raise DataError(f"нет файла {name}.csv или {name}.xlsx в папке {folder}")
        return Table(name)
    raw = read_xlsx(path) if path.suffix.lower() == ".xlsx" else read_csv(path)
    raw = [r for r in raw if any(str(c).strip() for c in r)]
    if not raw:
        return Table(name, path=path)

    header = [norm_header(h) for h in raw[0]]
    idx: dict[str, int] = {}
    for key, (_, _, aliases) in schema.items():
        for a in aliases:
            if norm_header(a) in header:
                idx[key] = header.index(norm_header(a))
                break
    missing = [aliases[0] for key, (_, req, aliases) in schema.items() if req and key not in idx]
    if missing:
        raise DataError(f"{path.name}: нет колонок {', '.join('«' + m + '»' for m in missing)}. "
                        f"Первая строка файла должна быть заголовком.")

    rows = []
    for line_no, r in enumerate(raw[1:], start=2):
        rec = {}
        for key, (typ, req, aliases) in schema.items():
            v = r[idx[key]] if key in idx and idx[key] < len(r) else None
            try:
                if typ == "n":
                    v = parse_num(v)
                elif typ == "d":
                    v = parse_date(v)
                else:
                    v = str(v).strip() if v is not None else ""
            except ValueError as e:
                raise DataError(f"{path.name}, строка {line_no}, «{aliases[0]}»: {e}") from None
            if req and v in (None, ""):
                if all(not str(c).strip() for c in r):
                    break
                raise DataError(f"{path.name}, строка {line_no}: не заполнено «{aliases[0]}»")
            rec[key] = v
        else:
            rows.append(rec)
    return Table(name, rows, path, set(idx))


# --------------------------------------------------------------- форматирование
NBSP = " "
THIN = " "


def fmt_num(v: float | None, digits: int = 0) -> str:
    if v is None:
        return "—"
    s = f"{abs(v):,.{digits}f}".replace(",", THIN).replace(".", ",")
    return ("−" if v < 0 and round(abs(v), digits) else "") + s


def fmt_money(v: float | None, short: bool = False) -> str:
    """Рубли: 12 345 678 ₽ или (short) 12,3 млн ₽."""
    if v is None:
        return "—"
    if short:
        a = abs(v)
        if a >= 1e9:
            return fmt_num(v / 1e9, 2) + NBSP + "млрд" + NBSP + "₽"
        if a >= 1e6:
            return fmt_num(v / 1e6, 1) + NBSP + "млн" + NBSP + "₽"
        if a >= 1e4:
            return fmt_num(v / 1e3, 0) + NBSP + "тыс." + NBSP + "₽"
    return fmt_num(v, 0) + NBSP + "₽"


def fmt_t(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else fmt_num(v, digits) + NBSP + "т"


def fmt_pct(v: float | None, digits: int = 0, signed: bool = False) -> str:
    if v is None:
        return "—"
    s = fmt_num(v, digits) + NBSP + "%"
    return ("+" + s) if signed and v > 0 else s


def fmt_signed(v: float | None, f=fmt_num) -> str:
    if v is None:
        return "—"
    return ("+" if v > 0 else "") + f(v)


MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
              "сентябрь", "октябрь", "ноябрь", "декабрь"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def fmt_date(d: dt.date | None, long: bool = False) -> str:
    if d is None:
        return "—"
    if long:
        return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}, {WEEKDAYS[d.weekday()]}"
    return d.strftime("%d.%m.%Y")


def fmt_dm(d: dt.date | None) -> str:
    return "—" if d is None else d.strftime("%d.%m")


def plural(n: float, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def month_key(v) -> str | None:
    """«11.2026», «2026-11», «ноябрь 2026», дата → «2026-11»."""
    if isinstance(v, (dt.date, dt.datetime)):
        return f"{v.year:04d}-{v.month:02d}"
    s = str(v or "").strip().lower()
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})", s)
    if m:
        return f"{int(m[1]):04d}-{int(m[2]):02d}"
    m = re.fullmatch(r"(\d{1,2})[-./](\d{4})", s)
    if m:
        return f"{int(m[2]):04d}-{int(m[1]):02d}"
    for i, name in enumerate(MONTHS_NOM):
        if s.startswith(name[:3]):
            y = re.search(r"\d{4}", s)
            if y:
                return f"{y.group()}-{i + 1:02d}"
    try:
        d = parse_date(s)
        return f"{d.year:04d}-{d.month:02d}" if d else None
    except ValueError:
        return None
