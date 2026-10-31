"""Game stories for settled bets: what happened in the game and why the bet won or lost.

Built from ESPN's free play-by-play (no credits). For basketball it finds:
  - the score by quarter, largest leads, lead changes and ties,
  - scoring runs (unanswered points) and momentum swings (biggest reversals in the margin),
  - the closing stretch (scoring plays in the last 5 minutes when the game was within 6),
  - top performers on each side,
  - for a player prop: the player's line quarter by quarter (points, rebounds, assists, threes,
    fouls) so you can see when the bet was won or lost.
Other sports get the line score, scoring plays and leaders that ESPN publishes.
Every function returns plain data and never raises; the narrative is written from that data.
"""
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone

ESPN = "https://site.api.espn.com/apis/site/v2/sports/"
PATHS = {
    "basketball_nba": "basketball/nba", "basketball_wnba": "basketball/wnba",
    "basketball_ncaab": "basketball/mens-college-basketball", "americanfootball_nfl": "football/nfl",
    "americanfootball_ncaaf": "football/college-football", "icehockey_nhl": "hockey/nhl",
    "baseball_mlb": "baseball/mlb", "soccer_epl": "soccer/eng.1", "soccer_usa_mls": "soccer/usa.1",
    "soccer_uefa_champs_league": "soccer/uefa.champions", "soccer_spain_la_liga": "soccer/esp.1",
    "soccer_italy_serie_a": "soccer/ita.1", "soccer_germany_bundesliga": "soccer/ger.1",
    "soccer_france_ligue_one": "soccer/fra.1",
}
SB_EXTRA = {"basketball_ncaab": "&groups=50", "americanfootball_ncaaf": "&groups=80"}
ET = timezone(timedelta(hours=-4))
PER_NAME = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Perseverance/1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _nk(s):
    s = str(s or "").lower().replace("la clippers", "los angeles clippers")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s)).strip()


def _same(odds_name, team):
    o = _nk(odds_name)
    full, nick, loc = _nk(team.get("displayName")), _nk(team.get("name")), _nk(team.get("location"))
    return bool(o) and (o == full or (nick and o.endswith(nick) and (not loc or o.startswith(loc.split()[0]))))


def find_event(sport, home, away, commence):
    """ESPN event id for a game, matched by team names on the game's ET date (and the day before,
    for late games stored in UTC)."""
    path = PATHS.get(sport)
    if not path:
        return None
    start = datetime.fromisoformat(commence.replace("Z", "+00:00")).astimezone(ET).date()
    for d in (start, start - timedelta(days=1)):
        try:
            evs = _get(f"{ESPN}{path}/scoreboard?dates={d:%Y%m%d}&limit=400{SB_EXTRA.get(sport, '')}").get("events", [])
        except Exception:
            continue
        for e in evs:
            cs = e["competitions"][0]["competitors"]
            h = next((c["team"] for c in cs if c["homeAway"] == "home"), {})
            a = next((c["team"] for c in cs if c["homeAway"] == "away"), {})
            if _same(home, h) and _same(away, a):
                return e["id"]
    return None


def _clock_sec(disp):
    m = re.match(r"^(\d+):(\d+)", disp or "")
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        return float(disp)
    except (TypeError, ValueError):
        return 0.0


def _when(per, clock, n_reg=4):
    if per > n_reg:
        name = "OT" if per == n_reg + 1 else f"{per - n_reg}OT"
    else:
        name = PER_NAME.get(per, f"P{per}") + (" Q" if n_reg == 4 else " half")
    return f"{clock} {name}".strip()


