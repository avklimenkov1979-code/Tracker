import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import phenol_index as pi  # noqa: E402


def row(code, name, value, change):
    return f'''<div class="indexes-table__row" key="{code}" v-if="filters.place == undefined || filters.place.value.allow.indexOf('{code.lower()}') > -1">
  <div class="indexes-table__cols" data-graph="{code}">
    <div class="indexes-table__col index-code"><span>{code}</span></div>
    <div class="indexes-table__col index-name">{name}</div>
    <div class="indexes-table__col index-value"><span>{value}</span><small>{change}</small><i class="arrow"></i></div>
  </div>
</div>'''


PAGE = f"""
<p>Национальные индикаторы рассчитываются для следующих продуктов:</p><ul><li>Фенол.</li></ul>
<div class="indexes-table">
 <div class="indexes-table__title"><span>Национальные индикаторы оптовых цен продуктов нефтегазохимии с учетом
 вторичного рынка</span> за <span>октябрь 2026 г.</span> (в <span>рублях за тонну</span> <span>с НДС</span>)</div>
 <div class="indexes-table__row indexes-table__head"><div>Код товара</div><div>Наименование товара</div></div>
 {row("ORT", "Ортоксилол", "280&nbsp;331", "+6.04%")}
 {row("STR", "Стирол", "210 754", "+9.98%")}
 {row("FEN", "Фенол", "142 055", "0.00%")}
</div>
<div class="indexes-table">
 <div class="indexes-table__title">Национальные индикаторы оптовых цен без учета вторичного рынка за
 сентябрь 2026 г. (в рублях за тонну без НДС)</div>
 {row("FEN", "Фенол", "118 379,50", "-1,25%")}
</div>
<script>var x = "FEN 999 999";</script>
"""


def test_parse_rows():
    rows = pi.parse_rows(PAGE)
    assert [(r["name"], r["value"], r["change"], r["table"]) for r in rows] == [
        ("Фенол", 142055.0, 0.0, 1), ("Фенол", 118379.5, -1.25, 2)]
    assert "с учетом вторичного рынка" in rows[0]["title"] and "с НДС" in rows[0]["title"]
    assert "без НДС" in rows[1]["title"]


def test_vue_template_rows_are_skipped():
    assert pi.parse_rows(row("FEN", "{{ item.name }}", "{{ item.value }}", "")) == []


def test_main_writes_table_and_overwrites_same_day(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "phenol_index.csv"
    assert pi.main(["--file", str(page), "--csv", str(out)]) == 0
    assert pi.main(["--file", str(page), "--csv", str(out)]) == 0
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Дата сбора;Код;Наименование;Значение")
    assert len(lines) == 3
    assert ";FEN;Фенол;142055;0;" in lines[1]
    assert ";FEN;Фенол;118379,50;-1,25;" in lines[2]
    assert out.with_suffix(".xlsx").exists()
