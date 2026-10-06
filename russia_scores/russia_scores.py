#!/usr/bin/env python3
"""Уведомления об изменении счёта в матчах сборной России в игровых видах спорта.

Скрипт смотрит live-матчи на Sofascore (футбол, хоккей, баскетбол, волейбол,
гандбол, футзал, водное поло, хоккей с мячом и др.), находит те, где играет
сборная России (в том числе женская и молодёжные), и сравнивает счёт с прошлым
запуском. Об изменениях сообщает:

  * уведомлением Android — если установлен Termux:API (termux-notification);
  * сообщением в Telegram — если заданы TG_BOT_TOKEN и TG_CHAT_ID;
  * строкой в stdout — всегда.

Состояние (счёт на прошлом запуске) хранится в JSON-файле, поэтому скрипт
удобно запускать раз в минуту из cron:

  python russia_scores.py                 # один проход
  python russia_scores.py --watch 60      # проверять каждые 60 секунд
  python russia_scores.py --test-notify   # проверить, что уведомления доходят
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

log = logging.getLogger("russia_scores")

API = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Mobile Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
}
DEFAULT_STATE = Path.home() / ".russia_scores.json"
DEFAULT_TEAM = r"Russia\b"

# вид спорта на Sofascore -> (значок, режим уведомлений)
#   score  — каждое изменение счёта (голы, шайбы, выигранные партии);
#   period — только смена периода/четверти и финал: в баскетболе и регби
#            очки меняются слишком часто
SPORTS = {
    "football": ("⚽", "score"),
    "ice-hockey": ("🏒", "score"),
    "basketball": ("🏀", "period"),
    "volleyball": ("🏐", "score"),
    "handball": ("🤾", "score"),
    "futsal": ("⚽", "score"),
    "waterpolo": ("🤽", "score"),
    "bandy": ("🏑", "score"),
    "beach-volley": ("🏐", "score"),
    "field-hockey": ("🏑", "score"),
    "floorball": ("🏑", "score"),
    "rugby": ("🏉", "period"),
}
SET_SPORTS = {"volleyball", "beach-volley"}  # в счёте — выигранные партии

STATUS_RU = {
    "1st half": "1-й тайм", "2nd half": "2-й тайм", "Halftime": "перерыв",
    "1st period": "1-й период", "2nd period": "2-й период", "3rd period": "3-й период",
    "1st quarter": "1-я четверть", "2nd quarter": "2-я четверть",
    "3rd quarter": "3-я четверть", "4th quarter": "4-я четверть",
    "1st set": "1-я партия", "2nd set": "2-я партия", "3rd set": "3-я партия",
    "4th set": "4-я партия", "5th set": "5-я партия",
    "Pause": "перерыв", "Break": "перерыв", "Awaiting extra time": "перед овертаймом",
    "Overtime": "овертайм", "Extra time": "дополнительное время",
    "1st extra": "1-й доп. тайм", "2nd extra": "2-й доп. тайм",
    "Penalties": "серия пенальти", "Awaiting penalties": "перед серией пенальти",
    "Ended": "завершён", "AET": "после доп. времени", "AP": "после пенальти",
    "Interrupted": "прерван", "Postponed": "перенесён", "Canceled": "отменён",
}


def ru_status(desc: str) -> str:
    return STATUS_RU.get(desc, desc)


def team_name(team: dict) -> str:
    tr = (team.get("fieldTranslations") or {}).get("nameTranslation") or {}
    name = tr.get("ru") or re.sub(r"^Russia\b", "Россия", team.get("name", "?"))
    if team.get("gender") == "F" and "жен" not in name:
        name += " (жен.)"
    return name


def is_target(team: dict, pattern: str) -> bool:
    return bool(team.get("national")) and bool(re.match(pattern, team.get("name", ""), re.I))


def score_of(event: dict) -> tuple[int, int]:
    def cur(side):
        s = event.get(side) or {}
        return int(s.get("current", s.get("display", 0)) or 0)
    return cur("homeScore"), cur("awayScore")


class Sofascore:
    def __init__(self, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout = timeout

    def get(self, path: str) -> dict:
        resp = self.session.get(f"{API}/{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def live(self, sport: str) -> list[dict]:
        return self.get(f"sport/{sport}/events/live").get("events", [])

    def event(self, event_id) -> dict:
        return self.get(f"event/{event_id}").get("event", {})


# ---------- уведомления ----------

def notify(title: str, body: str, args) -> None:
    print(f"{title}\n{body}\n", flush=True)
    if args.termux and shutil.which("termux-notification"):
        try:
            subprocess.run(["termux-notification", "--title", title, "--content", body,
                            "--priority", "high", "--sound", "--vibrate", "300,150,300"],
                           timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("termux-notification: %s", e)
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": f"{title}\n{body}"}, timeout=20).raise_for_status()
        except requests.RequestException as e:
            log.warning("Telegram: %s", e)


def describe(ev: dict, sport: str) -> str:
    """«⚽ Россия 2:1 Сербия» + строка со стадией и турниром."""
    icon = SPORTS.get(sport, ("🏆", ""))[0]
    h, a = score_of(ev)
    line = f"{icon} {team_name(ev['homeTeam'])} {h}:{a} {team_name(ev['awayTeam'])}"
    info = [ru_status((ev.get("status") or {}).get("description", ""))]
    tournament = (ev.get("tournament") or {}).get("name")
    if tournament:
        info.append(tournament)
    return line + "\n" + " · ".join(x for x in info if x)


def score_title(old: list[int], new: tuple[int, int], russia_home: bool, sport: str) -> str:
    ru_old, opp_old = (old[0], old[1]) if russia_home else (old[1], old[0])
    ru_new, opp_new = (new[0], new[1]) if russia_home else (new[1], new[0])
    sets = sport in SET_SPORTS
    if ru_new > ru_old and opp_new == opp_old:
        return "🇷🇺 Россия выигрывает партию!" if sets else "🇷🇺 Россия забивает!"
    if opp_new > opp_old and ru_new == ru_old:
        return "Соперник выигрывает партию" if sets else "Соперник забивает"
    return "Счёт изменился"


# ---------- основной проход ----------

def check(api: Sofascore, state: dict, args) -> int:
    """Один проход по live-матчам. Возвращает число отправленных уведомлений."""
    sent, seen, failed = 0, set(), 0
    for sport in args.sports:
        try:
            events = api.live(sport)
        except (requests.RequestException, ValueError) as e:
            log.warning("%s: не удалось получить live-матчи: %s", sport, e)
            failed += 1
            continue
        for ev in events:
            home, away = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
            russia_home = is_target(home, args.team)
            if not (russia_home or is_target(away, args.team)):
                continue
            key = str(ev["id"])
            seen.add(key)
            score = score_of(ev)
            status = (ev.get("status") or {}).get("description", "")
            mode = "score" if args.every_point else SPORTS.get(sport, ("", "score"))[1]
            old = state.get(key)
            title = None
            if old is None:
                title = "Матч сборной России идёт"
            elif mode == "score" and list(score) != old["score"]:
                title = score_title(old["score"], score, russia_home, sport)
            elif mode == "period" and status != old["status"]:
                title = f"Счёт: {ru_status(status) or 'обновление'}"
            if title:
                notify(title, describe(ev, sport), args)
                sent += 1
            state[key] = {"sport": sport, "score": list(score), "status": status,
                          "russia_home": russia_home, "seen": time.time()}

    if failed == len(args.sports):
        return sent  # сети нет — не считаем, что матчи закончились

    # матч пропал из live — значит, закончился (или прерван): сообщаем итог
    for key in [k for k in state if k not in seen]:
        old = state[key]
        try:
            ev = api.event(key)
        except (requests.RequestException, ValueError) as e:
            log.warning("матч %s: не удалось получить итог: %s", key, e)
            if time.time() - old.get("seen", 0) > 6 * 3600:
                del state[key]
            continue
        status_type = (ev.get("status") or {}).get("type")
        if status_type == "inprogress":
            continue  # временно выпал из списка live
        notify("🏁 Матч завершён" if status_type == "finished" else "Матч остановлен",
               describe(ev, old["sport"]), args)
        sent += 1
        del state[key]
    return sent


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None, api: Sofascore | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE, help="файл состояния (по умолчанию: %(default)s)")
    ap.add_argument("--sports", default=",".join(SPORTS),
                    help="виды спорта через запятую (по умолчанию: все — %(default)s)")
    ap.add_argument("--team", default=DEFAULT_TEAM,
                    help="регулярное выражение для названия сборной (по умолчанию: %(default)s)")
    ap.add_argument("--every-point", action="store_true",
                    help="сообщать о каждом изменении счёта и в баскетболе/регби")
    ap.add_argument("--watch", type=int, metavar="СЕК", help="не выходить, а проверять каждые СЕК секунд")
    ap.add_argument("--no-termux", dest="termux", action="store_false", help="не показывать уведомления Android")
    ap.add_argument("--test-notify", action="store_true", help="отправить пробное уведомление и выйти")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    args.sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.test_notify:
        notify("🇷🇺 Россия забивает! (проверка)", "⚽ Россия 1:0 Сербия\n1-й тайм · Товарищеский матч", args)
        return 0

    api = api or Sofascore()
    while True:
        state = load_state(args.state)
        sent = check(api, state, args)
        save_state(args.state, state)
        log.debug("уведомлений: %d, матчей России в эфире: %d", sent, len(state))
        if not args.watch:
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