# ---------------------------------------------------------------- basketball
def _basketball(d, focus=None):
    comp = d["header"]["competitions"][0]
    teams = {}
    for c in comp["competitors"]:
        t = c["team"]
        teams[t["id"]] = {"abbr": t["abbreviation"], "name": t["displayName"], "home": c["homeAway"] == "home",
                          "score": int(float(c.get("score") or 0)),
                          "ls": [int(float(x.get("displayValue") or 0)) for x in (c.get("linescores") or [])]}
    hid = next(k for k, v in teams.items() if v["home"])
    aid = next(k for k, v in teams.items() if not v["home"])
    H, A = teams[hid], teams[aid]
    n_reg = 4 if "nba" in (d.get("header", {}).get("league", {}).get("slug", "nba")) or len(H["ls"]) != 2 else 2
    plays = d.get("plays") or []

    # margin series (home - away) at every scoring play
    series, prev_h, prev_a = [], 0, 0
    for p in plays:
        hs, as_ = p.get("homeScore"), p.get("awayScore")
        if hs is None or as_ is None:
            continue
        if (hs, as_) != (prev_h, prev_a):
            per = (p.get("period") or {}).get("number", 0)
            clk = (p.get("clock") or {}).get("displayValue", "")
            dh, da = hs - prev_h, as_ - prev_a
            series.append({"h": hs, "a": as_, "per": per, "clk": clk, "txt": p.get("text", ""),
                           "team": hid if dh > 0 and da <= 0 else aid if da > 0 and dh <= 0 else None,
                           "pts": dh + da, "dh": dh, "da": da})
            prev_h, prev_a = hs, as_

    lead_changes = ties = 0
    big = {hid: (0, None), aid: (0, None)}
    last_sign = 0
    for s in series:
        m = s["h"] - s["a"]
        sign = (m > 0) - (m < 0)
        if sign == 0 and last_sign != 0:
            ties += 1
        if sign != 0 and last_sign != 0 and sign != last_sign:
            lead_changes += 1
        if sign != 0:
            last_sign = sign
        if m > big[hid][0]:
            big[hid] = (m, _when(s["per"], s["clk"], n_reg))
        if -m > big[aid][0]:
            big[aid] = (-m, _when(s["per"], s["clk"], n_reg))

    # unanswered runs
    runs, cur = [], None
    for s_ in series:
        side = s_["team"]
        if side is None:
            continue
        here = _when(s_["per"], s_["clk"], n_reg)
        if cur and cur["team"] == side:
            cur["pts"] += s_["pts"]
            cur["end"] = here
            cur["score_end"] = (s_["a"], s_["h"])
        else:
            if cur and cur["pts"] >= 8:
                runs.append(cur)
            cur = {"team": side, "pts": s_["pts"], "start": here, "end": here, "score_end": (s_["a"], s_["h"])}
    if cur and cur["pts"] >= 8:
        runs.append(cur)
    runs.sort(key=lambda r: -r["pts"])
    runs_out = [{"team": teams[r["team"]]["abbr"] if r["team"] in teams else "?", "pts": r["pts"], "from": r["start"],
                 "to": r["end"], "score": f"{A['abbr']} {r['score_end'][0]}-{r['score_end'][1]} {H['abbr']}"}
                for r in runs[:4] if r["team"] in teams]

    # momentum swings: the biggest change in the margin inside any 8-minute stretch, each direction
    plen = 720 if n_reg == 4 else 1200
    def el(x):
        per = x["per"]
        if per <= n_reg:
            return (per - 1) * plen + (plen - _clock_sec(x["clk"]))
        return n_reg * plen + (per - n_reg - 1) * 300 + (300 - _clock_sec(x["clk"]))
    swings = []
    ts = [el(x) for x in series]
    ms = [x["h"] - x["a"] for x in series]
    for direction, who in ((1, H), (-1, A)):
        best = (0, None, None)
        for i in range(len(series)):
            for j in range(i + 1, len(series)):
                if ts[j] - ts[i] > 480:
                    break
                g = direction * (ms[j] - ms[i])
                if g > best[0]:
                    best = (g, i, j)
        if best[0] >= 10:
            g, i0, i1 = best
            s0, s1 = series[i0], series[i1]
            pf = (s1["h"] - s0["h"]) if direction == 1 else (s1["a"] - s0["a"])
            pa = (s1["a"] - s0["a"]) if direction == 1 else (s1["h"] - s0["h"])
            swings.append({"team": who["abbr"], "swing": g, "from": _when(s0["per"], s0["clk"], n_reg),
                           "to": _when(s1["per"], s1["clk"], n_reg), "mins": round((ts[i1] - ts[i0]) / 60, 1),
                           "from_score": f"{A['abbr']} {s0['a']}-{s0['h']} {H['abbr']}",
                           "to_score": f"{A['abbr']} {s1['a']}-{s1['h']} {H['abbr']}", "pts_for": pf, "pts_against": pa})
    swings.sort(key=lambda x: -x["swing"])

    # closing stretch: scoring plays in the last 5:00 of regulation/OT while within 6
    clutch = []
    for s in series:
        before = (s["h"] - s["dh"]) - (s["a"] - s["da"])
        if s["per"] >= n_reg and _clock_sec(s["clk"]) <= 300 and abs(before) <= 6:
            clutch.append(f"{_when(s['per'], s['clk'], n_reg)}: {s['txt']} ({A['abbr']} {s['a']}-{s['h']} {H['abbr']})")
    clutch = clutch[-8:]

    # top performers from the box score
    leaders = []
    box = {}
    for t in d.get("boxscore", {}).get("players", []):
        ab = t["team"]["abbreviation"]
        for grp in t.get("statistics", []):
            L = {l: i for i, l in enumerate(grp.get("labels") or [])}
            for a in grp.get("athletes", []):
                st = a.get("stats") or []
                if not st or "PTS" not in L:
                    continue
                g = lambda k: st[L[k]] if k in L and L[k] < len(st) else "0"
                row = {"id": a["athlete"]["id"], "name": a["athlete"]["displayName"], "team": ab,
                       "min": g("MIN"), "pts": int(float(g("PTS") or 0)), "reb": int(float(g("REB") or 0)),
                       "ast": int(float(g("AST") or 0)), "3pt": g("3PT"), "fg": g("FG"), "pf": g("PF"),
                       "starter": bool(a.get("starter"))}
                box[row["id"]] = row
    for ab in (A["abbr"], H["abbr"]):
        rows = sorted((r for r in box.values() if r["team"] == ab), key=lambda r: -(r["pts"] + 0.7 * r["reb"] + 0.7 * r["ast"]))
        for r in rows[:2]:
            leaders.append(f"{r['name']} ({ab}): {r['pts']} pts, {r['reb']} reb, {r['ast']} ast, {r['3pt']} 3PT in {r['min']} min")

    out = {"kind": "basketball", "teams": {"home": H["abbr"], "away": A["abbr"]},
           "final": f"{A['abbr']} {A['score']} – {H['abbr']} {H['score']}" + (" (OT)" if len(H["ls"]) > n_reg else ""),
           "periods": {"away": A["ls"], "home": H["ls"]},
           "largest_lead": {A["abbr"]: big[aid][0], H["abbr"]: big[hid][0]},
           "largest_lead_at": {A["abbr"]: big[aid][1], H["abbr"]: big[hid][1]},
           "lead_changes": lead_changes, "ties": ties, "runs": runs_out, "swings": swings[:2],
           "clutch": clutch, "leaders": leaders}

    if focus and focus.get("player_id"):
        out["player"] = _player_flow(plays, focus["player_id"], n_reg, box.get(focus["player_id"]))
    out["story"] = _bb_story(out, focus)
    return out


