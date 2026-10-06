#!/usr/bin/env python3
"""Ежедневный сбор индикатора цен на фенол (FEN) с сайта Петербургской биржи.

Дневные бюллетени торгов с 28.09.2026 закрыты (Указ №686), но биржа публикует
индикаторы оптовых цен нефтегазохимии, среди них фенол — код FEN. Скрипт
забирает строку FEN со страницы индикаторов — значение, изменение, значение
за предыдущий день, объём, сумму и количество договоров — и дописывает её
в таблицу (CSV для Excel + копия .xlsx). Повторный запуск в тот же день
перезаписывает строку.

  python phenol_index.py --csv ~/Documents/SPIMEX/phenol_index.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import re
import sys
from pathlib import Path

import requests

log = logging.getLogger("phenol_index")

DEFAULT_URLS = ["https://spimex.com/indexes/petrochem/national/"]
DEFAULT_CODE = "FEN"
DEFAULT_CSV = Path(__file__).resolve().parent.parent / "data" / "phenol_index.csv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

COLUMNS = [
    ("date", "Дата сбора"),
    ("code", "Код"),
    ("name", "Наименование"),
    ("value", "Индикатор, руб./т"),
    ("change", "Изменение, %"),
    ("change_abs", "Изменение, руб."),
    ("prev", "Пред. день, руб./т"),
    ("volume", "Объём, т"),
    ("turnover", "Сумма договоров, руб."),
    ("deals", "Кол-во договоров"),
    ("page", "Страница"),
]
NUMERIC = {"value", "change", "change_abs", "prev", "volume", "turnover", "deals"}

TAG_RE = re.compile(r"<[^>]+>")
ROW_START_RE = re.compile(r"""<div\b[^>]*class=["'][^"']*indexes-table__row""", re.I)
VALUE_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ \xa0 ]\d{3})+(?:[.,]\d+)?|\d{4,}(?:[.,]\d+)?)(?![\d%])")
CHANGE_RE = re.compile(r"([+\-−–]?\s?\d+(?:[.,]\d+)?)\s*%")


def text_of(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.I | re.S)
    return " ".join(TAG_RE.sub(" ", html).replace("&nbsp;", " ").split())


def to_float(s: str) -> float:
    return float(re.sub(r"[ \xa0 ]", "", s).replace("−", "-").replace("–", "-").replace(",", "."))


GROUPED = r"\d{1,3}(?:[ \xa0\u202f]\d{3})*(?:[.,]\d+)?"


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def parse_details(text: str) -> dict:
    """Хвост строки после «+9.98%»: «19126 19 126 191 628 пред. день 220 тонн
    46 365 990 46.37 млн.руб. 7 Количество договоров»."""
    out: dict = {}
    m = re.match(r"\s*([+\-−–]?\d+(?:[.,]\d+)?)\s+(.*?)\s*пред\.?\s*день", text, re.S)
    if m:
        raw, rest = m[1], m[2]
        out["change_abs"] = to_float(raw)
        # за «сырым» числом идёт оно же с разрядами, затем — значение за прошлый день
        need, i = len(digits(raw)), 0
        while need and i < len(rest):
            need -= rest[i].isdigit()
            i += 1
        prev = digits(rest[i:])
        if prev:
            out["prev"] = float(prev)
    m = re.search(r"день\s+(" + GROUPED + r")\s*тонн", text)
    if m:
        out["volume"] = to_float(m[1])
    m = re.search(r"тонн\w*\s+(.*?)\s+(" + GROUPED + r")\s*млн", text)
    if m and digits(m[1]):
        out["turnover"] = to_float(m[1])
    elif m:
        out["turnover"] = to_float(m[2]) * 1e6
    m = re.search(r"млн\.?\s*руб\.?\s+(\d+)\s+Количество", text)
    if m:
        out["deals"] = int(m[1])
    return out


def parse_rows(html: str, code: str = DEFAULT_CODE) -> list[dict]:
    """Строки таблиц индикаторов с нужным кодом товара."""
    starts = [m.start() for m in ROW_START_RE.finditer(html)]
    out = []
    for n, start in enumerate(starts):
        head = html[start:start + 400]
        if not re.search(rf"""key=["']{re.escape(code)}["']|data-graph=["']{re.escape(code)}["']""", head, re.I):
            continue
        end = starts[n + 1] if n + 1 < len(starts) else start + 4000
        row_html = html[start:min(end, start + 4000)]
        row_text = text_of(row_html)
        if "{{" in row_text:
            log.warning("строка %s заполняется скриптом страницы, значений в HTML нет: %s", code, row_text[:200])
            continue
        after_code = row_text.split(code, 1)[-1]
        val = VALUE_RE.search(after_code)
        if not val:
            log.warning("в строке %s не найдено значение: %s", code, row_text[:200])
            continue
        tail = after_code[val.end():]
        chg = CHANGE_RE.search(tail)
        name = after_code[:val.start()].strip(" :–-") or code
        rec = {
            "code": code,
            "name": name,
            "value": to_float(val[1]),
            "change": to_float(chg[1].replace(" ", "")) if chg else None,
            "_text": row_text,
        }
        if chg:
            rec.update(parse_details(tail[chg.end():]))
        out.append(rec)
    return out


