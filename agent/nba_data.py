"""NBA box-score store built from ESPN's free public endpoints (no key).

Every completed game is saved once, compactly, in data/nba/games-<season>.json.
ESPN season ids: 2026 = the 2025-26 season, 2027 = 2026-27.
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from . import config

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
STORE = config.ROOT / "data" / "nba"
ET = timezone(timedelta(hours=-4))

# player row layout inside a game
P_ID, P_NAME, P_TEAM, P_GPOS, P_MIN, P_PTS, P_REB, P_AST, P_TPM, P_TPA, P_FGA, P_FTA, P_TOV, P_START = range(14)


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Perseverance/1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def season_of(d: date) -> int:
    return d.year + 1 if d.month >= 8 else d.year


def load(season):
    p = STORE / f"games-{season}.json"
    if p.exists():
        return json.load(open(p))
    return {"season": season, "games": {}, "dates_done": []}


def save(db):
    STORE.mkdir(parents=True, exist_ok=True)
    p = STORE / f"games-{db['season']}.json"
    tmp = p.with_suffix(".tmp")
    json.dump(db, open(tmp, "w"), separators=(",", ":"))
    tmp.replace(p)


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _ma(s):
    try:
        m, a = s.split("-")
        return float(m), float(a)
    except Exception:
        return 0.0, 0.0


def parse_summary(eid):
    d = get(f"{ESPN}/summary?event={eid}")
    comp = d["header"]["competitions"][0]
    if not comp["status"]["type"].get("completed"):
        return None
    teams = {c["team"]["abbreviation"]: {"home": c["homeAway"] == "home", "score": _num(c.get("score")),
                                         "name": c["team"]["displayName"], "nick": c["team"].get("name", "")}
             for c in comp["competitors"]}
    out = {"d": comp["date"][:10], "st": (d["header"].get("season") or {}).get("type", 2),
           "teams": teams, "tstats": {}, "players": []}
    for t in d["boxscore"].get("teams", []):
        ab = t["team"]["abbreviation"]
        st = {x["label"]: x["displayValue"] for x in t.get("statistics", [])}
        fgm, fga = _ma(st.get("FG", "0-0"))
        ftm, fta = _ma(st.get("FT", "0-0"))
        out["tstats"][ab] = {"fga": fga, "fta": fta, "oreb": _num(st.get("Offensive Rebounds")),
                             "tov": _num(st.get("Total Turnovers") or st.get("Turnovers"))}
    for t in d["boxscore"].get("players", []):
        ab = t["team"]["abbreviation"]
        for grp in t.get("statistics", []):
            labels = grp.get("labels") or []
            idx = {l: i for i, l in enumerate(labels)}
            for a in grp.get("athletes", []):
                s = a.get("stats") or []
                if a.get("didNotPlay") or not s:
                    continue
                mins = _num(s[idx["MIN"]]) if "MIN" in idx else 0
                if mins <= 0:
                    continue
                tpm, tpa = _ma(s[idx["3PT"]]) if "3PT" in idx else (0, 0)
                fgm, fga = _ma(s[idx["FG"]]) if "FG" in idx else (0, 0)
                ftm, fta = _ma(s[idx["FT"]]) if "FT" in idx else (0, 0)
                ath = a["athlete"]
                out["players"].append([ath["id"], ath["displayName"], ab,
                                       (ath.get("position") or {}).get("abbreviation", ""), mins,
                                       _num(s[idx["PTS"]]), _num(s[idx["REB"]]), _num(s[idx["AST"]]),
                                       tpm, tpa, fga, fta, _num(s[idx["TO"]]) if "TO" in idx else 0,
                                       1 if a.get("starter") else 0])
    return out


def scoreboard(d: date):
    return get(f"{ESPN}/scoreboard?dates={d:%Y%m%d}&limit=50").get("events", [])


def ingest(start: date, end: date, workers=6, log=print):
    """Fetch every completed game between start and end (inclusive)."""
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    dbs = {}
    todo = []
    for d in days:
        s = season_of(d)
        db = dbs.setdefault(s, load(s))
        if d.isoformat() in db["dates_done"]:
            continue
        todo.append(d)
    with ThreadPoolExecutor(workers) as ex:
        boards = dict(zip(todo, ex.map(lambda d: _safe(scoreboard, d), todo)))
    jobs = []
    for d, evs in boards.items():
        db = dbs[season_of(d)]
        if evs is None:
            continue
        all_done = True
        for e in evs:
            st = e["status"]["type"]
            if st.get("completed"):
                if e["id"] not in db["games"]:
                    jobs.append((d, e["id"]))
            elif st.get("state") != "post":
                all_done = False
        if all_done and d < datetime.now(ET).date():
            db["dates_done"].append(d.isoformat())
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda j: (j, _safe(parse_summary, j[1])), jobs))
    n = 0
    for (d, eid), g in res:
        if g:
            dbs[season_of(d)]["games"][eid] = g
            n += 1
        else:
            db = dbs[season_of(d)]
            if d.isoformat() in db["dates_done"]:
                db["dates_done"].remove(d.isoformat())   # retry that day next time
    for db in dbs.values():
        db["dates_done"] = sorted(set(db["dates_done"]))
        save(db)
    log(f"[nba] ingested {n} games across {len(todo)} days")
    return n


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        print(f"[nba] {fn.__name__}{a} failed: {e}")
        return None


def update(log=print):
    """Catch up from the last stored day through today (cheap: a few requests a day)."""
    today = datetime.now(ET).date()
    cur = season_of(today)
    db = load(cur)
    if db["dates_done"]:
        start = date.fromisoformat(db["dates_done"][-1]) + timedelta(1)
    else:
        start = date(cur - 1, 10, 1)
    start = max(start, today - timedelta(days=21))
    if start <= today:
        return ingest(start, today, log=log)
    return 0


def all_games(seasons):
    out = []
    for s in seasons:
        for gid, g in load(s)["games"].items():
            out.append((s, gid, g))
    out.sort(key=lambda x: x[2]["d"])
    return out


def injuries():
    """{athlete_id: status} for everyone on ESPN's injury report."""
    out = {}
    try:
        for t in get(f"{ESPN}/injuries").get("injuries", []):
            for i in t.get("injuries", []):
                href = (i["athlete"].get("links") or [{}])[0].get("href", "")
                aid = href.split("/id/")[1].split("/")[0] if "/id/" in href else None
                if aid:
                    out[aid] = i.get("status", "")
    except Exception as e:
        print(f"[nba] injuries failed: {e}")
    return out


def slate(d: date):
    """Tonight's games: [{espn_id, start, home, away, home_name, away_name, home_nick, away_nick}]"""
    out = []
    for e in scoreboard(d):
        c = e["competitions"][0]
        if e["status"]["type"].get("state") != "pre":
            continue
        t = {x["homeAway"]: x["team"] for x in c["competitors"]}
        out.append({"espn_id": e["id"], "start": e["date"], "home": t["home"]["abbreviation"],
                    "away": t["away"]["abbreviation"], "home_name": t["home"]["displayName"],
                    "away_name": t["away"]["displayName"], "home_nick": t["home"].get("name", ""),
                    "away_nick": t["away"].get("name", "")})
    return out


if __name__ == "__main__":
    import sys
    a, b = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
    ingest(a, b, workers=int(sys.argv[3]) if len(sys.argv) > 3 else 6)