def _player_flow(plays, pid, n_reg, box_row):
    per = {}
    first_three = []
    for p in plays:
        parts = [((x.get("athlete") or {}).get("id")) for x in (p.get("participants") or [])]
        if pid not in parts:
            continue
        n = (p.get("period") or {}).get("number", 0)
        q = per.setdefault(n, {"pts": 0, "reb": 0, "ast": 0, "tpm": 0, "pf": 0, "fga": 0, "fgm": 0})
        ty = ((p.get("type") or {}).get("text") or "").lower()
        txt = (p.get("text") or "").lower()
        actor = parts[0] == pid
        if p.get("shootingPlay") and actor:
            if "free throw" not in ty:
                q["fga"] += 1
                if p.get("scoringPlay"):
                    q["fgm"] += 1
            if p.get("scoringPlay"):
                q["pts"] += p.get("scoreValue") or 0
                if p.get("pointsAttempted") == 3:
                    q["tpm"] += 1
                    first_three.append(_when(n, (p.get("clock") or {}).get("displayValue", ""), n_reg))
        elif p.get("scoringPlay") and not actor and len(parts) > 1 and parts[1] == pid and "assist" in txt:
            q["ast"] += 1
        elif "rebound" in ty and actor and "team" not in ty:
            q["reb"] += 1
        elif "foul" in ty and actor and "technical" not in ty and "offensive charge" not in ty:
            q["pf"] += 1
        elif "foul" in ty and actor:
            q["pf"] += 1
    rows = [{"per": k, "label": ("OT" if k > n_reg else f"Q{k}") if n_reg == 4 else (f"H{k}" if k <= n_reg else "OT"), **v}
            for k, v in sorted(per.items())]
    return {"by_period": rows, "threes_at": first_three, "box": box_row}


