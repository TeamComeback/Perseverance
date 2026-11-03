"""Conviction track record. Every research run snapshots each player's lean and conviction for tonight;
after the games, each one is graded against his usual output (the norm for his projected minutes)
and folded into running totals by conviction level, stat and side. Grading against the book line
happens separately in lines.json for the players the agent priced."""
from datetime import timedelta

from . import config, store
from . import nba_data as nd
from . import nba_model as nm

FILE = "track.json"


def load():
    return store.load(FILE, {"pending": {}, "agg": {}, "graded_games": 0, "since": None})


def snapshot(db, day, players):
    """players: research players dict (with proj and cv). Only games that haven't started are (re)written."""
    games = {}
    for p in players.values():
        if p.get("out") or p.get("state") not in (None, "pre"):
            continue
        g = games.setdefault(p["game"], {"start": p["start"], "rows": []})
        for s, v in p["proj"].items():
            if v["base"] < (0.6 if s == "tpm" else 1.5) or s not in (p.get("cv") or {}):
                continue
            cvs = p["cv"][s]
            g["rows"].append([p["pid"], s, 1 if v["mult"] > 1 else -1, v["base"], v["proj"], cvs[2], cvs[3] if len(cvs) > 3 else ""])
    day_db = db["pending"].setdefault(day, {})
    for gid, g in games.items():
        day_db[gid] = g
    db["since"] = db.get("since") or day


def grade(db):
    cut = store.now() - timedelta(hours=config.GRADE_AFTER_H)
    done = 0
    for day in list(db["pending"]):
        for gid in list(db["pending"][day]):
            g = db["pending"][day][gid]
            if store.parse(g["start"]) > cut:
                continue
            try:
                box = nd.parse_summary(gid)
            except Exception as e:
                print(f"[track] box {gid} failed: {e}")
                continue
            if not box:
                if store.parse(g["start"]) < store.now() - timedelta(days=3):
                    del db["pending"][day][gid]          # never finished (postponed): drop it
                continue
            rows = {p[nd.P_ID]: p for p in box["players"]}
            for row in g["rows"]:
                pid, s, lean, base, proj, n = row[:6]
                codes = row[6] if len(row) > 6 else ""
                r = rows.get(pid)
                if r is None:
                    continue                             # did not play
                v = nm._val(r, s)
                if v == base:
                    continue
                k = f"{n}|{s}|{'O' if lean > 0 else 'U'}"
                a = db["agg"].setdefault(k, [0, 0, 0.0])
                a[0] += 1
                a[1] += 1 if (v - base) * lean > 0 else 0
                a[2] = round(a[2] + (v - base) * lean, 2)
                hit = 1 if (v - base) * lean > 0 else 0
                fa = db.setdefault("factors", {})
                for c in (["_all"] + [x for x in codes.split(",") if x]):
                    t = fa.setdefault(c, [0, 0])
                    t[0] += 1; t[1] += hit
            del db["pending"][day][gid]
            db["graded_games"] += 1
            done += 1
        if not db["pending"][day]:
            del db["pending"][day]
    return done