def debug_title(html: str, code: str) -> None:
    """Что стоит перед первой строкой с кодом — чтобы настроить чтение заголовка."""
    m = re.search(rf"""key=["']{re.escape(code)}["']""", html)
    if not m:
        print("строка", code, "не найдена")
        return
    before = html[max(0, m.start() - 8000):m.start()]
    print("1) Текст перед строкой (конец):")
    print("  ", text_of(before)[-500:])
    hits = list(re.finditer(r"вторичн|рублях|НДС", before, re.I))
    t = hits[-1] if hits else None
    if t:
        print("2) Разметка вокруг заголовка:")
        print("  ", " ".join(before[max(0, t.start() - 700):t.start() + 500].split())[:1200])
    for word in ("октябр", "сентябр", "selected", "v-model", "period"):
        print(f"3) «{word}» на странице: {len(re.findall(word, html, re.I))} раз")


def load_table(path: Path) -> list[dict]:
    if not path.exists():
        return []
    keys = {title: key for key, title in COLUMNS}
    keys["Значение, руб./т"] = "value"  # заголовок из первой версии таблицы
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{keys.get(k, k): v for k, v in r.items()} for r in csv.DictReader(f, delimiter=";")]


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return (f"{v:.0f}" if v.is_integer() else f"{v:.2f}").replace(".", ",")
    return str(v)


def num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return v


def save(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([t for _, t in COLUMNS])
        for r in rows:
            w.writerow([fmt(r.get(k)) for k, _ in COLUMNS])

    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Фенол"
    ws.append([t for _, t in COLUMNS])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        line = []
        for k, _ in COLUMNS:
            v = r.get(k)
            if k == "date" and v:
                v = dt.date.fromisoformat(str(v))
            elif k in NUMERIC:
                v = num(v)
            line.append(v)
        ws.append(line)
    for (c,) in ws.iter_rows(min_row=2, max_col=1):
        c.number_format = "DD.MM.YYYY"
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=10):
        for c in row:
            c.number_format = "0.00" if c.column == 5 else "#,##0"
    for col, w in zip("ABCDEFGHIJK", (12, 8, 14, 16, 12, 14, 16, 10, 20, 10, 45)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path.with_suffix(".xlsx"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="путь к таблице (по умолчанию: %(default)s)")
    ap.add_argument("--url", action="append", help="страница индикаторов (можно несколько раз)")
    ap.add_argument("--code", default=DEFAULT_CODE, help="код товара (по умолчанию: %(default)s)")
    ap.add_argument("--file", type=Path, help="разобрать сохранённую страницу вместо загрузки")
    ap.add_argument("--debug", action="store_true", help="показать разметку заголовка таблицы и выйти")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    today = dt.date.today().isoformat()
    collected, errors = [], 0
    pages = [(str(args.file), args.file.read_text(encoding="utf-8"))] if args.file else []
    if not args.file:
        session = requests.Session()
        session.headers.update(HEADERS)
        for url in args.url or DEFAULT_URLS:
            try:
                resp = session.get(url, timeout=60)
                resp.raise_for_status()
                pages.append((url, resp.text))
            except requests.RequestException as e:
                log.error("не удалось открыть %s: %s", url, e)
                errors += 1

    if args.debug:
        for url, html in pages:
            debug_title(html, args.code)
        return 0

    for url, html in pages:
        rows = parse_rows(html, args.code)
        if not rows:
            log.warning("%s: строк %s не найдено", url, args.code)
            errors += 1
        for r in rows:
            r.update(date=today, page=url)
            log.info("%s %s = %s руб./т (%s%%), пред. день %s, объём %s т, договоров %s",
                     r["code"], r["name"], fmt(r["value"]), fmt(r.get("change")) or "?",
                     fmt(r.get("prev")) or "?", fmt(r.get("volume")) or "?", fmt(r.get("deals")) or "?")
            log.debug("  текст строки: %s", r.pop("_text"))
            r.pop("_text", None)
            collected.append(r)

    if collected:
        key = lambda r: (str(r.get("date")), str(r.get("page")), str(r.get("code")))
        merged = {key(r): r for r in load_table(args.csv)}
        merged.update({key(r): r for r in collected})
        rows = sorted(merged.values(), key=key)
        save(args.csv, rows)
        log.info("записано строк за %s: %d, всего в таблице: %d (%s)", today, len(collected), len(rows), args.csv)

    # короткая сводка для уведомления (её показывает автоматизация в «Командах»)
    text = summary(collected[0]) if collected else "Фенол: цену получить не удалось — откройте a-Shell"
    print(text)
    try:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        args.csv.with_name("phenol_last.txt").write_text(text + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("не удалось сохранить сводку: %s", e)
    return 1 if errors else 0


def summary(r: dict) -> str:
    """«Фенол 06.10: 142 055 ₽/т (+0,00%, +0 ₽), договоров 0, 0 т»."""
    money = lambda v: f"{v:,.0f}".replace(",", " ")
    signed = lambda v, f: ("+" if v > 0 else "") + f(v)
    day = dt.date.fromisoformat(r["date"]).strftime("%d.%m")
    text = f"Фенол {day}: {money(r['value'])} ₽/т"
    extra = []
    if r.get("change") is not None:
        extra.append(signed(r["change"], lambda v: f"{v:.2f}".replace(".", ",")) + "%")
    if r.get("change_abs") is not None:
        extra.append(signed(r["change_abs"], money) + " ₽")
    if extra:
        text += f" ({', '.join(extra)})"
    if r.get("deals") is not None:
        text += f", договоров {r['deals']}"
    if r.get("volume") is not None:
        text += f", {money(r['volume'])} т"
    return text


if __name__ == "__main__":
    sys.exit(main())
