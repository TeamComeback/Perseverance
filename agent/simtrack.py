"""Grade the overnight simulations against what actually happened: final scores, winners, totals and
every player's box score line. Running totals compare the sim with Vegas (scores) and with the matchup
model's projections (players), so it's clear whether the sim adds anything."""
from datetime import timedelta

from . import config, store
from . import nba_data as nd
from . import nba_model as nm

FILE = "sims_log.json"
STATS = ("pts", "reb", "ast", "tpm", "pra", "ra")
KEEP_DAYS = 21


def load():
    return store.load(FILE, {"pending": {}, "graded": {}, "agg": {}})


def _add(agg, k, *vals):
    a = agg.setdefault(k, [0] + [0.0] * len(vals))
    a[0] += 1
    for i, v in enumerate(vals):
        a[i + 1] = round(a[i + 1] + v, 3)


def grade(db):
    cut = store.now() - timedelta(hours=config.GRADE_AFTER_H)
    done = 0
    agg = db["agg"]
    for day in list(db["pending"]):
        for gid in list(db["pending"][day]):
            g = db["pending"][day][gid]
            if store.parse(g["start"]) > cut:
                continue
            try:
                box = nd.parse_summary(gid)
            except Exception as e:
                print(f"[simtrack] box {gid} failed: {e}")
                continue
            if not box:
                if store.parse(g["start"]) < store.now() - timedelta(days=3):
                    del db["pending"][day][gid]
                continue
            sc = {("home" if t["home"] else "away"): t["score"] for t in box["teams"].values()}
            ah, aa = sc.get("home"), sc.get("away")
            ph, pa = g["avg"]["home"], g["avg"]["away"]
            v = g.get("vegas") or {}
            rec = {"home": g["home"], "away": g["away"], "pred": [pa, ph], "final": [aa, ah], "win_home": g["win_home"], "misses": []}
            if ah is not None and aa is not None:
                _add(agg, "total", abs((ph + pa) - (ah + aa)), (ph + pa) - (ah + aa))
                _add(agg, "margin", abs((ph - pa) - (ah - aa)))
                _add(agg, "winner", 1.0 if (g["win_home"] >= 0.5) == (ah > aa) else 0.0)
                if v.get("total") is not None:
                    _add(agg, "total_vegas", abs(v["total"] - (ah + aa)))
                if v.get("spread_home") is not None:
                    _add(agg, "margin_vegas", abs(-v["spread_home"] - (ah - aa)))
                    _add(agg, "winner_vegas", 1.0 if (v["spread_home"] < 0) == (ah > aa) else 0.0)
            rows = {p[nd.P_ID]: p for p in box["players"]}
            for pid, x in g["p"].items():
                r = rows.get(pid)
                if r is None:
                    continue                                   # didn't play
                _add(agg, "min", abs(x["min"] - r[nd.P_MIN]), x["min"] - r[nd.P_MIN])
                for s in STATS:
                    act = nm._val(r, s)
                    _add(agg, s, abs(x[s] - act), x[s] - act)
                    pj = (x.get("proj") or {}).get(s)
                    if pj is not None:
                        _add(agg, s + "_proj", abs(pj - act))
                    if s == "pra":
                        rec["misses"].append([r[nd.P_NAME], x[s], act, x["min"], r[nd.P_MIN]])
            rec["misses"] = sorted(rec["misses"], key=lambda m: -abs(m[1] - m[2]))[:5]
            db["graded"].setdefault(day, {})[gid] = rec
            del db["pending"][day][gid]
            done += 1
        if not db["pending"][day]:
            del db["pending"][day]
    keep = sorted(db["graded"])[-KEEP_DAYS:]
    db["graded"] = {d: db["graded"][d] for d in keep}
    return done
