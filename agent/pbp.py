"""Play-by-play data from pbpstats.com (free): true usage, each player's share of the rebounds available while he
is on the floor, his assist mix, and above all what teammates do in the possessions a player is OFF the floor.
That last one is what whole-game with/without splits can't see: every minute a star sat, all season, counts.
NBA.com's own tracking feed (potential assists, rebound chances) does not answer requests from GitHub's servers,
so this is the closest free substitute. Everything is cached for the day and optional: if the site is down the
model runs without it."""
import json
import time
import urllib.parse
import urllib.request

from . import config, store

API = "https://api.pbpstats.com"
DIR = config.ROOT / "data" / "nba" / "pbp"
NBA2ESPN = {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}
K_POSS = 250.0                    # possessions of prior on every on/off ratio
KEEP = ("Minutes", "OffPoss", "DefPoss", "Usage", "Points", "Assists", "Rebounds", "FG3M", "FG3A", "FG2A", "FTA", "Turnovers",
        "DefRebounds", "OffRebounds", "DefFGReboundPct", "OffFGReboundPct", "AtRimAssists", "ThreePtAssists", "AssistPoints",
        "ShotQualityAvg", "OnOffRtg", "GamesPlayed")
_last = [0.0]


def season_str(season):
    return f"{season - 1}-{str(season)[2:]}"


def _get(path, params, tries=2):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    for i in range(tries):
        wait = 1.2 - (time.time() - _last[0])            # be polite: about one request a second
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Perseverance research; personal use)"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except Exception as e:
            err = e
    raise err


def _cache(name):
    try:
        return json.loads((DIR / name).read_text())
    except Exception:
        return None


def _put(name, obj):
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / name).write_text(json.dumps(obj, separators=(",", ":")))


def _slim(r):
    out = {k: (round(r[k], 4) if isinstance(r.get(k), float) else r.get(k)) for k in KEEP if r.get(k) is not None}
    out["name"] = r.get("Name"); out["id"] = str(r.get("EntityId"))
    return out


def players(season, today, log=print):
    """{nba_id: slim season totals} for every player, plus team ids. One request a day per season."""
    name = f"players-{season}.json"
    c = _cache(name)
    cur = season >= config_season(today)
    if c and (c.get("day") == today or not cur):
        return c
    try:
        rows = _get("get-totals/nba", {"Season": season_str(season), "SeasonType": "Regular Season", "Type": "Player"})["multi_row_table_data"]
        teams = _get("get-totals/nba", {"Season": season_str(season), "SeasonType": "Regular Season", "Type": "Team"})["multi_row_table_data"]
    except Exception as e:
        log(f"[pbp] totals {season} failed: {e}")
        return c
    out = {"day": today, "season": season, "p": {}, "teams": {}}
    for r in rows:
        s = _slim(r)
        s["team"] = NBA2ESPN.get(r.get("TeamAbbreviation"), r.get("TeamAbbreviation"))
        out["p"][s["id"]] = s
    for t in teams:
        ab = NBA2ESPN.get(t.get("TeamAbbreviation"), t.get("TeamAbbreviation"))
        out["teams"][ab] = str(t.get("TeamId") or t.get("EntityId"))
    _put(name, out)
    return out


def config_season(today):
    y, m = int(today[:4]), int(today[5:7])
    return y + 1 if m >= 10 else y


