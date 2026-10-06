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
    assert [b.date for b in latest] == [dt.date(2026, 10, 5), dt.date(2026, 10, 2)]
    hist = sp.list_bulletins(S(), "R", dt.date.today() - dt.timedelta(days=5))
    assert len(hist) == len([d for d in (dt.date(2026, 10, 5), dt.date(2026, 10, 2))
                              if d >= dt.date.today() - dt.timedelta(days=5)])
    hist = sp.list_bulletins(S(), "R", dt.date(2026, 9, 25))
    assert [b.date for b in hist] == [dt.date(2026, 10, 5), dt.date(2026, 10, 2)]


FILES_PAGE = """
<table>
<tr><td>05.10.2026</td><td>Бюллетень по итогам торгов</td>
  <td><a href="/files/61309/" class="xls">XLS</a> <a href="/files/61308/">PDF</a></td></tr>
<tr><td>02.10.2026</td><td>Бюллетень по итогам торгов</td>
  <td><a href="/files/60659/">XLS</a> <a href="/files/60658/">PDF</a></td></tr>
</table>
<a href="/files/12/">Ежемесячная статистика по итогам торгов</a>
"""


def test_find_files_links():
    links = sp.find_bulletin_links(FILES_PAGE, "https://spimex.com/markets/oil_products/trades/results/")
    assert [sp.source_key(b.url) for b in links] == ["61309", "61308", "60659", "60658"]
    assert links[0].url == "https://spimex.com/files/61309/"
    assert [b.date for b in links[:4]] == [dt.date(2026, 10, 5)] * 2 + [dt.date(2026, 10, 2)] * 2


class FakeResp:
    status_code = 200
    def __init__(self, body):
        self.body = body
        self.text = body if isinstance(body, str) else ""

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def fake_site(monkeypatch, files):
    calls = []

    class Sess:
        def get(self, url, timeout, stream=False):
            calls.append(url)
            if url.endswith("/results/"):
                return FakeResp(FILES_PAGE)
            return FakeResp(files.get(sp.source_key(url), b"%PDF-1.4 ..."))

    monkeypatch.setattr(sp, "make_session", lambda: Sess())
    return calls


def test_main_downloads_latest_files_bulletin(monkeypatch, tmp_path):
    calls = fake_site(monkeypatch, {"61308": make_xls()})  # Excel оказался вторым, первый — PDF
    out = tmp_path / "p.csv"
    assert sp.main(["--csv", str(out)]) == 0
    rows = sp.load_table(out)
    assert [r["code"] for r in rows] == ["PHNL-KST1", "PHNL-SMR2"]
    assert rows[0]["source"] == "61308"
    assert calls[1:] == ["https://spimex.com/files/61309/", "https://spimex.com/files/61308/"]
    # повторный запуск: PDF помнится как обработанный, бюллетень уже в таблице
    calls.clear()
    assert sp.main(["--csv", str(out)]) == 0
    assert calls[1:] == []
    assert "skip:61309" in (tmp_path / "p.processed.txt").read_text()


def test_inspect_prints_groups(monkeypatch, capsys):
    fake_site(monkeypatch, {})
    assert sp.main(["--inspect"]) == 0
    out = capsys.readouterr().out
    assert "/files/N/" in out and "Бюллетень по итогам торгов" in out