def _bb_story(r, focus):
    A, H = r["teams"]["away"], r["teams"]["home"]
    s = []
    pa, ph = r["periods"]["away"], r["periods"]["home"]
    if pa and ph:
        half_a, half_h = sum(pa[:2]), sum(ph[:2])
        lead = A if half_a > half_h else H if half_h > half_a else None
        s.append(f"Halftime: {A} {half_a}-{half_h} {H}" + (f", {lead} up {abs(half_a - half_h)}." if lead else ", tied."))
        if len(pa) >= 4:
            q = [(i + 1, pa[i], ph[i]) for i in range(min(len(pa), len(ph)))]
            best = max(q, key=lambda x: abs(x[1] - x[2]))
            w = A if best[1] > best[2] else H
            s.append(f"The {'OT' if best[0] > 4 else PER_NAME.get(best[0], str(best[0])) + ' quarter'} decided the most: "
                     f"{w} won it {max(best[1], best[2])}-{min(best[1], best[2])}.")
    ll = r["largest_lead"]
    s.append(f"Largest leads: {A} {ll[A]}" + (f" ({r['largest_lead_at'][A]})" if r['largest_lead_at'][A] else "")
             + f", {H} {ll[H]}" + (f" ({r['largest_lead_at'][H]})" if r['largest_lead_at'][H] else "")
             + f". {r['lead_changes']} lead changes, {r['ties']} ties.")
    for sw in r["swings"]:
        s.append(f"Momentum swing: {sw['team']} outscored them {sw['pts_for']}-{sw['pts_against']} in {sw['mins']:g} minutes "
                 f"({sw['from']} to {sw['to']}), moving it from {sw['from_score']} to {sw['to_score']}.")
    for rn in r["runs"][:2]:
        s.append(f"{rn['team']} went on a {rn['pts']}-0 run ({rn['from']} to {rn['to']}), making it {rn['score']}.")
    if r["clutch"]:
        s.append(f"Close finish: {len(r['clutch'])} scoring plays in the last five minutes with the game within six.")
    elif max(ll.values() or [0]) >= 20:
        s.append("No real closing drama: the game was decided before the last five minutes.")
    pf = r.get("player")
    if pf and pf.get("by_period"):
        stat = (focus or {}).get("stat")
        key = {"ra": ("reb", "ast"), "pra": ("pts", "reb", "ast"), "tpm": ("tpm",), "ast": ("ast",),
               "reb": ("reb",), "pts": ("pts",)}.get(stat, ("pts", "reb", "ast"))
        name = (focus or {}).get("player", "He")
        line = (focus or {}).get("point")
        run_tot, crossed = 0, None
        parts = []
        for row in pf["by_period"]:
            v = sum(row[k] for k in key)
            run_tot += v
            parts.append(f"{row['label']} {v}")
            if line is not None and crossed is None and run_tot > line:
                crossed = row["label"]
        s.append(f"{name} by period ({'+'.join(k.upper() for k in key)}): " + ", ".join(parts) + ".")
        if line is not None:
            if crossed:
                s.append(f"He went over {line:g} in {crossed}.")
            else:
                s.append(f"He never got past {line:g}.")
        fouls = [(row["label"], row["pf"]) for row in pf["by_period"] if row["pf"]]
        tot = 0
        for lbl, n in fouls:
            tot += n
            if (lbl in ("Q1", "Q2") and tot >= 3) or (lbl == "Q3" and tot >= 4):
                s.append(f"Foul trouble: {tot} fouls by the end of {lbl}, which cuts minutes.")
                break
        if pf.get("threes_at"):
            s.append("Threes made at: " + ", ".join(pf["threes_at"]) + ".")
    return s


