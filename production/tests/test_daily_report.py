import datetime as dt
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import common  # noqa: E402
import daily_report as dr  # noqa: E402

DAY = dt.date(2026, 11, 24)


def write(folder: Path, name: str, text: str, enc: str = "utf-8-sig") -> None:
    (folder / f"{name}.csv").write_text(text.strip() + "\n", encoding=enc)


def test_parse_num_formats():
    assert common.parse_num("1 234,56") == 1234.56
    assert common.parse_num("1 234 567") == 1234567
    assert common.parse_num("1.234,5") == 1234.5
    assert common.parse_num("1,234.5") == 1234.5
    assert common.parse_num("−500") == -500
    assert common.parse_num("") is None and common.parse_num("—") is None
    with pytest.raises(ValueError):
        common.parse_num("abc")


def test_parse_date_formats():
    assert common.parse_date("24.11.2026") == DAY
    assert common.parse_date("24.11.26") == DAY
    assert common.parse_date("2026-11-24") == DAY
    assert common.parse_date("24.11.2026 9:30:00") == DAY
    assert common.parse_date(46350) == DAY  # номер дня Excel


def test_csv_cp1251_and_aliases(tmp_path):
    write(tmp_path, "stock", "Сырьё,Ед,Количество\nФенол,т,\"38,6\"", enc="cp1251")
    t = common.load(tmp_path, "stock")
    assert t.rows == [{"material": "Фенол", "unit": "т", "qty": 38.6, "min": None, "transit": None, "arrival": None,
                       "price": None, "lead": None, "supplier": ""}]


def test_missing_column_message(tmp_path):
    write(tmp_path, "orders", "Номер;Клиент\n1;А")
    with pytest.raises(common.DataError, match="Дата"):
        common.load(tmp_path, "orders")


def test_xlsx_reader(tmp_path):
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>Продукт</t></is></c><c r="B1" t="s"><v>0</v></c></row>'
             '<row r="2"><c r="A2" t="inlineStr"><is><t>Смола</t></is></c><c r="B2"><v>12.5</v></c></row>'
             '</sheetData></worksheet>')
    shared = ('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Остаток, т</t></si></sst>')
    with zipfile.ZipFile(tmp_path / "finished.xlsx", "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", shared)
    t = common.load(tmp_path, "finished")
    assert t.rows == [{"product": "Смола", "qty": 12.5}]


@pytest.fixture
def mini(tmp_path):
    write(tmp_path, "balances", "Дата;Счёт;Остаток\n23.11.2026;Банк;1000000\n24.11.2026;Банк;800000")
    write(tmp_path, "payments", "Дата;Счёт;Контрагент;Статья;Приход;Расход\n24.11.2026;Банк;Поставщик;Сырьё;;200000")
    write(tmp_path, "debts", """Вид;Контрагент;Документ;Срок оплаты;Сумма;Оплачено
Дебиторка;Клиент А;Р1;01.10.2026;500000;100000
Дебиторка;Клиент Б;Р2;26.11.2026;300000;
Кредиторка;Поставщик;П1;25.11.2026;1500000;""")
    write(tmp_path, "orders", """Номер;Дата;Клиент;Продукт;Количество, т;Цена, руб./т;Срок отгрузки
1;01.11.2026;Клиент А;Смола;20;100000;20.11.2026
2;24.11.2026;Клиент Б;Смола;10;100000;30.11.2026""")
    write(tmp_path, "shipments", "Дата;Номер заказа;Количество, т\n20.11.2026;1;5")
    write(tmp_path, "output", "Дата;Продукт;План, т;Факт, т;Брак, т\n23.11.2026;Смола;10;10;0\n24.11.2026;Смола;10;8;0,5")
    write(tmp_path, "plan", "Месяц;Продукт;План, т\n11.2026;Смола;300")
    write(tmp_path, "norms", "Продукт;Материал;Норма на 1 т\nСмола;Фенол;1")
    write(tmp_path, "stock", "Материал;Ед.;Остаток;Мин. запас;Срок поставки, дней\nФенол;т;15;5;5")
    write(tmp_path, "finished", "Продукт;Остаток, т\nСмола;4")
    write(tmp_path, "settings", "Параметр;Значение\nНеснижаемый остаток, руб.;100000\nГоризонт прогноза, дней;3")
    return tmp_path


def test_build_mini(mini):
    data = dr.Data.load(mini)
    s = dr.Settings.load(mini)
    assert s.min_cash == 100000 and s.horizon == 3
    r = dr.build(data, DAY, s)

    m = r["money"]
    assert m["total"] == 800000 and m["total_prev"] == 1000000
    assert m["expense_sum"] == 200000
    # 25.11 платим 1,5 млн → разрыв
    assert m["forecast"][0]["balance"] == -700000
    assert m["low"]["date"] == dt.date(2026, 11, 25)
    assert m["forecast"][1]["balance"] == -400000  # 26.11 вернули 300 тыс.

    d = r["debts"]
    assert d["recv"]["total"] == 700000 and d["recv"]["overdue"] == 400000
    assert d["recv"]["aging"][2]["sum"] == 400000  # 54 дня просрочки → 31–60

    o = r["orders"]
    assert o["backlog_t"] == 25  # 15 по заказу 1 + 10 по заказу 2
    assert [x["number"] for x in o["overdue"]] == ["1"]
    assert len(o["new"]) == 1

    out = r["output"]
    assert out["total"]["fact"] == 8 and out["total"]["plan"] == 10
    assert out["total"]["fact_mtd"] == 18

    fen = r["materials"]["items"][0]
    assert fen["daily"] > 0 and fen["status"] == "crit"  # хватит меньше чем на 5 дней поставки

    levels = {(a.level, a.section) for a in r["alerts"]}
    assert ("crit", "money") in levels and ("crit", "orders") in levels and ("crit", "materials") in levels


def test_balance_without_today_row_uses_payments(tmp_path):
    write(tmp_path, "balances", "Дата;Счёт;Остаток\n22.11.2026;Банк;1000")
    write(tmp_path, "payments", "Дата;Счёт;Контрагент;Приход;Расход\n23.11.2026;Банк;А;500;\n24.11.2026;Банк;Б;;200")
    r = dr.build(dr.Data.load(tmp_path), DAY, dr.Settings())
    assert r["money"]["total"] == 1300 and r["money"]["total_prev"] == 1500


def test_transfers_not_counted(tmp_path):
    write(tmp_path, "payments", """Дата;Счёт;Контрагент;Статья;Приход;Расход
24.11.2026;Касса;Банк;Пополнение кассы;100;
24.11.2026;Банк;Касса;Пополнение кассы;;100
24.11.2026;Банк;Клиент;Оплата;700;""")
    r = dr.build(dr.Data.load(tmp_path), DAY, dr.Settings())
    assert r["money"]["income_sum"] == 700 and r["money"]["expense_sum"] == 0
    assert len(r["money"]["transfers"]) == 2


def test_demo_renders(tmp_path):
    assert dr.main(["--demo", "--out", str(tmp_path)]) == 0
    page = (tmp_path / "daily_2026-11-24.html").read_text(encoding="utf-8")
    for title in ("Деньги", "Долги", "Заказы и отгрузки", "Выпуск продукции", "Сырьё и нехватка", "Фенол на бирже"):
        assert title in page
    line = (tmp_path / "daily_last.txt").read_text(encoding="utf-8")
    assert line.startswith("Отчёт 24.11") and "нехватка" in line


def test_empty_folder_is_error(tmp_path):
    assert dr.main(["--data", str(tmp_path), "--out", str(tmp_path / "r")]) == 2
