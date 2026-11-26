#!/usr/bin/env python3
"""Ежедневный отчёт руководителю: деньги, долги, заказы, выпуск и нехватка сырья.

Читает таблицы из папки с данными (CSV или .xlsx, см. README) и собирает один
HTML-отчёт на дату, плюс короткую сводку в одну строку для уведомления.

  python production/daily_report.py --data production/data
  python production/daily_report.py --demo                # на демо-данных
  python production/daily_report.py --data ... --date 24.11.2026

Результат: reports/daily_ГГГГ-ММ-ДД.html и daily_last.txt рядом с ним.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import html
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DataError, Table, fmt_date, fmt_dm, fmt_money, fmt_num, fmt_pct, fmt_signed,  # noqa: E402
                    fmt_t, load, month_key, norm_name, parse_num, plural, read_csv, WD_SHORT, MONTHS_NOM)

log = logging.getLogger("daily_report")
HERE = Path(__file__).resolve().parent
TRANSFER_WORDS = ("перемещ", "пополнение кассы", "внутренн", "между счет", "сдача наличн", "снятие наличн")


# ================================================================== настройки
@dataclass
class Settings:
    company: str = ""
    horizon: int = 7            # дней вперёд для прогноза денег, заказов и сырья
    days_per_week: int = 7      # 7 — непрерывное производство, 5 — пн–пт
    defect_norm: float = 1.5    # допустимый брак, %
    big_payment: float = 3e6    # крупный платёж — показывается отдельно
    min_cash: float = 0.0       # неснижаемый остаток денег
    reorder_reserve: int = 3    # запас в днях сверх срока поставки

    KEYS = {
        "предприятие": ("company", str), "компания": ("company", str),
        "горизонтпрогнозадней": ("horizon", int), "горизонтпрогноза": ("horizon", int),
        "рабочихднейвнеделю": ("days_per_week", int),
        "допустимыйбрак": ("defect_norm", float), "нормабрака": ("defect_norm", float),
        "крупныйплатежотруб": ("big_payment", float), "крупныйплатеж": ("big_payment", float),
        "неснижаемыйостатокруб": ("min_cash", float), "неснижаемыйостаток": ("min_cash", float),
        "резервзапасадней": ("reorder_reserve", int), "страховойзапасдней": ("reorder_reserve", int),
    }

    @classmethod
    def load(cls, folder: Path) -> "Settings":
        s = cls()
        p = folder / "settings.csv"
        if not p.exists():
            return s
        import re
        for row in read_csv(p)[1:]:
            if len(row) < 2 or not row[0].strip():
                continue
            key = re.sub(r"[^0-9a-zа-я]", "", row[0].lower().replace("ё", "е"))
            if key in cls.KEYS:
                attr, typ = cls.KEYS[key]
                val = row[1].strip()
                if typ is str:
                    setattr(s, attr, val)
                else:
                    num = parse_num(val)
                    if num is not None:
                        setattr(s, attr, typ(num))
        return s


def is_workday(d: dt.date, s: Settings) -> bool:
    return s.days_per_week >= 7 or d.weekday() < s.days_per_week


def workdays(a: dt.date, b: dt.date, s: Settings) -> int:
    """Рабочих дней в интервале [a, b]."""
    return sum(1 for i in range((b - a).days + 1) if is_workday(a + dt.timedelta(i), s))


# ================================================================== данные
@dataclass
class Data:
    balances: Table
    payments: Table
    debts: Table
    orders: Table
    shipments: Table
    output: Table
    plan: Table
    stock: Table
    norms: Table
    finished: Table
    phenol: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, folder: Path, phenol_csv: Path | None = None) -> "Data":
        names = ["balances", "payments", "debts", "orders", "shipments", "output", "plan", "stock", "norms", "finished"]
        tables = {n: load(folder, n) for n in names}
        if not any(len(t) for t in tables.values()):
            raise DataError(f"в папке {folder} нет ни одной таблицы с данными (balances.csv, orders.csv, …)")
        phenol = []
        for p in [phenol_csv, folder / "phenol_index.csv", HERE.parent / "data" / "phenol_index.csv"]:
            if p and p.exists():
                phenol = load_phenol(p)
                break
        return cls(**tables, phenol=phenol)

    def latest_date(self) -> dt.date | None:
        ds = [r["date"] for t in (self.output, self.payments, self.balances, self.shipments) for r in t if r.get("date")]
        return max(ds) if ds else None


def load_phenol(path: Path) -> list[dict]:
    """Таблица индикатора фенола, которую ведёт spimex_phenol/phenol_index.py."""
    rows = read_csv(path)
    if not rows:
        return []
    head = rows[0]
    try:
        i_date, i_val = head.index("Дата сбора"), head.index("Индикатор, руб./т")
    except ValueError:
        return []
    out = []
    for r in rows[1:]:
        try:
            d = dt.date.fromisoformat(r[i_date].strip())
            v = parse_num(r[i_val])
        except (ValueError, IndexError):
            continue
        if v:
            out.append({"date": d, "value": v})
    return sorted(out, key=lambda r: r["date"])


# ================================================================== расчёты
def is_transfer(p: dict, accounts: set[str]) -> bool:
    text = f"{p.get('article', '')} {p.get('purpose', '')}".lower()
    return norm_name(p.get("counterparty")) in accounts or any(w in text for w in TRANSFER_WORDS)


def calc_money(data: Data, day: dt.date, s: Settings, debts: dict) -> dict:
    accounts = sorted({r["account"] for r in data.balances} | {r["account"] for r in data.payments if r.get("account")})
    acc_norm = {norm_name(a) for a in accounts}

    def balance_on(acc: str, d: dt.date) -> float | None:
        known = [r for r in data.balances if r["account"] == acc and r["date"] <= d]
        if not known and not data.balances.rows:
            return None
        last = max(known, key=lambda r: r["date"]) if known else None
        base, since = (last["amount"], last["date"]) if last else (0.0, dt.date.min)
        moves = sum((p.get("income") or 0) - (p.get("expense") or 0)
                    for p in data.payments if p.get("account") == acc and since < p["date"] <= d)
        return base + moves

    acc_rows = []
    for a in accounts:
        now, prev = balance_on(a, day), balance_on(a, day - dt.timedelta(1))
        if now is None:
            continue
        acc_rows.append({"account": a, "balance": now, "prev": prev, "change": None if prev is None else now - prev})
    total = sum(r["balance"] for r in acc_rows) if acc_rows else None
    total_prev = sum(r["prev"] for r in acc_rows if r["prev"] is not None) if acc_rows else None

    today = [p for p in data.payments if p["date"] == day]
    real = [p for p in today if not is_transfer(p, acc_norm)]
    transfers = [p for p in today if is_transfer(p, acc_norm)]
    income = [p for p in real if (p.get("income") or 0) > 0]
    expense = [p for p in real if (p.get("expense") or 0) > 0]

    month_start = day.replace(day=1)
    mtd = [p for p in data.payments if month_start <= p["date"] <= day and not is_transfer(p, acc_norm)]
    by_article_out, by_article_in = defaultdict(float), defaultdict(float)
    for p in mtd:
        if p.get("expense"):
            by_article_out[p.get("article") or "Без статьи"] += p["expense"]
        if p.get("income"):
            by_article_in[p.get("article") or "Без статьи"] += p["income"]

    # прогноз: остаток + поступления дебиторки по срокам − платежи кредиторки по срокам;
    # просроченную кредиторку считаем к оплате в первый же день, просроченную дебиторку — не ждём
    forecast = []
    bal = total or 0.0
    overdue_pay = sum(r["left"] for r in debts["pay"]["items"] if r["due"] <= day)
    for i in range(1, s.horizon + 1):
        d = day + dt.timedelta(i)
        inflow = sum(r["left"] for r in debts["recv"]["items"] if r["due"] == d)
        outflow = sum(r["left"] for r in debts["pay"]["items"] if r["due"] == d) + (overdue_pay if i == 1 else 0)
        bal += inflow - outflow
        forecast.append({"date": d, "in": inflow, "out": outflow, "balance": bal})
    low = min(forecast, key=lambda r: r["balance"]) if forecast else None
    return {
        "accounts": acc_rows, "total": total, "total_prev": total_prev,
        "income": sorted(income, key=lambda p: -p["income"]), "expense": sorted(expense, key=lambda p: -p["expense"]),
        "income_sum": sum(p["income"] for p in income), "expense_sum": sum(p["expense"] for p in expense),
        "transfers": transfers,
        "mtd_in": sum(by_article_in.values()), "mtd_out": sum(by_article_out.values()),
        "by_article_out": sorted(by_article_out.items(), key=lambda kv: -kv[1]),
        "by_article_in": sorted(by_article_in.items(), key=lambda kv: -kv[1]),
        "forecast": forecast, "low": low, "overdue_pay": overdue_pay,
        "overdue_recv": debts["recv"]["overdue"],
        "has_data": bool(acc_rows or today or data.payments.rows),
    }


AGING = [("Срок не наступил", None, 0), ("1–30 дней", 1, 30), ("31–60 дней", 31, 60), ("61–90 дней", 61, 90), ("Более 90 дней", 91, 10**6)]


def calc_debts(data: Data, day: dt.date, s: Settings) -> dict:
    out = {}
    for kind, words in (("recv", ("дебит", "покуп", "нам долж", "дз")), ("pay", ("кредит", "постав", "мы долж", "кз"))):
        items = []
        for r in data.debts:
            k = r["kind"].lower()
            if not any(w in k for w in words):
                continue
            left = (r["amount"] or 0) - (r.get("paid") or 0)
            if left <= 0.5:
                continue
            late = (day - r["due"]).days
            items.append({**r, "left": left, "late": late})
        items.sort(key=lambda r: (-r["late"], -r["left"]))
        total = sum(r["left"] for r in items)
        overdue = sum(r["left"] for r in items if r["late"] > 0)
        aging = []
        for title, lo, hi in AGING:
            sel = [r for r in items if (r["late"] <= 0 if lo is None else lo <= r["late"] <= hi)]
            aging.append({"title": title, "sum": sum(r["left"] for r in sel), "count": len(sel)})
        by_cp = defaultdict(lambda: {"total": 0.0, "overdue": 0.0, "late": 0, "docs": 0, "next_due": None, "comment": ""})
        for r in items:
            c = by_cp[r["counterparty"]]
            c["total"] += r["left"]
            c["docs"] += 1
            if r["late"] > 0:
                c["overdue"] += r["left"]
                c["late"] = max(c["late"], r["late"])
            elif c["next_due"] is None or r["due"] < c["next_due"]:
                c["next_due"] = r["due"]
            if r.get("comment"):
                c["comment"] = r["comment"]
        counterparties = sorted(({"name": k, **v} for k, v in by_cp.items()), key=lambda c: (-c["overdue"], -c["total"]))
        soon = sorted((r for r in items if 0 <= -r["late"] <= s.horizon), key=lambda r: r["due"])
        out[kind] = {"items": items, "total": total, "overdue": overdue, "aging": aging,
                     "counterparties": counterparties, "soon": soon,
                     "soon_sum": sum(r["left"] for r in soon)}
    out["has_data"] = bool(data.debts.rows)
    return out


def _closed(status: str) -> bool:
    st = (status or "").lower()
    return any(w in st for w in ("отмен", "аннул"))


def avg_output(data: Data, day: dt.date, days: int = 7) -> dict[str, float]:
    """Средний выпуск (т/сутки) по продуктам за последние `days` дней, у которых есть записи."""
    by_day = defaultdict(lambda: defaultdict(float))
    for r in data.output:
        if day - dt.timedelta(days) < r["date"] <= day:
            by_day[r["date"]][norm_name(r["product"])] += r["fact"] or 0
    if not by_day:
        return {}
    res = defaultdict(float)
    for d, prods in by_day.items():
        for p, v in prods.items():
            res[p] += v / len(by_day)
    return dict(res)


def calc_orders(data: Data, day: dt.date, s: Settings) -> dict:
    shipped = defaultdict(float)
    ship_today, ship_mtd = [], []
    order_by_no = {r["number"]: r for r in data.orders}
    for sh in data.shipments:
        if sh["date"] <= day:
            shipped[sh["order"]] += sh["qty"] or 0
        o = order_by_no.get(sh["order"])
        price = (o or {}).get("price") or 0
        rec = {**sh, "client": (o or {}).get("client", "—"), "product": (o or {}).get("product", "—"),
               "sum": (sh["qty"] or 0) * price}
        if sh["date"] == day:
            ship_today.append(rec)
        if day.replace(day=1) <= sh["date"] <= day:
            ship_mtd.append(rec)

    fg = {norm_name(r["product"]): r["qty"] or 0 for r in data.finished}
    rate = avg_output(data, day)
    orders = []
    for o in data.orders:
        if o["date"] > day:
            continue
        done = shipped[o["number"]] if data.shipments.rows else (o.get("shipped") or 0)
        st = (o.get("status") or "").lower()
        if not data.shipments.rows and not data.orders.has("shipped") and ("отгруж" in st or "закрыт" in st or "выполн" in st):
            done = o["qty"]
        left = max((o["qty"] or 0) - done, 0)
        orders.append({**o, "done": done, "left": left, "sum_left": left * (o.get("price") or 0),
                       "sum": (o["qty"] or 0) * (o.get("price") or 0), "cancelled": _closed(o.get("status"))})
    open_ = [o for o in orders if o["left"] > 1e-6 and not o["cancelled"]]

    # покрытие заказов: склад готовой продукции по порядку сроков, потом будущий выпуск
    stock_left = dict(fg)
    produced_by = defaultdict(float)  # сколько уже «зарезервировано» из будущего выпуска
    for o in sorted(open_, key=lambda o: (o.get("ship_by") or dt.date.max, o["date"])):
        p = norm_name(o["product"])
        from_stock = min(stock_left.get(p, 0), o["left"])
        stock_left[p] = stock_left.get(p, 0) - from_stock
        need = o["left"] - from_stock
        o["from_stock"] = from_stock
        due = o.get("ship_by")
        if due is None:
            o["cover"] = "no_date"
            continue
        if need <= 1e-6:
            o["cover"] = "stock"
            o["late"] = (day - due).days
            continue
        days_left = workdays(day + dt.timedelta(1), due, s) if due > day else 0
        can = rate.get(p, 0) * days_left - produced_by[p]
        produced_by[p] += need
        o["late"] = (day - due).days
        o["cover"] = "late" if due < day else ("produce" if can >= need else "risk")
        o["ready_by"] = None
        if rate.get(p):
            o["ready_in"] = produced_by[p] / rate[p]

    by_product = defaultdict(lambda: {"left": 0.0, "sum": 0.0, "count": 0})
    for o in open_:
        b = by_product[o["product"]]
        b["left"] += o["left"]
        b["sum"] += o["sum_left"]
        b["count"] += 1
    products = []
    for name, b in sorted(by_product.items(), key=lambda kv: -kv[1]["left"]):
        p = norm_name(name)
        to_make = max(b["left"] - fg.get(p, 0), 0)
        products.append({"product": name, **b, "fg": fg.get(p, 0), "to_make": to_make,
                         "rate": rate.get(p, 0), "days": to_make / rate[p] if rate.get(p) else None})
    for name, q in ((r["product"], r["qty"]) for r in data.finished):
        if norm_name(name) not in {norm_name(x["product"]) for x in products}:
            products.append({"product": name, "left": 0, "sum": 0, "count": 0, "fg": q or 0, "to_make": 0,
                             "rate": rate.get(norm_name(name), 0), "days": 0})

    new = [o for o in orders if o["date"] == day]
    overdue = sorted((o for o in open_ if o.get("ship_by") and o["ship_by"] < day), key=lambda o: o["ship_by"])
    soon = sorted((o for o in open_ if o.get("ship_by") and day <= o["ship_by"] <= day + dt.timedelta(s.horizon)),
                  key=lambda o: o["ship_by"])
    month_new = [o for o in orders if o["date"].replace(day=1) == day.replace(day=1) and not o["cancelled"]]
    return {
        "open": open_, "new": new, "overdue": overdue, "soon": soon, "products": products,
        "backlog_t": sum(o["left"] for o in open_), "backlog_sum": sum(o["sum_left"] for o in open_),
        "ship_today": ship_today, "ship_today_t": sum(x["qty"] or 0 for x in ship_today),
        "ship_today_sum": sum(x["sum"] for x in ship_today),
        "ship_mtd_t": sum(x["qty"] or 0 for x in ship_mtd), "ship_mtd_sum": sum(x["sum"] for x in ship_mtd),
        "month_new_t": sum(o["qty"] or 0 for o in month_new), "month_new_sum": sum(o["sum"] for o in month_new),
        "risk": [o for o in soon if o.get("cover") == "risk"],
        "has_data": bool(data.orders.rows),
    }


def month_plans(data: Data, day: dt.date) -> dict[str, float]:
    mk = month_key(day)
    res = defaultdict(float)
    for r in data.plan:
        if month_key(r["month"]) == mk:
            res[r["product"]] += r["plan"] or 0
    return dict(res)


def calc_output(data: Data, day: dt.date, s: Settings) -> dict:
    mplan = month_plans(data, day)
    month_start = day.replace(day=1)
    days_in_month = calendar.monthrange(day.year, day.month)[1]
    month_end = day.replace(day=days_in_month)
    wd_total = workdays(month_start, month_end, s)
    wd_done = workdays(month_start, day, s)
    wd_left = wd_total - wd_done

    rows_today = [r for r in data.output if r["date"] == day]
    rows_mtd = [r for r in data.output if month_start <= r["date"] <= day]
    names = []
    for r in data.output:
        if r["product"] not in names:
            names.append(r["product"])
    for p in mplan:
        if p not in names:
            names.append(p)

    rate = avg_output(data, day)
    products = []
    for p in names:
        t = [r for r in rows_today if r["product"] == p]
        m = [r for r in rows_mtd if r["product"] == p]
        mp = next((v for k, v in mplan.items() if norm_name(k) == norm_name(p)), None)
        has_daily_plan = any(r.get("plan") is not None for r in t)
        day_plan = sum(r.get("plan") or 0 for r in t) if has_daily_plan else (mp / wd_total if mp and is_workday(day, s) else None)
        if any(r.get("plan") is not None for r in m):
            plan_mtd = sum(r.get("plan") or 0 for r in m)
        else:
            plan_mtd = mp * wd_done / wd_total if mp else None
        fact_mtd = sum(r["fact"] or 0 for r in m)
        forecast = fact_mtd + rate.get(norm_name(p), 0) * wd_left
        products.append({
            "product": p,
            "plan": day_plan, "fact": sum(r["fact"] or 0 for r in t), "defect": sum(r.get("defect") or 0 for r in t),
            "plan_mtd": plan_mtd, "fact_mtd": fact_mtd, "defect_mtd": sum(r.get("defect") or 0 for r in m),
            "month_plan": mp, "forecast": forecast,
            "need_per_day": (max(mp - fact_mtd, 0) / wd_left) if mp and wd_left else None,
            "rate": rate.get(norm_name(p), 0),
        })

    def tot(key):
        vals = [p[key] for p in products if p[key] is not None]
        return sum(vals) if vals else None

    total = {k: tot(k) for k in ("plan", "fact", "defect", "plan_mtd", "fact_mtd", "defect_mtd", "month_plan", "forecast", "need_per_day", "rate")}

    history = []
    for i in range(13, -1, -1):
        d = day - dt.timedelta(i)
        rs = [r for r in data.output if r["date"] == d]
        plan_d = sum(r.get("plan") or 0 for r in rs) if any(r.get("plan") is not None for r in rs) else (
            sum(mplan.values()) / wd_total if mplan and is_workday(d, s) and d.month == day.month else None)
        history.append({"date": d, "fact": sum(r["fact"] or 0 for r in rs) if rs else None, "plan": plan_d})

    # фактический расход сырья за день по нормам
    norms = defaultdict(list)
    for n in data.norms:
        norms[norm_name(n["product"])].append(n)
    consumption = defaultdict(lambda: {"qty": 0.0, "unit": ""})
    for p in products:
        for n in norms.get(norm_name(p["product"]), []):
            c = consumption[n["material"]]
            c["qty"] += p["fact"] * (n["norm"] or 0)
            c["unit"] = n.get("unit") or c["unit"]

    return {"products": products, "total": total, "rows": sorted(rows_today, key=lambda r: (r.get("shift") or "", r["product"])),
            "history": history, "wd_total": wd_total, "wd_done": wd_done, "wd_left": wd_left,
            "consumption": dict(consumption), "has_data": bool(data.output.rows), "has_today": bool(rows_today)}


def calc_materials(data: Data, day: dt.date, s: Settings, orders: dict, output: dict) -> dict:
    """Нехватка сырья: суточная потребность по плану выпуска (или по факту, если плана нет),
    на сколько дней хватит остатка и поставок в пути, сколько нужно на портфель заказов."""
    norms = defaultdict(list)
    for n in data.norms:
        norms[norm_name(n["material"])].append(n)
    # суточный темп: нужный для выполнения плана месяца, но не ниже среднего фактического
    rate = {}
    for p in output["products"]:
        planned = p["need_per_day"] if p["need_per_day"] is not None else (p["month_plan"] / output["wd_total"] if p["month_plan"] else 0)
        rate[norm_name(p["product"])] = max(planned or 0, p["rate"] or 0)
    to_make = {norm_name(p["product"]): p["to_make"] for p in orders["products"]}
    fen = data.phenol[-1] if data.phenol else None

    items = []
    for st in data.stock:
        m = norm_name(st["material"])
        uses = norms.get(m, [])
        daily = sum(rate.get(norm_name(n["product"]), 0) * (n["norm"] or 0) for n in uses)
        daily = daily * (s.days_per_week / 7)  # календарный темп
        need_orders = sum(to_make.get(norm_name(n["product"]), 0) * (n["norm"] or 0) for n in uses)
        qty, transit, mn = st["qty"] or 0, st.get("transit") or 0, st.get("min") or 0
        lead = int(st["lead"]) if st.get("lead") is not None else None
        cover = qty / daily if daily else None
        cover_all = (qty + transit) / daily if daily else None
        runout = day + dt.timedelta(days=int(cover)) if cover is not None else None
        arrival = st.get("arrival")
        status, note = "ok", ""
        gap_days = 0
        if daily:
            if transit and arrival and runout and arrival > runout:
                gap_days = (arrival - runout).days
                status = "crit"
                note = f"Закончится {fmt_dm(runout)}, поставка {fmt_dm(arrival)} — простой ~{gap_days} {plural(gap_days, 'день', 'дня', 'дней')}"
            elif not transit and lead is not None and cover is not None and cover < lead:
                status = "crit"
                note = f"Хватит на {fmt_num(cover, 1)} дн., а поставка идёт {lead} дн. — заказать сегодня"
            elif cover_all is not None and lead is not None and cover_all < lead + s.reorder_reserve:
                status = "warn"
                note = "Пора заказывать: запас меньше срока поставки с резервом"
            elif cover is not None and cover < 2:
                status = "warn"
                note = "Остатка меньше чем на 2 дня, вся надежда на поставку в пути"
        if status == "ok" and mn and qty < mn:
            status = "warn"
            note = f"Ниже минимального запаса ({fmt_num(mn, 1)} {st.get('unit') or ''})".strip()
        if status == "ok" and not daily and not uses:
            note = "Нет норм расхода — потребность не считается"
        order_qty = 0.0
        if daily:
            target = daily * ((lead or 0) + s.horizon) + mn
            order_qty = max(target - qty - transit, 0)
        price = st.get("price")
        market = None
        if fen and m.startswith("фенол"):
            market = fen
        items.append({**st, "daily": daily, "need_orders": need_orders, "cover": cover, "cover_all": cover_all,
                      "runout": runout, "status": status, "note": note, "order_qty": order_qty,
                      "order_sum": order_qty * price if price else None, "value": qty * price if price else None,
                      "market": market, "gap_days": gap_days, "uses": [n["product"] for n in uses]})
    order = {"crit": 0, "warn": 1, "ok": 2}
    items.sort(key=lambda i: (order[i["status"]], i["cover"] if i["cover"] is not None else 1e9))
    phenol = None
    if data.phenol:
        last = data.phenol[-1]
        week = [r for r in data.phenol if r["date"] <= last["date"] - dt.timedelta(7)]
        month = [r for r in data.phenol if r["date"] <= last["date"] - dt.timedelta(28)]
        phenol = {"last": last, "week": week[-1] if week else data.phenol[0],
                  "first": month[-1] if month else data.phenol[0], "series": data.phenol[-30:]}
    return {"items": items, "crit": [i for i in items if i["status"] == "crit"],
            "warn": [i for i in items if i["status"] == "warn"],
            "order_sum": sum(i["order_sum"] or 0 for i in items if i["status"] != "ok"),
            "value": sum(i["value"] or 0 for i in items), "phenol": phenol, "has_data": bool(data.stock.rows)}


# ================================================================== главное
@dataclass
class Alert:
    level: str     # crit | warn | info | good
    section: str
    title: str
    text: str = ""


def build_alerts(r: dict, s: Settings) -> list[Alert]:
    a: list[Alert] = []
    m, d, o, out, mat = r["money"], r["debts"], r["orders"], r["output"], r["materials"]
    if m["low"] and m["total"] is not None:
        low = m["low"]
        if low["balance"] < 0:
            a.append(Alert("crit", "money", f"Кассовый разрыв {fmt_dm(low['date'])}: {fmt_money(low['balance'], True)}",
                           "Если платить по срокам всем поставщикам и не получить просроченную дебиторку. "
                           "Нужно перенести платежи или ускорить поступления."))
        elif s.min_cash and low["balance"] < s.min_cash:
            a.append(Alert("warn", "money", f"Остаток опустится до {fmt_money(low['balance'], True)} ({fmt_dm(low['date'])})",
                           f"Ниже неснижаемого остатка {fmt_money(s.min_cash, True)}."))
    if d["has_data"]:
        rv = d["recv"]
        if rv["overdue"] > 0:
            worst = rv["counterparties"][0]
            share = rv["overdue"] / rv["total"] * 100 if rv["total"] else 0
            a.append(Alert("crit" if share >= 30 else "warn", "debts",
                           f"Просроченная дебиторка {fmt_money(rv['overdue'], True)} — {fmt_pct(share)} долга покупателей",
                           f"Больше всех должен {worst['name']}: {fmt_money(worst['overdue'], True)}, до {worst['late']} дн. просрочки."))
        old = sum(x["sum"] for x in rv["aging"][3:])
        if old > 0:
            a.append(Alert("warn", "debts", f"Долги покупателей старше 60 дней: {fmt_money(old, True)}",
                           "Высокий риск невозврата — претензия, суд или резерв по сомнительным долгам."))
        pv = d["pay"]
        if pv["overdue"] > 0:
            a.append(Alert("warn", "debts", f"Мы просрочили оплату поставщикам на {fmt_money(pv['overdue'], True)}",
                           ", ".join([c["name"] for c in pv["counterparties"] if c["overdue"] > 0][:3])))
    for i in mat["crit"]:
        a.append(Alert("crit", "materials", f"{i['material']}: {i['note']}",
                       f"Остаток {fmt_num(i['qty'], 1)} {i.get('unit') or ''}, расход {fmt_num(i['daily'], 2)} {i.get('unit') or ''}/сут. "
                       f"Нужен для: {', '.join(i['uses'])}."))
    for i in mat["warn"]:
        a.append(Alert("warn", "materials", f"{i['material']}: {i['note']}",
                       f"Хватит на {fmt_num(i['cover'], 1) if i['cover'] is not None else '—'} дн. Заказать {fmt_num(i['order_qty'], 1)} {i.get('unit') or ''}."))
    if o["has_data"]:
        if o["overdue"]:
            t = sum(x["left"] for x in o["overdue"])
            a.append(Alert("crit", "orders", f"Сорван срок отгрузки: {len(o['overdue'])} {plural(len(o['overdue']), 'заказ', 'заказа', 'заказов')}, {fmt_t(t)}",
                           "; ".join(f"{x['number']} {x['client']} — {x['late']} дн." for x in o["overdue"][:4])))
        if o["risk"]:
            a.append(Alert("warn", "orders", f"Не успеваем к сроку: {len(o['risk'])} {plural(len(o['risk']), 'заказ', 'заказа', 'заказов')} в ближайшие {s.horizon} дн.",
                           "; ".join(f"{x['number']} {x['product']} {fmt_t(x['left'])} до {fmt_dm(x['ship_by'])}" for x in o["risk"][:4])))
    if out["has_data"]:
        t = out["total"]
        if t["plan"]:
            pct = t["fact"] / t["plan"] * 100
            if pct < 90:
                lag = [p for p in out["products"] if p["plan"] and p["fact"] / p["plan"] < .9]
                a.append(Alert("warn" if pct >= 75 else "crit", "output", f"Выпуск за сутки {fmt_pct(pct)} плана: {fmt_t(t['fact'])} из {fmt_t(t['plan'])}",
                               "Отстают: " + ", ".join(f"{p['product']} ({fmt_pct(p['fact'] / p['plan'] * 100)})" for p in lag)))
            elif pct >= 100:
                a.append(Alert("good", "output", f"План суток выполнен: {fmt_pct(pct)}"))
        if t["month_plan"] and t["forecast"] is not None:
            fp = t["forecast"] / t["month_plan"] * 100
            if fp < 97:
                a.append(Alert("warn", "output", f"Прогноз месяца {fmt_pct(fp)} плана: {fmt_t(t['forecast'], 0)} из {fmt_t(t['month_plan'], 0)}",
                               f"Чтобы выполнить план, нужно {fmt_t(t['need_per_day'])} в сутки до конца месяца при текущих {fmt_t(t['rate'])}."))
        if t["fact"]:
            dr = (t["defect"] or 0) / t["fact"] * 100
            if dr > s.defect_norm:
                a.append(Alert("warn", "output", f"Брак {fmt_pct(dr, 1)} при норме {fmt_pct(s.defect_norm, 1)}",
                               ", ".join(f"{p['product']}: {fmt_t(p['defect'], 2)}" for p in out["products"] if p["defect"])))
    ph = mat.get("phenol")
    if ph and ph["week"]["value"]:
        ch = (ph["last"]["value"] / ph["week"]["value"] - 1) * 100
        if abs(ch) >= 3:
            a.append(Alert("warn" if ch > 0 else "info", "materials",
                           f"Фенол на бирже {'подорожал' if ch > 0 else 'подешевел'} за неделю на {fmt_pct(abs(ch), 1)}",
                           f"Индикатор FEN {fmt_money(ph['last']['value'])}/т на {fmt_dm(ph['last']['date'])}."))
    big_in = [p for p in m["income"] if p["income"] >= s.big_payment]
    big_out = [p for p in m["expense"] if p["expense"] >= s.big_payment]
    for p in big_in:
        a.append(Alert("good", "money", f"Поступило {fmt_money(p['income'], True)} от {p.get('counterparty') or '—'}", p.get("purpose") or ""))
    for p in big_out:
        a.append(Alert("info", "money", f"Оплачено {fmt_money(p['expense'], True)}: {p.get('counterparty') or '—'}", p.get("purpose") or ""))
    rank = {"crit": 0, "warn": 1, "info": 2, "good": 3}
    return sorted(a, key=lambda x: rank[x.level])


def build(data: Data, day: dt.date, s: Settings) -> dict:
    debts = calc_debts(data, day, s)
    money = calc_money(data, day, s, debts)
    orders = calc_orders(data, day, s)
    output = calc_output(data, day, s)
    materials = calc_materials(data, day, s, orders, output)
    r = {"day": day, "settings": s, "money": money, "debts": debts, "orders": orders, "output": output, "materials": materials}
    r["alerts"] = build_alerts(r, s)
    return r


def summary_line(r: dict) -> str:
    """Одна строка для уведомления на телефоне."""
    parts = [f"Отчёт {fmt_dm(r['day'])}"]
    m, d, o, out, mat = r["money"], r["debts"], r["orders"], r["output"], r["materials"]
    if m["total"] is not None:
        parts.append(f"деньги {fmt_money(m['total'], True)}")
    if d["has_data"]:
        parts.append(f"ДЗ {fmt_money(d['recv']['total'], True)} (просрочено {fmt_money(d['recv']['overdue'], True)})")
    if out["has_data"] and out["total"]["fact"] is not None:
        t = out["total"]
        pct = f" ({fmt_pct(t['fact'] / t['plan'] * 100)})" if t["plan"] else ""
        parts.append(f"выпуск {fmt_t(t['fact'])}{pct}")
    if o["has_data"]:
        parts.append(f"заказы {fmt_t(o['backlog_t'], 0)}" + (f", просрочено {len(o['overdue'])}" if o["overdue"] else ""))
    if mat["crit"]:
        parts.append("нехватка: " + ", ".join(i["material"] for i in mat["crit"]))
    return "; ".join(parts).replace(" ", " ").replace(" ", " ")


# ================================================================== HTML
E = html.escape

CSS = """
/* Сводка сверху, дальше разделы в порядке вопросов руководителя: деньги → долги → заказы → выпуск → сырьё.
   Одна колонка до 1040px, плитки и таблицы перестраиваются под телефон. */
