"""Schedule-adjusted projections, tracked but not used (stage 1 of the schedule framework).

Every research run saves, for each player on the slate, the projection the model uses and the one it would use if his
baseline were first put on a neutral schedule (players.json `sos`). After the games, both are graded against what he
actually did, on the DraftKings day file's actuals, and against DraftKings' line when there was one:
  - average miss (lower is better) for the current projection vs the schedule-adjusted one;
  - on priced lines where the two disagree about the side, which one was right.
Nothing here changes a projection or a pick. Stage 2 (the adjusted number replaces the baseline) needs both this live
record and tests/backtest_schedule.py to say the adjusted number is more accurate.
"""
from . import store

KEEP_DAYS = 60
MIN_GAP = 0.01          # skip players whose adjustment is under 1%: nothing to compare


def record(day, players):
    T = store.load("sos_track.json", {"days": {}})
    rows = {}
    for pid, p in players.items():
        if p.get("out") or p.get("state") != "pre":
            continue
        for s, v in (p.get("sos") or {}).items():
            pr = (p.get("proj") or {}).get(s)
            if not pr or not v or abs(v[2] - 1) < MIN_GAP:
                continue
            rows[f"{pid}|{s}"] = [round(pr["proj"], 2), v[5], v[2]]
    old = T["days"].get(day, {})
    for k, r in old.items():                  # games already under way keep the morning's numbers
        if k not in rows:
            rows[k] = r
    T["days"][day] = rows
    store.save("sos_track.json", T, compact=True)


def grade():
    T = store.load("sos_track.json", {"days": {}})
    summ = {"n": 0, "mae_now": 0.0, "mae_sos": 0.0, "lines": 0, "side_now": 0, "side_sos": 0, "by_stat": {}}
    for day, rows in sorted(T["days"].items()):
        dk = store.load(f"dk/{day}.json", None)
        if not dk:
            continue
        for k, r in rows.items():
            d = (dk.get("props") or {}).get(k)
            if len(r) < 4:
                if not d or d.get("actual") is None:
                    continue
                line = (d.get("c") or [None])[0]
                r.extend([d["actual"], line])
            now, sos, _, act, line = r[:5]
            st = k.split("|")[1]
            b = summ["by_stat"].setdefault(st, {"n": 0, "mae_now": 0.0, "mae_sos": 0.0})
            for x in (summ, b):
                x["n"] += 1
                x["mae_now"] += abs(act - now)
                x["mae_sos"] += abs(act - sos)
            if line is not None and act != line and (now > line) != (sos > line):
                summ["lines"] += 1
                summ["side_now"] += (now > line) == (act > line)
                summ["side_sos"] += (sos > line) == (act > line)
    for x in [summ, *summ["by_stat"].values()]:
        if x["n"]:
            x["mae_now"] = round(x["mae_now"] / x["n"], 3)
            x["mae_sos"] = round(x["mae_sos"] / x["n"], 3)
    T["days"] = dict(sorted(T["days"].items())[-KEEP_DAYS:])
    T["summary"] = summ
    store.save("sos_track.json", T, compact=True)
    return summ