# ---------------------------------------------------------------- other sports
def _generic(d):
    comp = d["header"]["competitions"][0]
    cs = {c["homeAway"]: c for c in comp["competitors"]}
    H, A = cs["home"], cs["away"]
    ab = lambda c: c["team"].get("abbreviation") or c["team"].get("displayName")
    ls = lambda c: [x.get("displayValue") for x in (c.get("linescores") or [])]
    sp = []
    scoring = d.get("scoringPlays") or [p for p in (d.get("plays") or []) if p.get("scoringPlay")]
    d["scoringPlays"] = scoring
    for p in scoring[-14:]:
        per = (p.get("period") or {}).get("number", "")
        clk = (p.get("clock") or {}).get("displayValue", "")
        sp.append(f"P{per} {clk}: {p.get('text', '')} ({ab(A)} {p.get('awayScore', '')}-{p.get('homeScore', '')} {ab(H)})")
    leaders = []
    for t in d.get("leaders") or []:
        tab = (t.get("team") or {}).get("abbreviation", "")
        for cat in (t.get("leaders") or [])[:3]:
            top = (cat.get("leaders") or [{}])[0]
            nm = (top.get("athlete") or {}).get("displayName")
            if nm:
                leaders.append(f"{tab} {cat.get('displayName', '')}: {nm} {top.get('displayValue', '')}")
    story = []
    la, lh = ls(A), ls(H)
    if la and lh:
        story.append("By period: " + ", ".join(f"P{i + 1} {a}-{h}" for i, (a, h) in enumerate(zip(la, lh))) + f" ({ab(A)}-{ab(H)}).")
    if scoring:
        lead, changes, decisive = 0, 0, None
        for p in scoring:
            m = (p.get("homeScore") or 0) - (p.get("awayScore") or 0)
            sign = (m > 0) - (m < 0)
            if sign and sign != lead:
                if lead:
                    changes += 1
                decisive = p
                lead = sign
        story.append(f"{len(scoring)} scores, {changes} lead changes.")
        if decisive:
            per = (decisive.get("period") or {}).get("number", "")
            story.append(f"Go-ahead score that held: P{per} {(decisive.get('clock') or {}).get('displayValue', '')} — {decisive.get('text', '')}")
    return {"kind": "generic", "final": f"{ab(A)} {A.get('score')} – {ab(H)} {H.get('score')}",
            "periods": {"away": la, "home": lh}, "scoring": sp, "leaders": leaders[:6], "story": story}


def build(sport, event_id, focus=None):
    path = PATHS.get(sport)
    if not path or not event_id:
        return None
    try:
        d = _get(f"{ESPN}{path}/summary?event={event_id}")
        if not d["header"]["competitions"][0]["status"]["type"].get("completed"):
            return None
        r = _basketball(d, focus) if sport.startswith("basketball") else _generic(d)
        r["espn_id"] = event_id
        r["link"] = f"https://www.espn.com/{path.split('/')[0].replace('football', 'nfl' if 'nfl' in path else 'college-football').replace('basketball', 'nba' if 'nba' in path else 'mens-college-basketball').replace('hockey', 'nhl').replace('baseball', 'mlb')}/game/_/gameId/{event_id}" if not path.startswith("soccer") else f"https://www.espn.com/soccer/match/_/gameId/{event_id}"
        return r
    except Exception as e:
        return {"error": str(e)[:120]}


def attach(bet):
    """Add a recap to each leg of a settled bet. Returns True if anything was added."""
    added = False
    rv = bet.get("review") or {}
    if rv.get("recaps"):
        return False
    recaps = []
    for l in bet["legs"]:
        sport = l.get("sport")
        if not sport or not l.get("home"):
            recaps.append(None)
            continue
        eid = l.get("espn_id") or find_event(sport, l["home"], l["away"], l["commence"])
        focus = {"player_id": l.get("player_id"), "player": l.get("player"), "stat": l.get("stat"),
                 "point": l.get("point")} if l.get("player_id") else None
        rc = build(sport, eid, focus) if eid else None
        recaps.append(rc)
        added = added or bool(rc and not rc.get("error"))
    if added:
        rv["recaps"] = recaps
        bet["review"] = rv
    return added


if __name__ == "__main__":
    import sys
    print(json.dumps(build(sys.argv[1], sys.argv[2], {"player_id": sys.argv[3], "player": "P", "stat": "pra", "point": 20.5}
                           if len(sys.argv) > 3 else None), indent=1))