:root{
  --bg:#F3F5F4; --paper:#FFFFFF; --ink:#18201F; --muted:#5D6B69; --line:#D9E0DE; --soft:#E9EEEC;
  --accent:#0E6A66; --accent-soft:#D5ECE9;
  --crit:#B8321F; --crit-soft:#F8E1DC; --warn:#A86A0C; --warn-soft:#F7EBD3; --good:#25774A; --good-soft:#DCEFE3; --info:#3A5D8F; --info-soft:#E1E8F3;
  --plan:#9AA7A5;
  --f-body:"Golos Text",-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
  --f-head:"Manrope","Golos Text",-apple-system,"Segoe UI",Arial,sans-serif;
  --f-mono:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0F1514; --paper:#172120; --ink:#E5ECEA; --muted:#95A6A3; --line:#2A3735; --soft:#1E2A28;
  --accent:#4FC0B8; --accent-soft:#183B38;
  --crit:#F07A64; --crit-soft:#3D1F1A; --warn:#E5AC4A; --warn-soft:#3A2D14; --good:#5CCB8A; --good-soft:#173324; --info:#8DB0E6; --info-soft:#1C2940;
  --plan:#5F6E6C; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0F1514; --paper:#172120; --ink:#E5ECEA; --muted:#95A6A3; --line:#2A3735; --soft:#1E2A28;
  --accent:#4FC0B8; --accent-soft:#183B38;
  --crit:#F07A64; --crit-soft:#3D1F1A; --warn:#E5AC4A; --warn-soft:#3A2D14; --good:#5CCB8A; --good-soft:#173324; --info:#8DB0E6; --info-soft:#1C2940;
  --plan:#5F6E6C; color-scheme:dark}
*,*::before,*::after{box-sizing:border-box}
html{scroll-padding-top:64px}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--f-body);-webkit-text-size-adjust:100%}
.wrap{max-width:1040px;margin:0 auto;padding-block:20px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2,h3{font-family:var(--f-head);text-wrap:balance;margin:0}
h1{font-size:clamp(22px,4vw,30px);font-weight:800;letter-spacing:-.02em;line-height:1.15}
h2{font-size:21px;font-weight:800;letter-spacing:-.01em}
h3{font-size:15px;font-weight:700;margin:22px 0 8px}
p{margin:0}
.num,td.n,th.n{font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
.small{font-size:13px}
.mono{font-family:var(--f-mono);font-size:12.5px}
header.top{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:flex-end;justify-content:space-between;padding-bottom:14px;border-bottom:2px solid var(--ink)}
.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:600}
nav.toc{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--bg);display:flex;gap:6px;overflow-x:auto;padding:10px 0;margin-bottom:6px;scrollbar-width:none}
nav.toc a{flex:none;text-decoration:none;color:var(--ink);font-size:13.5px;font-weight:600;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--paper);display:flex;gap:6px;align-items:center}
nav.toc a:hover{border-color:var(--accent)}
nav.toc a:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.badge{display:inline-block;min-width:18px;padding:0 6px;border-radius:9px;font-size:11.5px;line-height:18px;text-align:center;font-weight:700;color:#fff}
.badge.crit{background:var(--crit)} .badge.warn{background:var(--warn)}
section{margin-top:34px}
.sec-head{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;justify-content:space-between;margin-bottom:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:12px 14px;min-width:0;text-decoration:none;color:inherit;display:block}
a.tile:hover{border-color:var(--accent)}
.tile .k{font-size:12.5px;color:var(--muted);font-weight:500}
.tile .v{font:800 22px/1.2 var(--f-head);font-variant-numeric:tabular-nums;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .s{font-size:12.5px;margin-top:3px;color:var(--muted)}
.tile.crit{border-color:var(--crit);box-shadow:inset 0 3px 0 var(--crit)} .tile.warn{box-shadow:inset 0 3px 0 var(--warn)} .tile.good{box-shadow:inset 0 3px 0 var(--good)}
.up{color:var(--good)} .down{color:var(--crit)} .warn-t{color:var(--warn)} .crit-t{color:var(--crit)} .good-t{color:var(--good)}
.alerts{display:flex;flex-direction:column;gap:6px;margin-top:14px}
.alert{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;padding:10px 14px;border-radius:10px;background:var(--paper);border:1px solid var(--line);text-decoration:none;color:inherit}
.alert .ic{grid-row:span 2;width:22px;height:22px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:13px;color:#fff;margin-top:1px}
.alert.crit{background:var(--crit-soft);border-color:transparent}.alert.crit .ic{background:var(--crit)}
.alert.warn{background:var(--warn-soft);border-color:transparent}.alert.warn .ic{background:var(--warn)}
.alert.info .ic{background:var(--info)} .alert.good .ic{background:var(--good)}
.alert b{font-weight:650}
.alert .t{font-size:13.5px;color:var(--muted)}
.card{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-top:12px}
.tbl{overflow-x:auto;background:var(--paper);border:1px solid var(--line);border-radius:12px;margin-top:8px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:7px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--soft)}
th{font-size:12px;font-weight:600;color:var(--muted);background:var(--soft);white-space:nowrap}
td.n,th.n{text-align:right;white-space:nowrap}
tr:last-child td{border-bottom:0}
tr.total td{font-weight:700;border-top:2px solid var(--line);background:var(--soft)}
tr.crit td:first-child{box-shadow:inset 3px 0 0 var(--crit)} tr.warn td:first-child{box-shadow:inset 3px 0 0 var(--warn)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.pill.crit{background:var(--crit-soft);color:var(--crit)} .pill.warn{background:var(--warn-soft);color:var(--warn)}
.pill.ok{background:var(--good-soft);color:var(--good)} .pill.info{background:var(--info-soft);color:var(--info)} .pill.mute{background:var(--soft);color:var(--muted)}
.bar{height:8px;border-radius:4px;background:var(--soft);overflow:hidden;min-width:60px}
.bar i{display:block;height:100%;background:var(--accent);border-radius:4px}
.bar i.crit{background:var(--crit)} .bar i.warn{background:var(--warn)} .bar i.good{background:var(--good)}
.meter{position:relative;height:10px;border-radius:5px;background:var(--soft);margin-top:8px}
.meter i{position:absolute;left:0;top:0;bottom:0;border-radius:5px;background:var(--accent)}
.meter b{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink)}
.stack{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--soft);margin:10px 0 8px}
.stack i{display:block;height:100%}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12.5px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:5px}
.legend span::before{content:"";width:10px;height:10px;border-radius:3px;background:var(--c)}
.kv{display:grid;grid-template-columns:1fr auto;gap:4px 12px;font-size:14px}
.kv dt{color:var(--muted)} .kv dd{margin:0;text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
details{margin-top:8px}
summary{cursor:pointer;font-size:13.5px;color:var(--accent);font-weight:600;padding:4px 0}
svg text{fill:var(--muted);font:11px var(--f-body)}
svg .lbl{fill:var(--ink);font-weight:600}
.chart{width:100%;height:auto;display:block}
.note{font-size:13px;color:var(--muted);margin-top:8px;max-width:75ch}
.empty{padding:14px 16px;border:1px dashed var(--line);border-radius:12px;color:var(--muted);font-size:14px}
footer{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);font-size:12.5px;color:var(--muted)}
@media (max-width:560px){.tile .v{font-size:19px} h2{font-size:19px} th,td{padding:6px 8px}}
@media print{nav.toc{display:none} body{background:#fff} section{break-inside:avoid-page} .tbl,.card,.tile{break-inside:avoid}}
"""


def cls_change(v: float | None, good_up: bool = True) -> str:
    if not v:
        return "muted"
    return "up" if (v > 0) == good_up else "down"


def tile(k: str, v: str, sub: str = "", level: str = "", href: str = "") -> str:
    tag = "a" if href else "div"
    attr = f' href="#{href}"' if href else ""
    return f'<{tag} class="tile {level}"{attr}><div class="k">{E(k)}</div><div class="v">{v}</div>' + (f'<div class="s">{sub}</div>' if sub else "") + f"</{tag}>"


def table(head: list[tuple[str, str]], rows: list[list[str]], row_cls: list[str] | None = None, total: list[str] | None = None) -> str:
    """head: [(заголовок, 'n'|'')]. Значения в rows — уже готовый HTML."""
    h = "".join(f'<th class="{c}">{E(t)}</th>' for t, c in head)
    body = []
    for i, r in enumerate(rows):
        rc = (row_cls[i] if row_cls else "") or ""
        body.append(f'<tr class="{rc}">' + "".join(f'<td class="{head[j][1]}">{v}</td>' for j, v in enumerate(r)) + "</tr>")
    if total:
        body.append('<tr class="total">' + "".join(f'<td class="{head[j][1]}">{v}</td>' for j, v in enumerate(total)) + "</tr>")
    return f'<div class="tbl"><table><thead><tr>{h}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def bar(pct: float | None, level: str = "") -> str:
    if pct is None:
        return ""
    return f'<div class="bar" title="{fmt_pct(pct)}"><i class="{level}" style="width:{max(0, min(pct, 100)):.0f}%"></i></div>'


def pct_level(pct: float | None) -> str:
    if pct is None:
        return ""
    return "good" if pct >= 100 else ("warn" if pct >= 90 else "crit")


def pct_cell(fact: float | None, plan: float | None) -> str:
    if not plan:
        return '<span class="muted">—</span>'
    p = (fact or 0) / plan * 100
    lv = pct_level(p)
    return f'<span class="{ {"good": "good-t", "warn": "warn-t", "crit": "crit-t"}[lv] }">{fmt_pct(p)}</span>'


def empty(text: str) -> str:
    return f'<div class="empty">{text}</div>'


def section(id_: str, title: str, aside: str, body: str) -> str:
    return f'<section id="{id_}"><div class="sec-head"><h2>{E(title)}</h2><div class="muted small">{aside}</div></div>{body}</section>'


# ------------------------------------------------------------------ графики
def svg_output(history: list[dict]) -> str:
    """Выпуск по суткам за 14 дней: столбики — факт, штрих — план."""
    W, H, L, B, T = 640, 190, 34, 34, 14
    vals = [h["fact"] or 0 for h in history] + [h["plan"] or 0 for h in history]
    top = max(vals) if vals and max(vals) > 0 else 1
    step = 10 if top <= 60 else 20 if top <= 140 else 50
    top = (int(top / step) + 1) * step
    cw = (W - L - 8) / len(history)
    y = lambda v: T + (H - T - B) * (1 - v / top)
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Выпуск по суткам за 14 дней, т">']
    for g in range(0, top + 1, step):
        out.append(f'<line x1="{L}" x2="{W - 4}" y1="{y(g):.1f}" y2="{y(g):.1f}" stroke="var(--soft)" stroke-width="1"/>'
                   f'<text x="{L - 6}" y="{y(g) + 4:.1f}" text-anchor="end">{g}</text>')
    for i, h in enumerate(history):
        x = L + i * cw
        last = i == len(history) - 1
        if h["fact"] is not None:
            level = "var(--accent)"
            if h["plan"]:
                p = h["fact"] / h["plan"]
                level = "var(--accent)" if p >= .9 else ("var(--warn)" if p >= .75 else "var(--crit)")
            out.append(f'<rect x="{x + cw * .18:.1f}" y="{y(h["fact"]):.1f}" width="{cw * .64:.1f}" height="{y(0) - y(h["fact"]):.1f}" rx="2" fill="{level}" opacity="{1 if last else .78}"><title>{fmt_dm(h["date"])}: {fmt_t(h["fact"])}</title></rect>')
            if last:
                out.append(f'<text class="lbl" x="{x + cw / 2:.1f}" y="{y(h["fact"]) - 5:.1f}" text-anchor="middle">{fmt_num(h["fact"], 1)}</text>')
        if h["plan"]:
            out.append(f'<line x1="{x + cw * .08:.1f}" x2="{x + cw * .92:.1f}" y1="{y(h["plan"]):.1f}" y2="{y(h["plan"]):.1f}" stroke="var(--ink)" stroke-width="2" stroke-dasharray="4 3"/>')
        wd = WD_SHORT[h["date"].weekday()]
        out.append(f'<text x="{x + cw / 2:.1f}" y="{H - 18}" text-anchor="middle"{" class=\"lbl\"" if last else ""}>{h["date"].day}</text>'
                   f'<text x="{x + cw / 2:.1f}" y="{H - 5}" text-anchor="middle">{wd}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_cash(start: float, forecast: list[dict], day: dt.date, threshold: float) -> str:
    """Прогноз остатка денег по дням: линия с точками и порогом."""
    W, H, L, B, T = 640, 180, 62, 30, 18
    pts = [(day, start)] + [(f["date"], f["balance"]) for f in forecast]
    vs = [v for _, v in pts] + [0, threshold]
    lo, hi = min(vs), max(vs)
    pad = (hi - lo) * .1 or 1
    lo, hi = lo - pad, hi + pad
    x = lambda i: L + (W - L - 20) * i / max(len(pts) - 1, 1)
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Прогноз остатка денег">']
    for g in _nice_ticks(lo, hi, 4):
        out.append(f'<line x1="{L}" x2="{W - 10}" y1="{y(g):.1f}" y2="{y(g):.1f}" stroke="var(--soft)"/>'
                   f'<text x="{L - 6}" y="{y(g) + 4:.1f}" text-anchor="end">{fmt_num(g / 1e6, 0 if abs(hi - lo) > 2e7 else 1)}</text>')
    out.append(f'<text x="{L - 6}" y="10" text-anchor="end">млн ₽</text>')
    if lo < 0 < hi:
        out.append(f'<line x1="{L}" x2="{W - 10}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="var(--crit)" stroke-width="1.5"/>')
    if threshold:
        out.append(f'<line x1="{L}" x2="{W - 10}" y1="{y(threshold):.1f}" y2="{y(threshold):.1f}" stroke="var(--warn)" stroke-dasharray="5 4" stroke-width="1.5"/>'
                   f'<text x="{W - 12}" y="{y(threshold) - 5:.1f}" text-anchor="end">неснижаемый остаток</text>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(pts))
    area = path + f" L{x(len(pts) - 1):.1f},{y(max(lo, 0) if lo < 0 else lo):.1f} L{x(0):.1f},{y(max(lo, 0) if lo < 0 else lo):.1f} Z"
    out.append(f'<path d="{area}" fill="var(--accent-soft)" opacity=".7"/>')
    out.append(f'<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/>')
    for i, (d, v) in enumerate(pts):
        c = "var(--crit)" if v < 0 else ("var(--warn)" if threshold and v < threshold else "var(--accent)")
        out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="{5 if i == 0 else 4}" fill="{c}" stroke="var(--paper)" stroke-width="2"><title>{fmt_dm(d)}: {fmt_money(v)}</title></circle>')
        out.append(f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="middle"{" class=\"lbl\"" if i == 0 else ""}>{fmt_dm(d)}</text>')
    out.append("</svg>")
    return "".join(out)


def _nice_ticks(lo: float, hi: float, n: int) -> list[float]:
    import math
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = math.ceil(lo / step) * step
    ticks = []
    v = start
    while v <= hi + 1e-9:
        ticks.append(round(v, 6))
        v += step
    return ticks


def svg_spark(series: list[dict]) -> str:
    if len(series) < 2:
        return ""
    W, H = 400, 90
    vs = [r["value"] for r in series]
    lo, hi = min(vs), max(vs)
    if hi == lo:
        hi += 1
    x = lambda i: 4 + (W - 8) * i / (len(vs) - 1)
    y = lambda v: 6 + (H - 12) * (hi - v) / (hi - lo)
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vs))
    return (f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Индикатор фенола за месяц">'
            f'<path d="{path} L{x(len(vs) - 1):.1f},{H} L{x(0):.1f},{H} Z" fill="var(--accent-soft)"/>'
            f'<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="2"/>'
            f'<circle cx="{x(len(vs) - 1):.1f}" cy="{y(vs[-1]):.1f}" r="3.5" fill="var(--accent)"/></svg>')


# ------------------------------------------------------------------ разделы
def render_summary(r: dict) -> str:
    m, d, o, out, mat, s = r["money"], r["debts"], r["orders"], r["output"], r["materials"], r["settings"]
    tiles = []
    if m["total"] is not None:
        ch = m["total"] - m["total_prev"] if m["total_prev"] is not None else None
        lvl = "crit" if m["low"] and m["low"]["balance"] < 0 else ("warn" if s.min_cash and m["low"] and m["low"]["balance"] < s.min_cash else "")
        tiles.append(tile("Деньги на счетах", fmt_money(m["total"], True),
                          f'<span class="{cls_change(ch)}">{fmt_signed(ch, lambda v: fmt_money(v, True))}</span> за день' if ch is not None else "", lvl, "money"))
    if d["has_data"]:
        rv, pv = d["recv"], d["pay"]
        tiles.append(tile("Нам должны", fmt_money(rv["total"], True),
                          f'<span class="{"crit-t" if rv["overdue"] else "muted"}">просрочено {fmt_money(rv["overdue"], True)}</span>',
                          "crit" if rv["total"] and rv["overdue"] / rv["total"] >= .3 else ("warn" if rv["overdue"] else ""), "debts"))
        tiles.append(tile("Мы должны", fmt_money(pv["total"], True),
                          f'<span class="{"warn-t" if pv["overdue"] else "muted"}">просрочено {fmt_money(pv["overdue"], True)}</span>',
                          "warn" if pv["overdue"] else "", "debts"))
    if o["has_data"]:
        tiles.append(tile("Портфель заказов", fmt_t(o["backlog_t"], 0),
                          f'{fmt_money(o["backlog_sum"], True)}, {len(o["open"])} {plural(len(o["open"]), "заказ", "заказа", "заказов")}',
                          "crit" if o["overdue"] else ("warn" if o["risk"] else ""), "orders"))
    if out["has_data"]:
        t = out["total"]
        pct = t["fact"] / t["plan"] * 100 if t["plan"] else None
        tiles.append(tile("Выпуск за сутки", fmt_t(t["fact"]),
                          (f'{fmt_pct(pct)} плана' if pct is not None else "план не задан"),
                          "" if pct is None else ("good" if pct >= 100 else "warn" if pct >= 90 else "crit"), "output"))
        if t["month_plan"]:
            mp = t["fact_mtd"] / t["month_plan"] * 100
            tiles.append(tile("С начала месяца", fmt_t(t["fact_mtd"], 0),
                              f'{fmt_pct(mp)} месячного плана, прогноз {fmt_pct(t["forecast"] / t["month_plan"] * 100)}', "", "output"))
    if mat["has_data"]:
        tiles.append(tile("Сырьё", f'{len(mat["crit"])} / {len(mat["warn"])}',
                          "критично / заказать" if mat["crit"] or mat["warn"] else "всё в норме",
                          "crit" if mat["crit"] else ("warn" if mat["warn"] else "good"), "materials"))
    alerts = r["alerts"]
    icons = {"crit": "!", "warn": "!", "info": "i", "good": "✓"}
    al = "".join(f'<a class="alert {a.level}" href="#{a.section}"><span class="ic" aria-hidden="true">{icons[a.level]}</span>'
                 f'<b>{E(a.title)}</b>' + (f'<span class="t">{E(a.text)}</span>' if a.text else "<span></span>") + "</a>" for a in alerts)
    n_crit = sum(1 for a in alerts if a.level == "crit")
    n_warn = sum(1 for a in alerts if a.level == "warn")
    head = (f'{n_crit} {plural(n_crit, "критичный вопрос", "критичных вопроса", "критичных вопросов")}, '
            f'{n_warn} {plural(n_warn, "требует", "требуют", "требуют")} внимания') if alerts else "Замечаний нет"
    return (f'<section id="summary" style="margin-top:18px"><div class="tiles">{"".join(tiles)}</div>'
            f'<h3 style="margin-top:20px">Что требует решения — {head}</h3><div class="alerts">{al or empty("Все показатели в норме.")}</div></section>')


def render_money(r: dict) -> str:
    m, s, day = r["money"], r["settings"], r["day"]
    if not m["has_data"]:
        return section("money", "Деньги", "", empty("Нет таблиц balances и payments — добавьте остатки на счетах и выписку."))
    parts = []
    if m["accounts"]:
        rows = [[E(a["account"]), fmt_money(a["prev"]) if a["prev"] is not None else "—", fmt_money(a["balance"]),
                 f'<span class="{cls_change(a["change"])}">{fmt_signed(a["change"], fmt_money)}</span>'] for a in m["accounts"]]
        ch = m["total"] - m["total_prev"] if m["total_prev"] is not None else None
        parts.append('<div class="grid2"><div style="min-width:0"><h3 style="margin-top:0">Остатки на конец дня</h3>' + table(
            [("Счёт / касса", ""), (f"На {fmt_dm(day - dt.timedelta(1))}", "n"), (f"На {fmt_dm(day)}", "n"), ("Изменение", "n")], rows,
            total=["Итого", fmt_money(m["total_prev"]), fmt_money(m["total"]), f'<span class="{cls_change(ch)}">{fmt_signed(ch, fmt_money)}</span>']) + "</div>")
    else:
        parts.append('<div class="grid2">')
    parts.append(f'<div class="card"><h3 style="margin-top:0">Движение за {fmt_dm(day)}</h3><dl class="kv">'
                 f'<dt>Поступило</dt><dd class="up">{fmt_money(m["income_sum"])}</dd>'
                 f'<dt>Оплачено</dt><dd class="down">{fmt_money(m["expense_sum"])}</dd>'
                 f'<dt>Сальдо дня</dt><dd>{fmt_signed(m["income_sum"] - m["expense_sum"], fmt_money)}</dd>'
                 f'<dt>Поступило с 1-го числа</dt><dd>{fmt_money(m["mtd_in"])}</dd>'
                 f'<dt>Оплачено с 1-го числа</dt><dd>{fmt_money(m["mtd_out"])}</dd></dl>'
                 + (f'<p class="note">Внутренние перемещения ({len(m["transfers"])}) в приход и расход не включены.</p>' if m["transfers"] else "")
                 + "</div></div>")

    if m["income"] or m["expense"]:
        rows, rc = [], []
        for p in m["income"]:
            rows.append(['<span class="pill ok">приход</span>', E(p.get("counterparty") or "—"), E(p.get("article") or ""), E(p.get("purpose") or ""), f'<span class="up">{fmt_money(p["income"])}</span>'])
            rc.append("")
        for p in m["expense"]:
            rows.append(['<span class="pill mute">расход</span>', E(p.get("counterparty") or "—"), E(p.get("article") or ""), E(p.get("purpose") or ""), fmt_money(p["expense"])])
            rc.append("")
        parts.append(f'<h3>Платежи за день</h3>' + table([("", ""), ("Контрагент", ""), ("Статья", ""), ("Назначение", ""), ("Сумма", "n")], rows, rc))
    else:
        parts.append(f'<h3>Платежи за день</h3>' + empty(f"За {fmt_dm(day)} движения по счетам нет."))

    if m["forecast"] and m["total"] is not None:
        rows, rc = [], []
        for f in m["forecast"]:
            lv = "crit" if f["balance"] < 0 else ("warn" if s.min_cash and f["balance"] < s.min_cash else "")
            rows.append([f'{fmt_dm(f["date"])} <span class="muted">{WD_SHORT[f["date"].weekday()]}</span>', fmt_money(f["in"]) if f["in"] else "—",
                         fmt_money(f["out"]) if f["out"] else "—", f'<b class="{lv + "-t" if lv else ""}">{fmt_money(f["balance"])}</b>'])
            rc.append(lv)
        note = ("В первый день учтена оплата просроченной кредиторки " + fmt_money(m["overdue_pay"]) + ". " if m["overdue_pay"] else "")
        note += ("Просроченная дебиторка " + fmt_money(m["overdue_recv"]) + " в прогноз не включена: если её вернут, остаток будет выше." if m["overdue_recv"] else "")
        parts.append(f'<h3>Прогноз остатка на {s.horizon} дней по срокам оплат</h3><div class="card">{svg_cash(m["total"], m["forecast"], day, s.min_cash)}</div>'
                     + table([("Дата", ""), ("Ждём от покупателей", "n"), ("Платим поставщикам", "n"), ("Остаток", "n")], rows, rc)
                     + f'<p class="note">{note}</p>')
    if m["by_article_out"]:
        mx = m["by_article_out"][0][1]
        rows = [[E(a), fmt_money(v), fmt_pct(v / m["mtd_out"] * 100), bar(v / mx * 100)] for a, v in m["by_article_out"]]
        parts.append(f'<details><summary>Расходы с начала месяца по статьям</summary>' + table([("Статья", ""), ("Сумма", "n"), ("Доля", "n"), ("", "")], rows,
                     total=["Итого", fmt_money(m["mtd_out"]), "100 %", ""]) + "</details>")
    aside = f'Итого {fmt_money(m["total"])}' if m["total"] is not None else ""
    return section("money", "Деньги", aside, "".join(parts))


def render_debts(r: dict) -> str:
    d, day, s = r["debts"], r["day"], r["settings"]
    if not d["has_data"]:
        return section("debts", "Долги", "", empty("Нет таблицы debts — выгрузите дебиторку и кредиторку по документам."))
    colors = ["var(--good)", "var(--warn)", "#D98A2B", "var(--crit)", "#7E1F12"]

    def block(kind: str, title: str, who: str) -> str:
        x = d[kind]
        if not x["items"]:
            return f'<div class="card"><h3 style="margin-top:0">{title}</h3><p class="muted">Долгов нет.</p></div>'
        total = x["total"] or 1
        stack = "".join(f'<i style="width:{a["sum"] / total * 100:.2f}%;background:{colors[i]}" title="{E(a["title"])}: {fmt_money(a["sum"])}"></i>' for i, a in enumerate(x["aging"]) if a["sum"])
        leg = "".join(f'<span style="--c:{colors[i]}">{E(a["title"])}: <b class="num">{fmt_money(a["sum"], True)}</b></span>' for i, a in enumerate(x["aging"]) if a["sum"])
        return (f'<div class="card"><h3 style="margin-top:0">{title}</h3><dl class="kv"><dt>Всего</dt><dd>{fmt_money(x["total"])}</dd>'
                f'<dt>Просрочено</dt><dd class="{"crit-t" if x["overdue"] else ""}">{fmt_money(x["overdue"])} ({fmt_pct(x["overdue"] / total * 100)})</dd>'
                f'<dt>Срок в ближайшие {s.horizon} дн.</dt><dd>{fmt_money(x["soon_sum"])}</dd>'
                f'<dt>{who}</dt><dd>{len(x["counterparties"])}</dd></dl>'
                f'<div class="stack">{stack}</div><div class="legend">{leg}</div></div>')

    parts = ['<div class="grid2">', block("recv", "Нам должны покупатели", "Должников"), block("pay", "Мы должны поставщикам", "Кредиторов"), "</div>"]

    def cp_table(kind: str, limit: int = 10) -> str:
        cps = d[kind]["counterparties"]
        rows, rc = [], []
        for c in cps:
            lv = "crit" if c["late"] > 60 else ("warn" if c["late"] > 0 else "")
            rows.append([E(c["name"]) + (f'<div class="muted small">{E(c["comment"])}</div>' if c["comment"] else ""),
                         fmt_money(c["total"]), f'<span class="{"crit-t" if c["overdue"] else "muted"}">{fmt_money(c["overdue"]) if c["overdue"] else "—"}</span>',
                         f'<span class="pill {lv or "ok"}">{c["late"]} дн.</span>' if c["late"] else '<span class="pill ok">в срок</span>',
                         fmt_dm(c["next_due"]) if c["next_due"] else "—", str(c["docs"])])
            rc.append(lv)
        head = [("Контрагент", ""), ("Долг", "n"), ("Просрочено", "n"), ("Просрочка", "n"), ("Ближайший срок", "n"), ("Док.", "n")]
        main = table(head, rows[:limit], rc[:limit])
        if len(rows) > limit:
            main += f'<details><summary>Остальные {len(rows) - limit}</summary>' + table(head, rows[limit:], rc[limit:]) + "</details>"
        return main

    parts.append("<h3>Должники — сначала самые просроченные</h3>" + cp_table("recv"))
    soon = d["recv"]["soon"]
    if soon:
        rows = [[fmt_dm(x["due"]), E(x["counterparty"]), E(x.get("doc") or ""), fmt_money(x["left"])] for x in soon]
        parts.append(f"<details open><summary>Ожидаемые поступления в ближайшие {s.horizon} дней — {fmt_money(d['recv']['soon_sum'])}</summary>"
                     + table([("Срок", ""), ("Покупатель", ""), ("Документ", ""), ("Сумма", "n")], rows) + "</details>")
    parts.append("<h3>Кредиторы</h3>" + cp_table("pay"))
    soon = d["pay"]["soon"]
    over = [x for x in d["pay"]["items"] if x["late"] > 0]
    if soon or over:
        rows, rc = [], []
        for x in over + soon:
            rows.append([fmt_dm(x["due"]) + (f' <span class="pill crit">просрочен {x["late"]} дн.</span>' if x["late"] > 0 else ""),
                         E(x["counterparty"]), E(x.get("doc") or ""), fmt_money(x["left"])])
            rc.append("crit" if x["late"] > 0 else "")
        parts.append(f"<details open><summary>Платежи к оплате: просроченные и на {s.horizon} дней вперёд — {fmt_money(sum(x['left'] for x in over + soon))}</summary>"
                     + table([("Срок", ""), ("Получатель", ""), ("Основание", ""), ("Сумма", "n")], rows, rc) + "</details>")
    net = d["recv"]["total"] - d["pay"]["total"]
    return section("debts", "Долги", f'Сальдо расчётов <b class="{cls_change(net)}">{fmt_signed(net, lambda v: fmt_money(v, True))}</b>', "".join(parts))


COVER = {"stock": ("ok", "есть на складе"), "produce": ("ok", "успеваем выпустить"), "risk": ("warn", "не успеваем"),
         "late": ("crit", "просрочен"), "no_date": ("mute", "без срока")}


def render_orders(r: dict) -> str:
    o, day, s = r["orders"], r["day"], r["settings"]
    if not o["has_data"]:
        return section("orders", "Заказы и отгрузки", "", empty("Нет таблицы orders — выгрузите заказы покупателей."))
    tiles = "".join([
        tile("Новые заказы за день", f'{len(o["new"])}', f'{fmt_t(sum(x["qty"] or 0 for x in o["new"]))}, {fmt_money(sum(x["sum"] for x in o["new"]), True)}'),
        tile("Отгружено за день", fmt_t(o["ship_today_t"]), fmt_money(o["ship_today_sum"], True)),
        tile("Отгружено с 1-го числа", fmt_t(o["ship_mtd_t"], 0), fmt_money(o["ship_mtd_sum"], True)),
        tile("Принято заказов в месяце", fmt_t(o["month_new_t"], 0), fmt_money(o["month_new_sum"], True)),
    ])
    parts = [f'<div class="tiles">{tiles}</div>']

    rows = []
    for p in o["products"]:
        cover = f'{fmt_num(p["days"], 1)} сут.' if p["days"] is not None else '<span class="muted">нет выпуска</span>'
        rows.append([E(p["product"]), str(p["count"]), fmt_t(p["left"]), fmt_money(p["sum"], True), fmt_t(p["fg"]), fmt_t(p["to_make"]),
                     fmt_t(p["rate"]) + "/сут.", cover])
    parts.append("<h3>Портфель по продуктам</h3>" + table(
        [("Продукт", ""), ("Заказов", "n"), ("Осталось отгрузить", "n"), ("Сумма", "n"), ("На складе", "n"), ("Нужно выпустить", "n"), ("Выпуск", "n"), ("Загрузка", "n")],
        rows, total=["Итого", str(len(o["open"])), fmt_t(o["backlog_t"]), fmt_money(o["backlog_sum"], True),
                     fmt_t(sum(p["fg"] for p in o["products"])), fmt_t(sum(p["to_make"] for p in o["products"])), "", ""]))
    parts.append('<p class="note">«Загрузка» — сколько суток работы при среднем выпуске последней недели нужно, чтобы закрыть портфель с учётом склада готовой продукции.</p>')

    def order_rows(lst):
        rows, rc = [], []
        for x in lst:
            lv, txt = COVER.get(x.get("cover"), ("mute", ""))
            if x.get("cover") == "late":
                txt = f'просрочен {x["late"]} дн.'
            elif x.get("ship_by") and x["ship_by"] < day:
                lv, txt = "crit", f'просрочен {x["late"]} дн., есть на складе'
            rows.append([f'<span class="mono">{E(x["number"])}</span>', E(x["client"]), E(x["product"]), fmt_t(x["qty"]), fmt_t(x["left"]),
                         fmt_dm(x.get("ship_by")), f'<span class="pill {lv}">{txt}</span>', fmt_money(x["sum_left"], True)])
            rc.append(lv if lv in ("crit", "warn") else "")
        return rows, rc

    head = [("Заказ", ""), ("Клиент", ""), ("Продукт", ""), ("Объём", "n"), ("Осталось", "n"), ("Срок", "n"), ("Статус", ""), ("Сумма", "n")]
    if o["overdue"]:
        parts.append(f'<h3>Просроченные отгрузки — {len(o["overdue"])}</h3>' + table(head, *order_rows(o["overdue"])))
    if o["soon"]:
        parts.append(f'<h3>Отгрузить в ближайшие {s.horizon} дней</h3>' + table(head, *order_rows(o["soon"])))
        parts.append('<p class="note">Склад готовой продукции распределяется по заказам в порядке сроков, остаток — из будущего выпуска по среднему за неделю.</p>')
    if o["new"]:
        rows = [[f'<span class="mono">{E(x["number"])}</span>', E(x["client"]), E(x["product"]), fmt_t(x["qty"]), fmt_money(x.get("price")),
                 fmt_dm(x.get("ship_by")), fmt_money(x["sum"])] for x in o["new"]]
        parts.append(f'<h3>Новые заказы за {fmt_dm(day)}</h3>' + table(
            [("Заказ", ""), ("Клиент", ""), ("Продукт", ""), ("Объём", "n"), ("Цена за т", "n"), ("Срок", "n"), ("Сумма", "n")], rows))
    if o["ship_today"]:
        rows = [[f'<span class="mono">{E(x["order"])}</span>', E(x["client"]), E(x["product"]), fmt_t(x["qty"]), fmt_money(x["sum"])] for x in o["ship_today"]]
        parts.append(f'<h3>Отгрузки за {fmt_dm(day)}</h3>' + table([("Заказ", ""), ("Клиент", ""), ("Продукт", ""), ("Объём", "n"), ("Сумма", "n")], rows))
    rest = [x for x in o["open"] if x not in o["overdue"] and x not in o["soon"]]
    if rest:
        rest.sort(key=lambda x: x.get("ship_by") or dt.date.max)
        parts.append(f'<details><summary>Остальные открытые заказы — {len(rest)}</summary>' + table(head, *order_rows(rest)) + "</details>")
    return section("orders", "Заказы и отгрузки", f'Портфель {fmt_t(o["backlog_t"], 0)} на {fmt_money(o["backlog_sum"], True)}', "".join(parts))


def render_output(r: dict) -> str:
    out, day, s = r["output"], r["day"], r["settings"]
    if not out["has_data"]:
        return section("output", "Выпуск продукции", "", empty("Нет таблицы output — внесите выпуск по сменам."))
    t = out["total"]
    parts = []
    if not out["has_today"]:
        parts.append(empty(f"За {fmt_dm(day)} выпуск ещё не внесён. Ниже — итоги месяца."))
    rows = []
    for p in out["products"]:
        dr = p["defect"] / p["fact"] * 100 if p["fact"] else None
        rows.append([E(p["product"]), fmt_t(p["plan"]), fmt_t(p["fact"]), pct_cell(p["fact"], p["plan"]),
                     bar(p["fact"] / p["plan"] * 100 if p["plan"] else None, pct_level(p["fact"] / p["plan"] * 100 if p["plan"] else None)),
                     f'<span class="{"warn-t" if dr and dr > s.defect_norm else ""}">{fmt_t(p["defect"], 2)}</span>'])
    dr_tot = (t["defect"] or 0) / t["fact"] * 100 if t["fact"] else None
    parts.append(f"<h3>За сутки {fmt_dm(day)}</h3>" + table([("Продукт", ""), ("План", "n"), ("Факт", "n"), ("%", "n"), ("", ""), ("Брак", "n")], rows,
                 total=["Итого", fmt_t(t["plan"]), fmt_t(t["fact"]), pct_cell(t["fact"], t["plan"]), "",
                        fmt_t(t["defect"], 2) + (f' <span class="muted">({fmt_pct(dr_tot, 1)})</span>' if dr_tot is not None else "")]))

    if out["rows"]:
        rows = [[E(x.get("shift") or "—"), E(x["product"]), f'<span class="mono">{E(x.get("batch") or "—")}</span>', fmt_t(x.get("plan")), fmt_t(x["fact"]),
                 pct_cell(x["fact"], x.get("plan")), fmt_t(x.get("defect"), 2)] for x in out["rows"]]
        parts.append("<details><summary>По сменам и партиям</summary>" + table(
            [("Смена", ""), ("Продукт", ""), ("Партия", ""), ("План", "n"), ("Факт", "n"), ("%", "n"), ("Брак", "n")], rows) + "</details>")

    parts.append(f'<h3>Последние 14 суток, т</h3><div class="card">{svg_output(out["history"])}'
                 '<div class="legend" style="margin-top:6px"><span style="--c:var(--accent)">факт ≥ 90 % плана</span><span style="--c:var(--warn)">75–90 %</span>'
                 '<span style="--c:var(--crit)">меньше 75 %</span><span style="--c:var(--ink)">план (штрих)</span></div></div>')

    mname = MONTHS_NOM[day.month - 1]
    rows = []
    for p in out["products"]:
        mp = p["month_plan"]
        meter = ""
        if mp:
            meter = (f'<div class="meter" title="Выполнено {fmt_pct(p["fact_mtd"] / mp * 100)}, прогноз {fmt_pct(p["forecast"] / mp * 100)}">'
                     f'<i style="width:{min(p["forecast"] / mp * 100, 100):.0f}%;opacity:.3"></i><i style="width:{min(p["fact_mtd"] / mp * 100, 100):.0f}%"></i>'
                     f'<b style="left:{min(p["plan_mtd"] / mp * 100, 100) if p["plan_mtd"] else 0:.0f}%"></b></div>')
        rows.append([E(p["product"]) + meter, fmt_t(p["plan_mtd"], 0), fmt_t(p["fact_mtd"], 0), pct_cell(p["fact_mtd"], p["plan_mtd"]),
                     fmt_t(mp, 0), fmt_t(p["forecast"], 0), pct_cell(p["forecast"], mp),
                     fmt_t(p["need_per_day"]) if p["need_per_day"] is not None else "—", fmt_t(p["rate"])])
    parts.append(f"<h3>{mname.capitalize()}: план и прогноз</h3>" + table(
        [("Продукт", ""), ("План на сегодня", "n"), ("Факт", "n"), ("%", "n"), ("План месяца", "n"), ("Прогноз", "n"), ("% месяца", "n"),
         ("Нужно в сутки", "n"), ("Сейчас в сутки", "n")], rows,
        total=["Итого", fmt_t(t["plan_mtd"], 0), fmt_t(t["fact_mtd"], 0), pct_cell(t["fact_mtd"], t["plan_mtd"]), fmt_t(t["month_plan"], 0),
               fmt_t(t["forecast"], 0), pct_cell(t["forecast"], t["month_plan"]), fmt_t(t["need_per_day"]), fmt_t(t["rate"])]))
    parts.append(f'<p class="note">Прошло {out["wd_done"]} из {out["wd_total"]} рабочих суток. Прогноз = факт + средний выпуск последней недели × '
                 f'{out["wd_left"]} оставшихся суток. На полосе: тёмное — факт, светлое — прогноз, черта — где должны быть сегодня по плану.</p>')
    if out["consumption"]:
        rows = [[E(k), fmt_num(v["qty"], 2 if v["qty"] < 10 else 1) + " " + E(v["unit"])] for k, v in sorted(out["consumption"].items(), key=lambda kv: kv[0]) if v["qty"]]
        parts.append(f"<details><summary>Расход сырья за сутки по нормам</summary>" + table([("Материал", ""), ("Расход", "n")], rows) +
                     '<p class="note">Расчётный расход: факт выпуска × норма. Сравнение с фактическим списанием — в отчёте о себестоимости.</p></details>')
    aside = f'{fmt_t(t["fact"])} за сутки · {fmt_t(t["fact_mtd"], 0)} с 1-го числа'
    return section("output", "Выпуск продукции", aside, "".join(parts))


STATUS = {"crit": ("crit", "нехватка"), "warn": ("warn", "заказать"), "ok": ("ok", "в норме")}


def render_materials(r: dict) -> str:
    mat, day, s = r["materials"], r["day"], r["settings"]
    if not mat["has_data"]:
        return section("materials", "Сырьё", "", empty("Нет таблицы stock — внесите остатки сырья."))
    rows, rc = [], []
    for i in mat["items"]:
        lv, txt = STATUS[i["status"]]
        u = E(i.get("unit") or "")
        dg = 0 if norm_name(i.get("unit")) in ("шт", "шт.") else (2 if (i["qty"] or 0) < 10 else 1)
        cover = "—" if i["cover"] is None else f'{fmt_num(i["cover"], 1)} дн.'
        if i["cover"] is not None and i.get("transit"):
            cover += f'<div class="muted small">с поставкой {fmt_num(i["cover_all"], 1)}</div>'
        transit = "—"
        if i.get("transit"):
            transit = f'{fmt_num(i["transit"], dg)} {u}' + (f'<div class="muted small">{fmt_dm(i.get("arrival"))}</div>' if i.get("arrival") else "")
        rows.append([f'<b>{E(i["material"])}</b>' + (f'<div class="muted small">{E(i["note"])}</div>' if i["note"] else ""),
                     f'{fmt_num(i["qty"], dg)} {u}', f'{fmt_num(i.get("min"), dg)} {u}' if i.get("min") else "—",
                     f'{fmt_num(i["daily"], 2 if i["daily"] < 10 else 1)} {u}' if i["daily"] else "—", cover, transit,
                     f'{i["lead"]:.0f} дн.' if i.get("lead") is not None else "—",
                     f'<span class="pill {lv}">{txt}</span>',
                     (f'{fmt_num(i["order_qty"], dg)} {u}' + (f'<div class="muted small">{fmt_money(i["order_sum"], True)}</div>' if i["order_sum"] else "")) if i["order_qty"] and i["status"] != "ok" else "—"])
        rc.append(lv if lv != "ok" else "")
    parts = [table([("Материал", ""), ("Остаток", "n"), ("Мин. запас", "n"), ("Расход в сутки", "n"), ("Хватит на", "n"), ("В пути", "n"),
                    ("Срок поставки", "n"), ("Статус", ""), ("Заказать", "n")], rows, rc)]
    parts.append(f'<p class="note">Суточный расход — по нормам и темпу, нужному для выполнения плана месяца (но не ниже фактического). '
                 f'«Заказать» — чтобы хватило на срок поставки плюс {s.horizon} дн. и остался минимальный запас. '
                 f'Склад сырья по учётным ценам: {fmt_money(mat["value"], True)}.</p>')
    need = [i for i in mat["items"] if i["need_orders"]]
    if need:
        rows, rc = [], []
        for i in need:
            u = E(i.get("unit") or "")
            dg = 0 if norm_name(i.get("unit")) in ("шт", "шт.") else 2
            short = i["need_orders"] - (i["qty"] or 0) - (i.get("transit") or 0)
            rows.append([E(i["material"]), f'{fmt_num(i["need_orders"], dg)} {u}', f'{fmt_num(i["qty"], dg)} {u}', f'{fmt_num(i.get("transit") or 0, dg)} {u}',
                         f'<span class="crit-t">{fmt_num(short, dg)} {u}</span>' if short > 0 else '<span class="good-t">хватает</span>'])
            rc.append("crit" if short > 0 else "")
        parts.append("<h3>Хватит ли сырья на портфель заказов</h3>" + table(
            [("Материал", ""), ("Нужно на заказы", "n"), ("Остаток", "n"), ("В пути", "n"), ("Не хватает", "n")], rows, rc))
    ph = mat["phenol"]
    if ph:
        last, wk, first = ph["last"], ph["week"], ph["first"]
        chw = (last["value"] / wk["value"] - 1) * 100 if wk["value"] else None
        chm = (last["value"] / first["value"] - 1) * 100 if first["value"] else None
        our = next((i for i in mat["items"] if norm_name(i["material"]).startswith("фенол")), None)
        extra = ""
        if our and our.get("price"):
            diff = last["value"] - our["price"]
            extra = (f'<dt>Наша учётная цена</dt><dd>{fmt_money(our["price"])}</dd>'
                     f'<dt>Рынок {"дороже" if diff > 0 else "дешевле"} на</dt><dd class="{"crit-t" if diff > 0 else "good-t"}">{fmt_money(abs(diff))}/т</dd>')
            if our["order_qty"]:
                extra += f'<dt>Закупка {fmt_num(our["order_qty"], 0)} т по рынку</dt><dd>{fmt_money(our["order_qty"] * last["value"], True)}</dd>'
        parts.append(f'<h3>Фенол на бирже (индикатор FEN, SPIMEX)</h3><div class="grid2" style="margin-top:0"><div class="card"><dl class="kv">'
                     f'<dt>На {fmt_dm(last["date"])}</dt><dd>{fmt_money(last["value"])}/т</dd>'
                     f'<dt>За неделю</dt><dd class="{cls_change(chw, False)}">{fmt_pct(chw, 1, True)}</dd>'
                     f'<dt>С {fmt_dm(first["date"])}</dt><dd class="{cls_change(chm, False)}">{fmt_pct(chm, 1, True)}</dd>{extra}</dl></div>'
                     f'<div class="card">{svg_spark(ph["series"])}<p class="note">Данные собирает spimex_phenol/phenol_index.py.</p></div></div>')
    aside = (f'{len(mat["crit"])} {plural(len(mat["crit"]), "позиция", "позиции", "позиций")} в нехватке' if mat["crit"] else "нехватки нет")
    if mat["order_sum"]:
        aside += f' · закупка на {fmt_money(mat["order_sum"], True)}'
    return section("materials", "Сырьё и нехватка", aside, "".join(parts))


def render(r: dict, generated: dt.datetime, sources: list[str]) -> str:
    day, s = r["day"], r["settings"]
    n = lambda lvl, sec: sum(1 for a in r["alerts"] if a.level == lvl and a.section == sec)

    def chip(id_, title):
        c, w = n("crit", id_), n("warn", id_)
        b = f'<span class="badge crit">{c}</span>' if c else (f'<span class="badge warn">{w}</span>' if w else "")
        return f'<a href="#{id_}">{title}{b}</a>'
    nav = "".join(chip(*x) for x in [("summary", "Главное"), ("money", "Деньги"), ("debts", "Долги"), ("orders", "Заказы"), ("output", "Выпуск"), ("materials", "Сырьё")])
    company = E(s.company) if s.company else "Ежедневный отчёт"
    body = "".join([render_summary(r), render_money(r), render_debts(r), render_orders(r), render_output(r), render_materials(r)])
    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700&family=Manrope:wght@700;800&family=JetBrains+Mono:wght@500&display=swap">')
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Отчёт руководителю {fmt_dm(day)}</title>
<meta name="claude:viewport-fit-sides" content="cover">
{fonts}
<style>{CSS}</style>
<div class="wrap">
<header class="top"><div><div class="eyebrow">{company}</div><h1>Отчёт руководителю за {fmt_date(day, True)}</h1></div>
<div class="muted small">Сформирован {generated.strftime("%d.%m.%Y %H:%M")}</div></header>
<nav class="toc" aria-label="Разделы">{nav}</nav>
{body}
<footer>Источники: {E(", ".join(sources)) or "—"}. Суммы в рублях по данным учёта; отгрузки оцениваются по ценам заказов.
Пороги: прогноз на {s.horizon} дн., допустимый брак {fmt_pct(s.defect_norm, 1)}, неснижаемый остаток {fmt_money(s.min_cash)}.</footer>
</div>
"""


# ================================================================== запуск
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, help="папка с таблицами (по умолчанию production/data)")
    ap.add_argument("--demo", action="store_true", help="взять демо-данные из production/sample_data")
    ap.add_argument("--date", help="дата отчёта ДД.ММ.ГГГГ (по умолчанию — последняя дата в данных)")
    ap.add_argument("--out", type=Path, help="папка для отчётов (по умолчанию <данные>/reports)")
    ap.add_argument("--phenol", type=Path, help="таблица индикатора фенола phenol_index.csv")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    folder = HERE / "sample_data" if args.demo else (args.data or HERE / "data")
    if not folder.is_dir():
        log.error("нет папки с данными %s. Укажите --data или запустите с --demo", folder)
        return 2
    try:
        data = Data.load(folder, args.phenol)
        from common import parse_date
        day = parse_date(args.date) if args.date else (data.latest_date() or dt.date.today())
        settings = Settings.load(folder)
        report = build(data, day, settings)
    except DataError as e:
        log.error("%s", e)
        return 2

    out_dir = args.out or folder / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted(t.path.name for t in vars(data).values() if isinstance(t, Table) and t.path)
    page = render(report, dt.datetime.now(), sources)
    path = out_dir / f"daily_{day.isoformat()}.html"
    path.write_text(page, encoding="utf-8")
    text = summary_line(report)
    (out_dir / "daily_last.txt").write_text(text + "\n", encoding="utf-8")
    log.info("отчёт: %s", path)
    for a in report["alerts"]:
        if a.level in ("crit", "warn"):
            log.info("%s %s", "‼" if a.level == "crit" else "•", a.title)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
