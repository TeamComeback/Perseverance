"""Daily NBA research: everything the dashboard's Props Lab, player cards and Trends tab read.

Free (ESPN + RotoWire, no Odds API credits). Runs every morning and again before the evening props
run, so the dashboard never shows a stale slate. Writes to docs/data/:
  players.json  every rotation player on the slate: projections for every stat, matchup signals,
                game logs over three seasons (for L5/L10/L15, H2H and home/away filters), which key
                teammates played each game (with/without filter), similar players vs the opponent,
                defense-vs-position ranks, RotoWire lineup status and profile link
  shots.json    this season's shot locations for those players (shot charts)
  dvp.json      defense vs position for every team, role and stat
  trends.json   league trends: hot/cold players, minutes risers, pace, scoring, ATS and O/U records,
                softest/toughest matchups, back-to-backs, halftime comebacks
  lineups.json  RotoWire's projected/confirmed lineups as read
"""
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from . import config, store
from . import nba_data as nd
from . import nba_model as nm
from . import rotowire

ALL_STATS = ("ra", "ast", "tpm", "pra", "pts", "reb")
MIN_MINUTES = 10
OUT_WORDS = ("out", "ofs", "susp", "doubtful", "inactive")


def norm(name):
    s = re.sub(r"[.'’\-]", "", (name or "").lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def et_today():
    return store.et_now().date()


def slate_all(d):
    """Every game on an ET date with its state: pre, in or post."""
    out = []
    for e in nd.scoreboard(d):
        c = e["competitions"][0]
        t = {x["homeAway"]: x for x in c["competitors"]}
        h, a = t["home"]["team"], t["away"]["team"]
        out.append({"espn_id": e["id"], "start": e["date"], "state": e["status"]["type"].get("state", "pre"),
                    "home": h["abbreviation"], "away": a["abbreviation"], "home_name": h["displayName"],
                    "away_name": a["displayName"], "home_nick": h.get("name", ""), "away_nick": a.get("name", ""),
                    "home_score": t["home"].get("score"), "away_score": t["away"].get("score")})
    return out


def pick_date():
    """Today's slate while any game is still to tip; otherwise tomorrow's."""
    d = et_today()
    games = slate_all(d)
    if games and any(g["state"] == "pre" for g in games):
        return d, games
    nxt = d + timedelta(days=1)
    return nxt, slate_all(nxt)


def _status_out(s):
    s = (s or "").lower()
    return any(w in s for w in OUT_WORDS)


# ------------------------------------------------------------------ context
def context(log=print, refresh=True):
    """Build the model and project every rotation player on the slate (free)."""
    if refresh and not store.os.environ.get("PERSEVERANCE_NOW"):
        try:
            nd.update(log=lambda m: None)
        except Exception as e:
            log(f"[research] box-score refresh failed: {e}")
    day, games = pick_date()
    cur = nd.season_of(day)
    model = nm.Model(cur)
    inj = nd.injuries()
    try:
        rw_games = rotowire.fetch()
        rw = rotowire.by_player(rw_games, norm)
    except Exception as e:
        log(f"[research] RotoWire lineups unavailable: {e}")
        rw_games, rw = [], {}
    W = {k: config.DEFAULT_PARAMS[k] for k in ("w_dvp", "w_sim", "w_h2h", "matchup_strength")}
    st = store.load("state.json", None)
    if st:
        W.update({k: st["params"].get(k, W[k]) for k in W})

    roster = defaultdict(list)
    for pid, pl in model.players.items():
        roster[pl["team"]].append(pid)
    entries = []
    for g in games:
        for team, opp, home, nick in ((g["home"], g["away"], True, g["home_nick"]), (g["away"], g["home"], False, g["away_nick"])):
            for pid in roster.get(team, []):
                pl = model.players[pid]
                if pl["games"] < 3:
                    continue
                mins = model.minutes(pid)
                r = rw.get(norm(pl["name"]))
                if r and r.get("nick") and r["nick"].lower() != nick.lower():
                    r = None
                if mins < MIN_MINUTES and not (r and r.get("starter")):
                    continue
                last = model.by_player[pid][-1]["d"]
                if (day - datetime.fromisoformat(last).date()).days > 30:
                    continue
                status = inj.get(pid, "")
                if r and r.get("inj") and not status:
                    status = r["inj"]
                proj = {}
                for s in ALL_STATS:
                    pr = model.project(pid, s, opp, home, W)
                    proj[s] = pr
                entries.append({"pid": pid, "name": pl["name"], "team": team, "opp": opp, "home": home, "game": g["espn_id"],
                                "start": g["start"], "state": g["state"], "role": pl["role"], "minutes": round(mins, 1),
                                "status": status, "out": _status_out(status), "rw": r, "proj": proj})
    return {"day": day, "games": games, "model": model, "inj": inj, "rw_games": rw_games, "rw": rw,
            "entries": entries, "weights": W, "season": cur}


# ------------------------------------------------------------------ team model (display only)
def team_projection(model, home, away):
    T = model.teams
    if home not in T or away not in T:
        return None
    lg_o = sum(t["ortg"] for t in T.values()) / len(T)
    pace = (T[home]["pace"] + T[away]["pace"]) / 2
    hp = pace * (T[home]["ortg"] + T[away]["drtg"] - lg_o) / 100 + 1.4
    ap = pace * (T[away]["ortg"] + T[home]["drtg"] - lg_o) / 100 - 1.4
    return {"home": round(hp, 1), "away": round(ap, 1), "total": round(hp + ap, 1), "spread_home": round(ap - hp, 1),
            "pace": round(pace, 1)}


# ------------------------------------------------------------------ players.json
def _games_index(seasons):
    games = nd.all_games(seasons)
    played = {}
    for s, gid, g in games:
        played[gid] = {p[nd.P_ID] for p in g["players"] if p[nd.P_MIN] > 0}
    return games, played


def build_players(ctx):
    model, entries = ctx["model"], ctx["entries"]
    seasons = [ctx["season"] - 2, ctx["season"] - 1, ctx["season"]]
    games, played = _games_index(seasons)
    want = {e["pid"] for e in entries}
    logs = defaultdict(list)
    team_min = defaultdict(lambda: defaultdict(float))       # cur season minutes per team per player
    for s, gid, g in games:
        if g.get("st", 2) not in (2, 3):
            continue
        ab = list(g["teams"])
        if len(ab) != 2:
            continue
        for p in g["players"]:
            if p[nd.P_MIN] <= 0:
                continue
            t = p[nd.P_TEAM]
            o = ab[1] if t == ab[0] else ab[0]
            if s == ctx["season"]:
                team_min[t][p[nd.P_ID]] += p[nd.P_MIN]
            if p[nd.P_ID] in want:
                logs[p[nd.P_ID]].append({"gid": gid, "d": g["d"], "opp": o, "home": 1 if g["teams"][t]["home"] else 0,
                                         "min": round(p[nd.P_MIN]), "pts": p[nd.P_PTS], "reb": p[nd.P_REB],
                                         "ast": p[nd.P_AST], "tpm": p[nd.P_TPM], "tpa": p[nd.P_TPA], "fga": p[nd.P_FGA],
                                         "start": p[nd.P_START], "s": s, "ts": g["teams"][t]["score"],
                                         "os": g["teams"][o]["score"], "team": t})
    out = {}
    for e in entries:
        pid, team, opp = e["pid"], e["team"], e["opp"]
        mates = sorted(((m, pid2) for pid2, m in team_min[team].items() if pid2 != pid), reverse=True)[:5]
        if len(mates) < 3:                     # early season: fall back to the model's roster
            extra = sorted((p2 for p2, pl in model.players.items() if pl["team"] == team and p2 != pid),
                           key=lambda p2: -model.minutes(p2))
            for p2 in extra:
                if all(p2 != m[1] for m in mates) and len(mates) < 5:
                    mates.append((0, p2))
        mate_ids = [m[1] for m in mates]
        rows = sorted(logs.get(pid, []), key=lambda r: r["d"])
        keep = rows[-82:] + [r for r in rows[:-82] if r["opp"] == opp]
        keep.sort(key=lambda r: r["d"])
        L = []
        for r in keep:
            mask = 0
            pl = played.get(r["gid"], set())
            for i, m in enumerate(mate_ids):
                if m in pl:
                    mask |= 1 << i
            L.append([r["d"], r["opp"], r["home"], r["min"], r["pts"], r["reb"], r["ast"], r["tpm"], r["tpa"],
                      r["fga"], r["start"], mask, r["s"], r["ts"], r["os"], r["team"]])
        proj = {}
        for s, pr in e["proj"].items():
            proj[s] = {"proj": pr["proj"], "base": pr["base"], "mult": pr["mult"],
                       "sd": round(math.sqrt(max(pr.get("var") or 0, 0)), 2),
                       "signals": [{"k": sg["k"], "v": sg.get("v"), "txt": sg["txt"], **({"rank": sg["rank"]} if "rank" in sg else {})}
                                   for sg in pr["signals"]]}
        dvp = {}
        for s in ALL_STATS:
            f, n = model.dvp_tab.get((opp, e["role"], s), (1.0, 0))
            dvp[s] = [round(f, 3), model.dvp_rank.get((opp, e["role"], s), 0), round(n, 1)]
        out[pid] = {"pid": pid, "name": e["name"], "team": team, "opp": opp, "home": e["home"], "game": e["game"],
                    "start": e["start"], "state": e["state"], "role": e["role"], "pos": nm.ROLE_NAME.get(e["role"], ""),
                    "minutes": e["minutes"], "status": e["status"], "out": e["out"],
                    "rw": ({k: e["rw"].get(k) for k in ("starter", "pos", "status", "inj", "pct", "url", "note")} if e["rw"] else None),
                    "espn": f"https://www.espn.com/nba/player/_/id/{pid}",
                    "mates": [{"pid": m, "name": model.players[m]["name"] if m in model.players else m} for m in mate_ids],
                    "logs": L, "proj": proj, "dvp": dvp, "comps": comps(model, pid, opp)}
    return out


def comps(model, pid, opp, k=6):
    """Players with his statistical profile and how they did against this opponent (2 seasons)."""
    if pid not in model.players:
        return []
    by = defaultdict(list)
    for r, rr, w in model.by_opp.get(opp, []):
        if r["pid"] != pid and r["pid"] in model.players:
            by[r["pid"]].append(r)
    scored = []
    for p2, rs in by.items():
        sm = model.sim(pid, p2)
        if sm < 0.2:
            continue
        scored.append((sm * (1 - math.exp(-len(rs) / 2)), p2, rs, sm))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, p2, rs, sm in scored[:k]:
        mins = sum(r["min"] for r in rs)
        st = {}
        for s in ALL_STATS:
            act = sum(r[s] for r in rs) / len(rs)
            exp = sum(model.rate(p2, s, r["season"]) * r["min"] for r in rs) / len(rs)
            st[s] = [round(act, 1), round(exp, 1)]
        out.append({"name": model.players[p2]["name"], "team": model.players[p2]["team"], "role": model.players[p2]["role"],
                    "sim": round(sm, 2), "n": len(rs), "min": round(mins / len(rs), 1), "stats": st})
    return out


def build_shots(ctx, pids):
    sdb = nd.load_shots(ctx["season"])
    stype = {gid: g.get("st", 2) for gid, g in nd.load(ctx["season"])["games"].items()}
    want = set(pids)
    out = defaultdict(list)
    for gid, shots in sdb["games"].items():
        if stype.get(gid, 2) not in (2, 3):          # regular season and playoffs only
            continue
        day = sdb["dates"].get(gid, "")
        for aid, x, y, made, three, per in shots:
            if aid in want:
                out[aid].append([x, y, made, three, day])
    for k in out:
        out[k].sort(key=lambda r: r[4])
        out[k] = out[k][-600:]
    return out


# ------------------------------------------------------------------ dvp.json
def build_dvp(model):
    tab = {}
    for (o, role, s), (f, n) in model.dvp_tab.items():
        tab.setdefault(o, {}).setdefault(role, {})[s] = [round(f, 3), model.dvp_rank.get((o, role, s), 0)]
    teams = {t: {k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()} for t, d in model.teams.items()}
    return {"roles": nm.ROLE_NAME, "stats": {s: nm.STATS[s] for s in ALL_STATS}, "n_teams": model.n_teams,
            "tab": tab, "teams": teams}


# ------------------------------------------------------------------ trends.json
def build_trends(ctx):
    model, cur = ctx["model"], ctx["season"]
    games = [x for x in nd.all_games([cur - 1, cur]) if x[2].get("st", 2) == 2]
    curg = [x for x in games if x[0] == cur]
    sections = []

    # players: hot / cold over the last 5 games vs their own season norm (minutes-adjusted)
    hot, cold, risers = [], [], []
    for pid, rs in model.by_player.items():
        if pid not in model.players or len(rs) < 15 or not any(r["season"] == cur for r in rs[-5:]):
            continue
        if (ctx["day"] - datetime.fromisoformat(rs[-1]["d"]).date()).days > 14:
            continue
        last5, prior = rs[-5:], rs[-45:-5]
        m5 = sum(r["min"] for r in last5) / 5
        mp = sum(r["min"] for r in prior) / len(prior)
        name = f"{model.players[pid]['name']} ({model.players[pid]['team']})"
        if m5 >= 24 and m5 - mp >= 5:
            risers.append((m5 - mp, f"{name}: {m5:.0f} min/g in his last 5 vs {mp:.0f} in the 40 before"))
        if m5 < 20:
            continue
        for s_ in ("ra", "ast", "tpm", "pra"):
            a5 = sum(r[s_] for r in last5) / 5
            rate = sum(r[s_] for r in prior) / max(1, sum(r["min"] for r in prior))
            exp = rate * m5
            if exp < (1.2 if s_ == "tpm" else 3.0 if s_ == "ast" else 5.0):
                continue
            d = (a5 - exp) / exp
            txt = f"{name}: {a5:.1f} {nm.STATS[s_]}/g last 5 vs {exp:.1f} expected for his minutes"
            (hot if d > 0 else cold).append((abs(d), txt, s_))
    def per_stat(xs, k=3):
        xs.sort(reverse=True)
        out = []
        for st_ in ("ra", "ast", "tpm", "pra"):
            out += [x for x in xs if x[2] == st_][:k]
        return out
    hot, cold = per_stat(hot), per_stat(cold)
    risers.sort(reverse=True)
    sections.append({"id": "hot", "title": "Hot players (last 5 vs their norm)", "items": [
        {"txt": t, "val": f"+{d:.0%}", "tone": "good", "stat": s} for d, t, s in hot[:12]]})
    sections.append({"id": "cold", "title": "Cold players (last 5 vs their norm)", "items": [
        {"txt": t, "val": f"-{d:.0%}", "tone": "bad", "stat": s} for d, t, s in cold[:12]]})
    sections.append({"id": "minutes", "title": "Minutes risers", "items": [
        {"txt": t, "val": f"+{d:.0f}", "tone": "good"} for d, t in risers[:10]]})

    # teams: pace and scoring, last 10 vs season; ATS and O/U records from ESPN's pregame lines
    by_team = defaultdict(list)
    for s, gid, g in curg:
        ab = list(g["teams"])
        if len(ab) != 2:
            continue
        line = g.get("line") or {}
        for t in ab:
            o = ab[1] if t == ab[0] else ab[0]
            me, th = g["teams"][t], g["teams"][o]
            ts = g["tstats"].get(t) or {}
            poss = ts.get("fga", 0) + 0.44 * ts.get("fta", 0) - ts.get("oreb", 0) + ts.get("tov", 0)
            rec = {"d": g["d"], "pf": me["score"], "pa": th["score"], "poss": poss, "home": me["home"]}
            if line.get("spread") is not None:
                sp = line["spread"] if me["home"] else -line["spread"]
                m = me["score"] - th["score"] + sp
                rec["ats"] = "W" if m > 0 else "L" if m < 0 else "P"
            if line.get("total") is not None:
                tot = me["score"] + th["score"]
                rec["ou"] = "O" if tot > line["total"] else "U" if tot < line["total"] else "P"
            ls = g.get("ls", {})
            if len(ls.get(t, [])) >= 4 and len(ls.get(o, [])) >= 4:
                rec["h1"] = sum(ls[t][:2]) - sum(ls[o][:2])
                rec["h2"] = sum(ls[t][2:]) - sum(ls[o][2:])
            by_team[t].append(rec)
    team_rows = []
    for t, rs in by_team.items():
        rs.sort(key=lambda r: r["d"])
        l10 = rs[-10:]
        ats = [r["ats"] for r in rs if "ats" in r]
        ou = [r["ou"] for r in rs if "ou" in r]
        l10ou = [r["ou"] for r in l10 if "ou" in r]
        T = model.teams.get(t, {})
        team_rows.append({"team": t, "gp": len(rs), "ppg": round(sum(r["pf"] for r in rs) / len(rs), 1),
                          "oppg": round(sum(r["pa"] for r in rs) / len(rs), 1),
                          "ppg10": round(sum(r["pf"] for r in l10) / len(l10), 1),
                          "oppg10": round(sum(r["pa"] for r in l10) / len(l10), 1),
                          "pace10": round(sum(r["poss"] for r in l10) / len(l10), 1),
                          "pace": round(T.get("pace", 0), 1), "pace_rank": T.get("pace_rank"),
                          "ortg_rank": T.get("ortg_rank"), "drtg_rank": T.get("drtg_rank"),
                          "ats": f"{ats.count('W')}-{ats.count('L')}" + (f"-{ats.count('P')}" if ats.count("P") else ""),
                          "ou": f"{ou.count('O')}-{ou.count('U')}" + (f"-{ou.count('P')}" if ou.count("P") else ""),
                          "ou10": f"{l10ou.count('O')}-{l10ou.count('U')}", "lined": len(ats),
                          "h2_net": round(sum(r.get("h2", 0) for r in rs) / len(rs), 1)})
    team_rows.sort(key=lambda r: r["team"])
    if team_rows:
        fast = sorted(team_rows, key=lambda r: -(r["pace10"] - r["pace"]))
        sections.append({"id": "pace", "title": "Pace changes (last 10 vs baseline)", "items": [
            {"txt": f"{r['team']}: {r['pace10']:.1f} possessions/g last 10 vs {r['pace']:.1f} baseline", "val": f"{r['pace10'] - r['pace']:+.1f}",
             "tone": "good" if r["pace10"] > r["pace"] else "bad"} for r in fast[:4] + fast[-4:]]})
        overs = sorted((r for r in team_rows if r["lined"] >= 3), key=lambda r: -int(r["ou"].split("-")[0]) / max(1, r["lined"]))
        sections.append({"id": "ou", "title": "Over/under records (ESPN pregame totals)", "items": [
            {"txt": f"{r['team']}: overs {r['ou']} this season, {r['ou10']} last 10", "val": r["ou"], "tone": "good" if i < 5 else "bad"}
            for i, r in enumerate(overs[:5] + overs[-5:])]})
        atsr = sorted((r for r in team_rows if r["lined"] >= 3), key=lambda r: -int(r["ats"].split("-")[0]) / max(1, r["lined"]))
        sections.append({"id": "ats", "title": "Against the spread", "items": [
            {"txt": f"{r['team']}: {r['ats']} ATS", "val": r["ats"], "tone": "good" if i < 5 else "bad"}
            for i, r in enumerate(atsr[:5] + atsr[-5:])]})
        h2 = sorted(team_rows, key=lambda r: -r["h2_net"])
        sections.append({"id": "h2", "title": "Second-half teams (net 2H margin per game)", "items": [
            {"txt": f"{r['team']}: {r['h2_net']:+.1f} in second halves", "val": f"{r['h2_net']:+.1f}", "tone": "good" if r["h2_net"] > 0 else "bad"}
            for r in h2[:4] + h2[-4:]]})

    # matchups: softest and toughest team/position spots this season
    soft, tough = [], []
    for (o, role, s), (f, n) in model.dvp_tab.items():
        if s not in ("ra", "ast", "tpm", "pra") or n < 8:
            continue
        item = (f, f"{o} vs {nm.ROLE_NAME[role]}: {(f - 1) * 100:+.0f}% {nm.STATS[s]} vs norm", s)
        soft.append(item)
        tough.append(item)
    soft.sort(key=lambda x: -x[0])
    tough.sort(key=lambda x: x[0])
    sections.append({"id": "soft", "title": "Softest matchups (defense vs position)", "items": [
        {"txt": t, "val": f"{(f - 1):+.0%}", "tone": "good", "stat": s} for f, t, s in soft[:10]]})
    sections.append({"id": "tough", "title": "Toughest matchups (defense vs position)", "items": [
        {"txt": t, "val": f"{(f - 1):+.0%}", "tone": "bad", "stat": s} for f, t, s in tough[:10]]})

    # halftime comebacks (the 2H Comeback theory): teams trailing by 10+ at the half
    hc = {"n": 0, "won_2h": 0, "won_game": 0, "net_2h": 0.0, "covered_2h_spread_5": 0}
    for s, gid, g in games:
        ls = g.get("ls", {})
        ab = list(g["teams"])
        if len(ab) != 2 or any(len(ls.get(t, [])) < 4 for t in ab):
            continue
        a, b = ab
        ha, hb = sum(ls[a][:2]), sum(ls[b][:2])
        if abs(ha - hb) < 10:
            continue
        trail, lead = (a, b) if ha < hb else (b, a)
        t2, l2 = sum(ls[trail][2:]), sum(ls[lead][2:])
        hc["n"] += 1
        hc["won_2h"] += t2 > l2
        hc["won_game"] += g["teams"][trail]["score"] > g["teams"][lead]["score"]
        hc["net_2h"] += t2 - l2
    if hc["n"]:
        sections.append({"id": "comeback", "title": "Trailing by 10+ at halftime (last two seasons)", "items": [
            {"txt": f"{hc['n']} games: the trailing team won the second half {hc['won_2h']} times ({hc['won_2h'] / hc['n']:.0%})",
             "val": f"{hc['won_2h'] / hc['n']:.0%}", "tone": "good" if hc['won_2h'] / hc['n'] > 0.5 else "mid"},
            {"txt": f"Average second-half margin for the trailing team: {hc['net_2h'] / hc['n']:+.1f}", "val": f"{hc['net_2h'] / hc['n']:+.1f}", "tone": "mid"},
            {"txt": f"The trailing team came back to win {hc['won_game']} times ({hc['won_game'] / hc['n']:.0%})",
             "val": f"{hc['won_game'] / hc['n']:.0%}", "tone": "mid"}]})

    # rest: back-to-backs on tonight's slate
    b2b = []
    try:
        yday = ctx["day"] - timedelta(days=1)
        played = {x for g in slate_all(yday) for x in (g["home"], g["away"])}
        for g in ctx["games"]:
            for t, o in ((g["home"], g["away"]), (g["away"], g["home"])):
                if t in played:
                    b2b.append({"txt": f"{t} on a back-to-back vs {o}" + (" (opponent rested)" if o not in played else " (both teams)"),
                                "val": "B2B", "tone": "bad"})
    except Exception:
        pass
    sections.append({"id": "rest", "title": "Rest: back-to-backs on the slate", "items": b2b or [{"txt": "No back-to-backs on this slate.", "val": "", "tone": "mid"}]})
    return {"sections": sections, "teams": team_rows}


# ------------------------------------------------------------------ games for the slate
def game_cards(ctx):
    out = []
    for g in ctx["games"]:
        tp = team_projection(ctx["model"], g["home"], g["away"])
        out.append({**{k: g[k] for k in ("espn_id", "start", "state", "home", "away", "home_name", "away_name")},
                    "model": tp})
    return out


def _index_row(p):
    """The light version of a player for lists: no logs, no signals."""
    return {"pid": p["pid"], "name": p["name"], "team": p["team"], "opp": p["opp"], "home": p["home"], "game": p["game"],
            "start": p["start"], "state": p["state"], "role": p["role"], "minutes": p["minutes"], "status": p["status"],
            "out": p["out"], "starter": (p["rw"] or {}).get("starter"), "lineup": (p["rw"] or {}).get("status"),
            "proj": {s: [v["proj"], v["base"], v["mult"]] for s, v in p["proj"].items()},
            "dvp": {s: v[1] for s, v in p["dvp"].items()}}


def write_all(ctx, log=print):
    players = build_players(ctx)
    shots = build_shots(ctx, players.keys())
    stamp = {"updated": store.iso(store.now()), "date": ctx["day"].isoformat()}
    cards = game_cards(ctx)
    # light index for lists, one detail file per game for player cards (keeps phone downloads small)
    store.save("players.json", {**stamp, "games": cards, "players": {k: _index_row(v) for k, v in players.items()},
                                "stat_names": {s: nm.STATS[s] for s in ALL_STATS}}, compact=True)
    gdir = config.DATA / "games"
    gdir.mkdir(parents=True, exist_ok=True)
    live = set()
    for g in cards:
        ps = {k: v for k, v in players.items() if v["game"] == g["espn_id"]}
        store.save(f"games/{g['espn_id']}.json", {**stamp, "game": g, "players": ps,
                                                   "shots": {k: shots.get(k, []) for k in ps}}, compact=True)
        live.add(f"{g['espn_id']}.json")
    for f in gdir.glob("*.json"):
        if f.name not in live:
            f.unlink()
    store.save("dvp.json", {**stamp, **build_dvp(ctx["model"])}, compact=True)
    store.save("trends.json", {**stamp, **build_trends(ctx)})
    store.save("lineups.json", {**stamp, "games": ctx["rw_games"]})
    log(f"[research] {ctx['day']}: {len(ctx['games'])} games, {len(players)} players, "
        f"{sum(len(v) for v in shots.values())} shots, lineups for {len(ctx['rw_games'])} games")
    return players
