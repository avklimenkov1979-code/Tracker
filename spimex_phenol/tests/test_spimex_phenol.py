import datetime as dt
import io
import re
import sys
from pathlib import Path

import xlwt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import spimex_phenol as sp  # noqa: E402

PATTERN = re.compile(sp.DEFAULT_PATTERN, re.IGNORECASE)

HEADER = [
    "Код\nИнструмента", "Наименование\nИнструмента", "Базис\nпоставки",
    "Объем\nДоговоров\nв единицах\nизмерения", "Обьем\nДоговоров,\nруб.",
    "Изменение рыночной\nцены к цене\nпредыдущего\nдня", "Минимальная\nцена", "Средняя\nцена",
    "Максимальная\nцена", "Рыночная\nцена", "Цена в Заявках\n(за единицу\nизмерения)\nлучшая\nпокупка",
    "лучшая\nпродажа", "Количество\nДоговоров,\nшт.",
]


def make_xls() -> bytes:
    wb = xlwt.Workbook()
    ws = wb.add_sheet("TRADE_SUMMARY")
    rows = [
        ["Бюллетень по итогам торгов"],
        ["Дата торгов: 05.10.2026"],
        ["Единица измерения: Метрическая тонна"],
        HEADER,
        ["A592ANK065F", "Бензин (АИ-92-К5), ст. Новокуйбышевская", "ст. Новокуйбышевская",
         "60", "3 900 000", "150", "65000", "65000", "65000", "65000", "64900", "65100", "1"],
        ["PHNL-KST1", "Фенол, Кстово (ст. отправления)", "ст. Кстово",
         "120", "12000000", "-500", "99000", "100000", "101000", "100000", "98500", "-", "3"],
        ["PHRS-XYZ", "Фенольная смола", "ст. Где-то", "1", "1", "-", "1", "1", "1", "1", "-", "-", "1"],
        ["Итого:", "", "", "181"],
        [],
        ["Единица измерения: Килограмм"],
        HEADER,
        ["PHNL-SMR2", "ФЕНОЛ синтетический, ст. Новокуйбышевская", "ст. Новокуйбышевская",
         "-", "-", "-", "-", "-", "-", "102500", "101000", "103000", "-"],
    ]
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_bulletin_finds_only_phenol():
    b = sp.parse_bulletin(make_xls(), "https://spimex.com/upload/reports/oil_xls/oil_xls_20261005162000.xls", PATTERN)
    assert b.date == dt.date(2026, 10, 5)
    assert [r["code"] for r in b.rows] == ["PHNL-KST1", "PHNL-SMR2"]
    r = b.rows[0]
    assert r["price_avg"] == 100000
    assert r["price_market"] == 100000
    assert r["price_change"] == -500
    assert r["volume_rub"] == 12000000
    assert r["bid_best"] == 98500
    assert r["ask_best"] is None
    assert r["deals"] == 3
    assert r["source"] == "oil_xls_20261005162000.xls"
    assert b.rows[1]["price_avg"] is None and b.rows[1]["price_market"] == 102500


def test_find_links_and_dates():
    html = '''<a href="/upload/reports/oil_xls/oil_xls_20261005162000.xls?r=8513">x</a>
              <a href="/upload/reports/oil_xls/oil_xls_20261005162000.xls?r=8513">dup</a>
              <a href="/upload/reports/pdf/oil_20261005.pdf">pdf</a>
              <a href='/upload/reports/oil_xls/oil_xls_20261002162000.xlsx'>y</a>'''
    links = sp.find_bulletin_links(html, "https://spimex.com/markets/oil_products/trades/results/")
    assert [b.url for b in links] == [
        "https://spimex.com/upload/reports/oil_xls/oil_xls_20261005162000.xls?r=8513",
        "https://spimex.com/upload/reports/oil_xls/oil_xls_20261002162000.xlsx",
    ]
    assert [b.date for b in links] == [dt.date(2026, 10, 5), dt.date(2026, 10, 2)]


def test_main_with_local_file_is_idempotent(tmp_path):
    f = tmp_path / "oil_xls_20261005162000.xls"
    f.write_bytes(make_xls())
    out = tmp_path / "phenol.csv"
    assert sp.main(["--file", str(f), "--csv", str(out)]) == 0
    assert sp.main(["--file", str(f), "--csv", str(out)]) == 0
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Дата торгов;Код инструмента")
    assert len(lines) == 3
    assert lines[1].startswith("2026-10-05;PHNL-KST1;Фенол, Кстово")
    assert out.with_suffix(".xlsx").exists()
    rows = sp.load_table(out)
    assert rows[0]["code"] == "PHNL-KST1"


def test_list_bulletins_paginates(monkeypatch):
    pages = {
        "R": '<a href="/upload/reports/oil_xls/oil_xls_20261005162000.xls">a</a>'
             '<a href="/upload/reports/oil_xls/oil_xls_20261002162000.xls">b</a>',
        "R?page=page-2": '<a href="/upload/reports/oil_xls/oil_xls_20260920162000.xls">c</a>',
    }

    class Resp:
        def __init__(self, t): self.text = t
        def raise_for_status(self): pass

    class S:
        def get(self, url, timeout): return Resp(pages.get(url, ""))

    latest = sp.list_bulletins(S(), "R", None)
    assert len(latest) == 1 and latest[0].date == dt.date(2026, 10, 5)
    hist = sp.list_bulletins(S(), "R", dt.date(2026, 9, 25))
    assert [b.date for b in hist] == [dt.date(2026, 10, 5), dt.date(2026, 10, 2)]
