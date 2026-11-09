"""Referee crews from the NBA's own assignments feed (free, posted about 9am ET on game days; history available).
Each official's tendencies come from the games he worked: free throws called and points scored compared with
what the two teams normally produce. Individual effects are small and noisy, so they are shrunk hard."""
import json
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import config

API = "https://official.nba.com/wp-json/api/v1/get-game-officials?&date={}"
FILE = config.ROOT / "data" / "nba" / "refs.json"
NBA2ESPN = {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}
K_FTA, K_TOT = 45.0, 150.0        # games of prior: FT tendencies carry over year to year a little, totals barely


def _load():
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return {}


def _save(db):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(db, separators=(",", ":"), sort_keys=True))


def fetch_date(d):
    """[{h, a, o: [crew chief, referee, umpire]}] for an ET date (ISO string)."""
    req = urllib.request.Request(API.format(d), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = (((json.loads(r.read()).get("nba") or {}).get("Table") or {}).get("rows")) or []
    out = []
    for x in rows:
        o = [x.get(f"official{i}") for i in (1, 2, 3)]
        if not all(o):
            continue
        ab = lambda k: NBA2ESPN.get(x.get(k), x.get(k))
        out.append({"h": ab("home_team_abbr"), "a": ab("away_team_abbr"), "o": o})
    return out


def update(dates, today=None, log=print):
    """Fill the cache for any missing dates (past dates never change) and always re-read today."""
    db = _load()
    want = sorted({d for d in dates if d not in db} | ({today} if today else set()))
    if not want:
        return db

    def one(d):
        try:
            return d, fetch_date(d)
        except Exception as e:
            return d, e
    with ThreadPoolExecutor(max_workers=6) as ex:
        for d, res in ex.map(one, want):
            if isinstance(res, Exception):
                log(f"[refs] {d} failed: {res}")
            elif res or d != today:            # an empty today is "not posted yet": don't cache it
                db[d] = res
    _save(db)
    return db


def crew(db, day, home, away):
    """The three officials for a game on an ET date, or None if not posted."""
    for x in db.get(day if isinstance(day, str) else day.isoformat(), []):
        if x["h"] == home and x["a"] == away:
            return x["o"]
    return None


def _et_dates(utc_day):
    """Box scores are stamped with the UTC date; a late tip is the day before in ET."""
    d = date.fromisoformat(utc_day)
    return [(d - timedelta(days=1)).isoformat(), utc_day]


def tendencies(games, db=None):
    """{official: {n, fta, tot}}: shrunk extra free throws per game (both teams) and extra points per game.
    games: nba_data.all_games rows [(season, gid, g)]. Expectation per game: each team's season average for and
    the opponent's season average against, relative to the league."""
    db = db if db is not None else _load()
    by_season = defaultdict(list)
    for s, gid, g in games:
        if g.get("st", 2) != 2 or len(g["teams"]) != 2 or len(g.get("tstats") or {}) != 2:
            continue
        by_season[s].append(g)
    acc = defaultdict(lambda: [0, 0.0, 0.0])
    for s, gs in by_season.items():
        tm = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])                  # n, fta for, fta against, pts for, pts against
        for g in gs:
            ab = list(g["teams"])
            for t, o in ((ab[0], ab[1]), (ab[1], ab[0])):
                a = tm[t]; a[0] += 1
                a[1] += g["tstats"][t]["fta"]; a[2] += g["tstats"][o]["fta"]
                a[3] += g["teams"][t]["score"]; a[4] += g["teams"][o]["score"]
        if not tm:
            continue
        lg_f = sum(a[1] for a in tm.values()) / sum(a[0] for a in tm.values())
        lg_p = sum(a[3] for a in tm.values()) / sum(a[0] for a in tm.values())
        for g in gs:
            ab = list(g["teams"])
            home = next(t for t in ab if g["teams"][t]["home"]); away = next(t for t in ab if t != home)
            if tm[home][0] < 8 or tm[away][0] < 8:
                continue
            cr = None
            for d in _et_dates(g["d"]):
                cr = crew(db, d, home, away)
                if cr:
                    break
            if not cr:
                continue
            exp_f = exp_p = 0.0
            for t, o in ((home, away), (away, home)):
                exp_f += tm[t][1] / tm[t][0] + tm[o][2] / tm[o][0] - lg_f
                exp_p += tm[t][3] / tm[t][0] + tm[o][4] / tm[o][0] - lg_p
            rf = g["tstats"][home]["fta"] + g["tstats"][away]["fta"] - exp_f
            rp = g["teams"][home]["score"] + g["teams"][away]["score"] - exp_p
            for o in cr:
                a = acc[(o, s)]; a[0] += 1; a[1] += rf; a[2] += rp
    out = {}
    names = {o for (o, _) in acc}
    cur = max(by_season) if by_season else None
    for o in names:
        n = sf = sp = 0.0
        for (o2, s), a in acc.items():
            if o2 != o:
                continue
            w = 1.0 if s == cur else 0.6                                     # older seasons count less
            n += w * a[0]; sf += w * a[1]; sp += w * a[2]
        out[o] = {"n": round(n, 1), "fta": round(sf / (n + K_FTA), 2), "tot": round(sp / (n + K_TOT), 2)}
    return out, acc


def crew_effect(tend, names):
    """A crew's expected extra free throws and points: the average of its officials' shrunk effects."""
    xs = [tend[o] for o in names if o in tend]
    if not xs:
        return None
    return {"crew": names, "fta": round(sum(x["fta"] for x in xs) / len(xs), 2), "tot": round(sum(x["tot"] for x in xs) / len(xs), 2),
            "n": round(sum(x["n"] for x in xs) / len(xs))}
