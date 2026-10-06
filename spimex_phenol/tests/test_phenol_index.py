import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import phenol_index as pi  # noqa: E402


def row(code, name, value, change, raw, fmt_abs, prev, vol, rub, mln, deals):
    return f'''<div class="indexes-table__row" key="{code}" v-if="filters.place == undefined || filters.place.value.allow.indexOf('{code.lower()}') > -1">
  <div class="indexes-table__cols" data-graph="{code}">
    <div class="indexes-table__col index-code"><span>{code}</span></div>
    <div class="indexes-table__col index-name">{name}</div>
    <div class="indexes-table__col index-value"><span>{value}</span><small>{change}</small>
      <svg><line stroke-miterlimit="10" stroke-width="3" x1="2" x2="20.5" y1="12" y2="12"/></svg></div>
    <div class="indexes-table__col"><span data-raw="1">{raw}</span> <span>{fmt_abs}</span></div>
    <div class="indexes-table__col"><b>{prev}</b> пред. день</div>
    <div class="indexes-table__col">{vol} тонн</div>
    <div class="indexes-table__col">{rub} <span>{mln} млн.руб.</span></div>
    <div class="indexes-table__col">{deals} <span>Количество договоров</span></div>
    <div class="loader">Загрузка</div>
  </div>
</div>'''


PAGE = f"""
<p>Национальные индикаторы рассчитываются для следующих продуктов:</p><ul><li>Фенол.</li></ul>
<div class="indexes-table">
 {row("TOL", "Толуол", "172 008", "-6.07%", "-11107", "-11 107", "183 115", "315", "54 182 457", "54.18", "5")}
 {row("STR", "Стирол", "210 754", "+9.98%", "19126", "19 126", "191 628", "220", "46 365 990", "46.37", "7")}
 {row("FEN", "Фенол", "142&nbsp;055", "0.00%", "0", "0", "142 055", "0", "0", "0.00", "0")}
</div>
<script>var x = "FEN 999 999";</script>
"""


def test_parse_rows_with_details():
    (fen,) = pi.parse_rows(PAGE)
    assert (fen["name"], fen["value"], fen["change"], fen["change_abs"], fen["prev"]) == (
        "Фенол", 142055.0, 0.0, 0.0, 142055.0)
    assert (fen["volume"], fen["deals"]) == (0.0, 0)
    (tol,) = pi.parse_rows(PAGE, "TOL")
    assert (tol["value"], tol["change"], tol["change_abs"], tol["prev"]) == (172008.0, -6.07, -11107.0, 183115.0)
    assert (tol["volume"], tol["turnover"], tol["deals"]) == (315.0, 54182457.0, 5)
    (st,) = pi.parse_rows(PAGE, "STR")
    assert (st["change_abs"], st["prev"], st["volume"], st["turnover"], st["deals"]) == (
        19126.0, 191628.0, 220.0, 46365990.0, 7)


def test_vue_template_rows_are_skipped():
    assert pi.parse_rows(row("FEN", "{{ item.name }}", "{{ item.value }}", "", "", "", "", "", "", "", "")) == []


def test_main_writes_table_and_overwrites_same_day(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "phenol_index.csv"
    assert pi.main(["--file", str(page), "--csv", str(out)]) == 0
    assert pi.main(["--file", str(page), "--csv", str(out)]) == 0
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Дата сбора;Код;Наименование;Индикатор, руб./т;Изменение, %")
    assert len(lines) == 2
    assert ";FEN;Фенол;142055;0;0;142055;0;0;0;" in lines[1]
    assert out.with_suffix(".xlsx").exists()


def test_old_table_columns_are_tolerated(tmp_path):
    out = tmp_path / "phenol_index.csv"
    out.write_text("Дата сбора;Код;Наименование;Значение, руб./т;Изменение, %;Индикатор;№ таблицы;Страница\n"
                   "2026-10-05;FEN;Фенол;142055;0;таблица 1;1;https://spimex.com/indexes/petrochem/national/\n",
                   encoding="utf-8-sig")
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    assert pi.main(["--file", str(page), "--csv", str(out)]) == 0
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 3 and lines[1].startswith("2026-10-05;FEN;Фенол;142055;0;")
