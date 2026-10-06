import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import russia_scores as rs  # noqa: E402


def team(name, national=True, gender="M", ru=None):
    t = {"name": name, "national": national, "gender": gender}
    if ru:
        t["fieldTranslations"] = {"nameTranslation": {"ru": ru}}
    return t


def event(eid, home, away, h, a, desc="1st half", type_="inprogress"):
    return {"id": eid, "homeTeam": home, "awayTeam": away,
            "homeScore": {"current": h}, "awayScore": {"current": a},
            "status": {"description": desc, "type": type_},
            "tournament": {"name": "Товарищеские матчи"}}


class FakeApi:
    def __init__(self):
        self.live_events = {}
        self.final = {}

    def live(self, sport):
        return self.live_events.get(sport, [])

    def event(self, eid):
        return self.final[str(eid)]


def run(api, tmp_path, capsys, *extra):
    assert rs.main(["--state", str(tmp_path / "s.json"), "--no-termux", *extra], api=api) == 0
    return capsys.readouterr().out


def test_goal_start_and_finish(tmp_path, capsys):
    api = FakeApi()
    rus, srb = team("Russia"), team("Serbia", ru="Сербия")
    api.live_events["football"] = [event(1, rus, srb, 0, 0),
                                   event(2, team("Zenit", national=False), team("Spartak", national=False), 1, 0)]
    out = run(api, tmp_path, capsys)
    assert "Матч сборной России идёт" in out and "⚽ Россия 0:0 Сербия" in out and "Zenit" not in out

    assert run(api, tmp_path, capsys) == ""  # ничего не изменилось

    api.live_events["football"] = [event(1, rus, srb, 1, 0)]
    out = run(api, tmp_path, capsys)
    assert "Россия забивает!" in out and "Россия 1:0 Сербия\n1-й тайм · Товарищеские матчи" in out

    api.live_events["football"] = [event(1, rus, srb, 1, 1, "2nd half")]
    assert "Соперник забивает" in run(api, tmp_path, capsys)

    api.live_events["football"] = []
    api.final["1"] = event(1, rus, srb, 2, 1, "Ended", "finished")
    out = run(api, tmp_path, capsys)
    assert "Матч завершён" in out and "Россия 2:1 Сербия\nзавершён" in out
    assert run(api, tmp_path, capsys) == ""


def test_away_team_and_volleyball_sets(tmp_path, capsys):
    api = FakeApi()
    rus_w = team("Russia", gender="F")
    api.live_events["volleyball"] = [event(5, team("Belarus"), rus_w, 0, 0, "1st set")]
    assert "Belarus 0:0 Россия (жен.)" in run(api, tmp_path, capsys)
    api.live_events["volleyball"] = [event(5, team("Belarus"), rus_w, 0, 1, "2nd set")]
    assert "Россия выигрывает партию!" in run(api, tmp_path, capsys)


def test_basketball_notifies_on_period_only(tmp_path, capsys):
    api = FakeApi()
    ev = lambda h, a, d: [event(7, team("Russia U20"), team("China"), h, a, d)]
    api.live_events["basketball"] = ev(10, 8, "1st quarter")
    assert "Россия U20 10:8 China" in run(api, tmp_path, capsys)
    api.live_events["basketball"] = ev(14, 8, "1st quarter")
    assert run(api, tmp_path, capsys) == ""
    api.live_events["basketball"] = ev(22, 20, "2nd quarter")
    assert "Счёт: 2-я четверть" in run(api, tmp_path, capsys)
    api.live_events["basketball"] = ev(24, 20, "2nd quarter")
    assert "Россия забивает" in run(api, tmp_path, capsys, "--every-point")


def test_network_failure_keeps_matches(tmp_path, capsys):
    api = FakeApi()
    api.live_events["ice-hockey"] = [event(9, team("Russia"), team("Kazakhstan"), 0, 0, "1st period")]
    run(api, tmp_path, capsys)

    def down(sport):
        raise rs.requests.ConnectionError("нет сети")
    api.live = down
    assert run(api, tmp_path, capsys) == ""
    assert "9" in rs.load_state(tmp_path / "s.json")


def test_test_notify(tmp_path, capsys):
    assert "проверка" in run(FakeApi(), tmp_path, capsys, "--test-notify")