def _key(name):
    import re, unicodedata
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[.'’\-]", "", t)
    t = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", t)
    return re.sub(r"\s+", " ", t).strip()


def id_map(P, model_players, norm=None):
    """{espn_pid: nba_id} by name (accents and suffixes ignored; team breaks ties when a name repeats)."""
    by = {}
    for nid, p in (P or {}).get("p", {}).items():
        by.setdefault(_key(p["name"]), []).append((nid, p.get("team")))
    out = {}
    for pid, pl in model_players.items():
        c = by.get(_key(pl["name"]))
        if not c:
            continue
        hit = next((nid for nid, t in c if t == pl["team"]), None) or (c[0][0] if len(c) == 1 else None)
        if hit:
            out[pid] = hit
    return out


def wowy_off(team_id, off_id, season, today, log=print, to_date=None):
    """Teammates' totals in the possessions `off_id` was NOT on the floor (this team, one season). Cached for the day
    (finished seasons and fixed date ranges: for good)."""
    name = f"off-{season}-{team_id}-{off_id}{'-to' + to_date if to_date else ''}.json"
    c = _cache(name)
    if c and (c.get("day") == today or to_date or season < config_season(today)):
        return c["p"]
    try:
        q = {"TeamId": team_id, "Season": season_str(season), "SeasonType": "Regular Season", "Type": "Player", "0Exactly0OnFloor": off_id}
        if to_date:
            q["ToDate"] = to_date
        rows = _get("get-wowy-stats/nba", q)["multi_row_table_data"]
    except Exception as e:
        log(f"[pbp] on/off {off_id} {season} failed: {e}")
        return c["p"] if c else None
    out = {str(r.get("EntityId")): _slim(r) for r in rows}
    _put(name, {"day": today, "p": out})
    return out


def team_totals(team_id, season, today, log=print, to_date=None):
    """Every player's totals for this team only (a traded player's league row mixes two teams)."""
    name = f"team-{season}-{team_id}{'-to' + to_date if to_date else ''}.json"
    c = _cache(name)
    if c and (c.get("day") == today or to_date or season < config_season(today)):
        return c["p"]
    try:
        q = {"TeamId": team_id, "Season": season_str(season), "SeasonType": "Regular Season", "Type": "Player"}
        if to_date:
            q["ToDate"] = to_date
        rows = _get("get-wowy-stats/nba", q)["multi_row_table_data"]
    except Exception as e:
        log(f"[pbp] team totals {team_id} {season} failed: {e}")
        return c["p"] if c else None
    out = {str(r.get("EntityId")): _slim(r) for r in rows}
    _put(name, {"day": today, "p": out})
    return out


SUMK = ("OffPoss", "Points", "Assists", "Rebounds", "FG3M")


def combine(parts):
    """Add up seasons: parts = [(weight, all_rows, off_rows)]. Usage is averaged by possessions."""
    A, O = {}, {}
    for w, all_rows, off_rows in parts:
        for src, dst in ((all_rows, A), (off_rows, O)):
            for nid, r in (src or {}).items():
                d = dst.setdefault(nid, {k: 0.0 for k in SUMK} | {"_u": 0.0, "name": r.get("name")})
                for k in SUMK:
                    d[k] += w * (r.get(k) or 0)
                d["_u"] += w * (r.get("Usage") or 0) * (r.get("OffPoss") or 0)
    for dst in (A, O):
        for d in dst.values():
            d["Usage"] = d["_u"] / d["OffPoss"] if d["OffPoss"] else None
    return A, O


RATE = {"pts": "Points", "ast": "Assists", "reb": "Rebounds", "tpm": "FG3M"}


def off_effect(all_rows, off_rows, mate_id):
    """For one teammate: per-possession rates with the absent player OFF vs ON the floor.
    Returns {stat: ratio (shrunk)}, usage on/off, and the possessions behind it; None when there is too little."""
    a, o = all_rows.get(mate_id), off_rows.get(mate_id)
    if not a or not o or not a.get("OffPoss") or not o.get("OffPoss"):
        return None
    p_off, p_all = o["OffPoss"], a["OffPoss"]
    p_on = p_all - p_off
    if p_off < 60 or p_on < 150:
        return None
    w = p_off / (p_off + K_POSS)
    out = {"poss": int(p_off), "share_on": round(p_on / p_all, 3), "r": {}}
    for s, k in RATE.items():
        on = ((a.get(k) or 0) - (o.get(k) or 0)) / p_on
        off = (o.get(k) or 0) / p_off
        if on <= 0:
            continue
        out["r"][s] = round(max(0.6, min(1.7, 1 + w * (off / on - 1))), 3)
    # usage: the totals row gives the season figure; the off row gives usage in the off possessions
    if a.get("Usage") and o.get("Usage"):
        u_all, u_off = a["Usage"], o["Usage"]
        u_on = (u_all * p_all - u_off * p_off) / p_on if p_on else u_all
        out["usage"] = [round(u_on, 1), round(u_off, 1)]
    return out


def tonight_mult(r, p_on):
    """Baseline games mix on and off possessions (p_on of them with the star on). Tonight is all off."""
    return r / (p_on + (1 - p_on) * r)


def profile(p):
    """Opportunity profile for a player card (the free stand-in for tracking data)."""
    if not p or not p.get("Minutes"):
        return None
    ast = p.get("Assists") or 0
    out = {"usage": p.get("Usage"), "min": p.get("Minutes"), "dreb_share": p.get("DefFGReboundPct"), "oreb_share": p.get("OffFGReboundPct"),
           "shot_q": p.get("ShotQualityAvg"), "onoff": p.get("OnOffRtg")}
    if p.get("DefFGReboundPct") and p.get("DefRebounds"):
        out["dreb_chances36"] = round(p["DefRebounds"] / p["DefFGReboundPct"] / p["Minutes"] * 36, 1)
    if ast >= 10:
        out["ast_three"] = round((p.get("ThreePtAssists") or 0) / ast, 3)
        out["ast_rim"] = round((p.get("AtRimAssists") or 0) / ast, 3)
        out["ast_pts"] = round((p.get("AssistPoints") or 0) / ast, 2)
    return {k: v for k, v in out.items() if v is not None}
