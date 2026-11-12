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
from . import conviction as cvm

ALL_STATS = ("ra", "ast", "tpm", "pra", "pts", "reb")
TUNE = {}            # learned corrections (tuning.json), loaded each context build
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
    """Today's slate while any game is still to tip or in progress (so tonight's live games stay on the dashboard);
    once every game tonight is final, tomorrow's."""
    d = et_today()
    games = slate_all(d)
    if games and any(g["state"] in ("pre", "in") for g in games):
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
    TUNE.clear(); TUNE.update(store.load("tuning.json", {}))
    inj = nd.injuries()
    try:
        from . import injury_report
        official, inj_time = injury_report.fetch(model.players, norm)
        inj.update(official)                 # the league's own report wins over ESPN's
    except Exception as e:
        log(f"[research] official injury report failed: {e}")
        official, inj_time = {}, None
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
    pre = {"games": games, "model": model, "day": day}
    flags, cards = slate_flags(pre, team_dates(model))
    cleared = {pid: True for pid, st in official.items() if st == "" or "probable" in st.lower()}
    # One status per player for the minutes model AND the projections: ESPN / the league's report first, RotoWire's
    # injury tag when they have nothing. (Before Nov 12 the minutes model saw only the first, so a player RotoWire had
    # ruled out kept his minutes in the 240 fit and his teammates were cut, then he was dropped: teams came up short.)
    nick_of = {}
    for g in games:
        nick_of[g["home"]], nick_of[g["away"]] = g.get("home_nick", ""), g.get("away_nick", "")
    inj_all = dict(inj)
    for pid, pl in model.players.items():
        if inj_all.get(pid):
            continue
        r = rw.get(norm(pl["name"]))
        if r and r.get("inj") and (not r.get("nick") or r["nick"].lower() == (nick_of.get(pl["team"]) or "").lower()):
            inj_all[pid] = r["inj"]
    # and the 240 is shared only among players who can actually get a projection below (3+ games, played in the last 30 days)
    eligible = {pid for pid, pl in model.players.items() if pl["games"] >= 3 and model.by_player[pid]
                and (day - datetime.fromisoformat(model.by_player[pid][-1]["d"]).date()).days <= 30}
    mins_model = minutes_model(model, games, inj_all, rw, flags, cleared, eligible)
    entries = []
    for g in games:
        for team, opp, home, nick in ((g["home"], g["away"], True, g["home_nick"]), (g["away"], g["home"], False, g["away_nick"])):
            for pid in roster.get(team, []):
                pl = model.players[pid]
                if pl["games"] < 3:
                    continue
                mins, mwhy, pplay = (list(mins_model.get(pid, [model.minutes(pid), []])) + [1.0])[:3]
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
                    pr = model.project(pid, s, opp, home, W, mins=mins if mins > 0 else None)
                    fs = (TUNE.get("stat_factor") or {}).get(s)
                    if fs and abs(fs - 1) >= 0.005:          # learned level correction (keeps the matchup lean)
                        pr["proj"] = round(pr["proj"] * fs, 2); pr["base"] = round(pr["base"] * fs, 2)
                        pr["var"] = (pr.get("var") or 0) * fs * fs
                    proj[s] = pr
                entries.append({"pid": pid, "name": pl["name"], "team": team, "opp": opp, "home": home, "game": g["espn_id"],
                                "start": g["start"], "state": g["state"], "role": pl["role"], "minutes": round(mins, 1),
                                "status": status, "out": _status_out(status), "rw": r, "proj": proj,
                                "min_base": round(model.minutes(pid), 1), "min_why": mwhy, "pplay": pplay})
    try:
        onoff_adjust(model, entries, day, log)
    except Exception as e:
        log(f"[research] lineup on/off data unavailable: {e}")
    try:
        situations(model, games, cards, day, log)
    except Exception as e:
        log(f"[research] schedule/referee context failed: {e}")
    return {"day": day, "games": games, "model": model, "inj": inj, "rw_games": rw_games, "rw": rw,
            "entries": entries, "weights": W, "season": cur, "flags": flags, "slate_cards": cards,
            "inj_report": inj_time, "inj_official": len(official)}


# ------------------------------------------------------------------ lineup data: who absorbs the work when a player sits
def onoff_adjust(model, entries, day, log=print):
    """For every rotation player who is out tonight, pull what each teammate does in the possessions he is off the
    floor (pbpstats, this season and last) and move tonight's projections by the difference. A player who has
    already missed the recent games is mostly in the baseline already, so the adjustment shrinks on its own."""
    from . import pbp
    today = day.isoformat()
    cur = model.cur
    P = {cur: pbp.players(cur, today, log), cur - 1: pbp.players(cur - 1, today, log)}
    if not P[cur]:
        return
    ids = pbp.id_map(P[cur], model.players)
    for pid, nid in pbp.id_map(P[cur - 1] or {}, model.players).items():
        ids.setdefault(pid, nid)
    by_team = defaultdict(list)
    for e in entries:
        by_team[e["team"]].append(e)
        nid = ids.get(e["pid"])
        prof = pbp.profile((P[cur]["p"] or {}).get(nid)) if nid else None
        if prof:
            e["pbp"] = prof
    played_dates = defaultdict(set)
    for r in model.rows:
        played_dates[r["pid"]].add((r["team"], r["d"]))
    n_adj = 0
    for team, es in by_team.items():
        outs = sorted((e for e in es if e["out"] and e["min_base"] >= 18 and ids.get(e["pid"])), key=lambda e: -e["min_base"])[:3]
        for o in outs:
            oid = ids[o["pid"]]
            parts = []
            for season, w in ((cur, 1.0), (cur - 1, 0.5)):
                tid = ((P.get(season) or {}).get("teams") or {}).get(team)
                if not tid:
                    continue
                allr = pbp.team_totals(tid, season, today, log)
                if not allr or oid not in allr or (allr[oid].get("Minutes") or 0) < 150:
                    continue                          # he wasn't a regular on this team that season
                offr = pbp.wowy_off(tid, oid, season, today, log)
                if offr:
                    parts.append((w, allr, offr))
            if not parts:
                continue
            A, O = pbp.combine(parts)
            o_last = re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV)$", "", o["name"]).split()[-1]
            for m in es:
                if m["out"] or m["pid"] == o["pid"] or not ids.get(m["pid"]):
                    continue
                eff = pbp.off_effect(A, O, ids[m["pid"]])
                if not eff or not eff["r"]:
                    continue
                # how much of his baseline was played next to the absent player
                rows = model.by_player[m["pid"]][-40:]
                ws = [r["sw"] * 0.96 ** i for i, r in enumerate(reversed(rows))][::-1]
                wt = sum(ws) or 1
                f_recent = sum(w for w, r in zip(ws, rows) if (r["team"], r["d"]) in played_dates[o["pid"]]) / wt
                mine = [r for r in model.by_player[m["pid"]] if r["team"] == team]
                g_frac = (sum(1 for r in mine if (team, r["d"]) in played_dates[o["pid"]]) / len(mine)) if mine else 1.0
                on_when_playing = min(0.92, eff["share_on"] / g_frac) if g_frac > 0.05 else 0.0
                p_on = on_when_playing * f_recent
                # Out of sample (tests/onoff.py, 73 absences): the raw on/off ratio is right in direction but about three
                # times too big, and everyone left on the floor gains a little per minute. Level and slope from that check,
                # scaled by how much of his baseline was played next to the absent player and how big that player's role is.
                role = min(1.0, o["min_base"] / 30.0)
                mult = {}
                for st, r in eff["r"].items():
                    lvl, slope = ONOFF_CAL[st]
                    k = math.exp(lvl * f_recent * role + slope * math.log(pbp.tonight_mult(r, p_on)))
                    mult[st] = max(0.9, min(1.25, k))
                if all(abs(v - 1) < 0.02 for v in mult.values()):
                    continue
                n_adj += 1
                b = {st: m["proj"][st]["base"] for st in ("pts", "reb", "ast")}
                mult["pra"] = sum(b[k] * mult.get(k, 1) for k in b) / (sum(b.values()) or 1)
                mult["ra"] = (b["reb"] * mult.get("reb", 1) + b["ast"] * mult.get("ast", 1)) / ((b["reb"] + b["ast"]) or 1)
                u = eff.get("usage")
                for st, k in mult.items():
                    pr = m["proj"].get(st)
                    if not pr or abs(k - 1) < 0.02:
                        continue
                    pr["proj"] = round(pr["proj"] * k, 2); pr["base"] = round(pr["base"] * k, 2)
                    pr["var"] = (pr.get("var") or 0) * k * k
                    txt = (f"{o_last} out: {nm.STATS[st]} {_pct_s(k)} tonight. With him off the floor ({eff['poss']:,} possessions)"
                           + (f" his usage goes {u[0]:.0f}% → {u[1]:.0f}%" if u else " his per-possession numbers change")
                           + (f"; {f_recent * 100:.0f}% of his recent games were next to {o_last}" if f_recent < 0.9 else ""))
                    pr["signals"].append({"k": "onoff", "v": round(k, 3), "n": eff["poss"], "txt": txt})
                    m.setdefault("onoff", {})[st] = {"k": round(k, 3), "out": o_last, "poss": eff["poss"]}
    log(f"[research] lineup on/off: {n_adj} teammate adjustments")


ONOFF_CAL = {"pts": (0.055, 0.25), "ast": (0.045, 0.38), "reb": (0.012, 0.36), "tpm": (0.0, 0.15)}     # (level, slope)


def _pct_s(m):
    return f"{(m - 1) * 100:+.0f}%"


# ------------------------------------------------------------------ schedule spots and referees
def situations(model, games, cards, day, log=print):
    """Per game: each team's schedule spot (rest, miles, time zones, altitude) with its estimated point value, and
    tonight's referee crew with how its officials' games have run. Adds `spots` and `refs` to each game card."""
    from . import travel, refs
    site_hist = defaultdict(list)                       # team -> [(date, site team)]
    seen = set()
    for r in model.rows:
        k = (r["team"], r["d"])
        if k in seen:
            continue
        seen.add(k)
        site_hist[r["team"]].append((r["d"], r["team"] if r["home"] else r["opp"]))
    try:
        all_g = nd.all_games([model.cur - 2, model.cur - 1, model.cur])
        dates = {d for _, _, g in all_g if g.get("st", 2) == 2 for d in (g["d"],)}
        db = refs.update(dates, today=day.isoformat(), log=log)
        tend, _ = refs.tendencies(all_g, db)
    except Exception as e:
        log(f"[research] referee data failed: {e}")
        db, tend = {}, {}
    for g in games:
        card = cards.setdefault(g["espn_id"], {})
        gday = datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone(nd.ET).date()
        spots = {}
        for side in ("home", "away"):
            t = g[side]
            f = travel.features(t, g["home"], gday, site_hist.get(t, []))
            pts, why = travel.margin_points(f)
            spots[side] = {"pts": pts, "why": [[v, w] for v, w in why], "rest": f["rest"], "b2b": f["b2b"], "miles": f["miles"],
                           "miles7": f["miles7"], "tz": f["tz"], "alt": f["alt"], "road_trip": f["road_trip"], "games7": f["games7"]}
        spots["net_home"] = round(spots["home"]["pts"] - spots["away"]["pts"], 2)
        card["spots"] = spots
        cr = refs.crew(db, gday.isoformat(), g["home"], g["away"])
        if cr:
            ce = refs.crew_effect(tend, cr)
            card["refs"] = {"crew": cr, **({"fta": ce["fta"], "tot": ce["tot"], "n": ce["n"]} if ce else {})}


# ------------------------------------------------------------------ team model (display only)
def team_projection(model, home, away, net_home=0.0):
    T = model.teams
    if home not in T or away not in T:
        return None
    lg_o = sum(t["ortg"] for t in T.values()) / len(T)
    pace = (T[home]["pace"] + T[away]["pace"]) / 2
    hp = pace * (T[home]["ortg"] + T[away]["drtg"] - lg_o) / 100 + 1.4
    ap = pace * (T[away]["ortg"] + T[home]["drtg"] - lg_o) / 100 - 1.4
    if net_home:                                    # schedule spots (back-to-back, eastward travel): half to each team's score
        hp += net_home / 2; ap -= net_home / 2
    return {"home": round(hp, 1), "away": round(ap, 1), "total": round(hp + ap, 1), "spread_home": round(ap - hp, 1),
            "pace": round(pace, 1)}



# ------------------------------------------------------------------ slate context: rest, Vegas, blowout risk
def _nick(name):
    return (name or "").split()[-1].lower()


def market_lines(day=None):
    """NBA spreads and totals from the agent's last board scan (or DraftKings' free line): {(home_nick, away_nick): {...}}."""
    out = {}
    for e in store.load("board.json", {"events": []}).get("events", []):
        if e.get("sport") != "basketball_nba":
            continue
        sp = tot = None
        for l in e.get("lines", []):
            if l["market"] == "spreads":
                sp = next((o["point"] for o in l["outcomes"] if o["name"] == e["home"]), None)
            elif l["market"] == "totals":
                tot = next((o["point"] for o in l["outcomes"] if o["name"] == "Over"), None)
        if sp is not None or tot is not None:
            out[(_nick(e["home"]), _nick(e["away"]))] = {"spread_home": sp, "total": tot}
    # Free fallback: DraftKings' own line from ESPN's odds feed (saved by dk.refresh on earlier runs today).
    # The paid board only covers the games the agent spent credits on; without this the sims and the
    # minutes model run with no market anchor for every other game.
    if day is not None:
        for d in (day, day + timedelta(days=1)):
            for g in store.load(f"dk/{d.isoformat()}.json", {}).get("games", {}).values():
                key = (_nick(g.get("home_name")), _nick(g.get("away_name")))
                ln = g.get("close") or g.get("open") or {}
                if key in out or ln.get("sp") is None or ln.get("tot") is None:
                    continue
                out[key] = {"spread_home": ln["sp"], "total": ln["tot"], "src": "draftkings"}
    return out


def team_dates(model):
    dates = defaultdict(set)
    for r in model.rows:
        dates[r["team"]].add(r["d"])
    return dates


def slate_flags(ctx, dates):
    """Per team on the slate: rest (back-to-back, 3 in 4), spread, Vegas implied points vs the model's."""
    mk = market_lines(ctx.get("day"))
    flags, cards = {}, {}
    for g in ctx["games"]:
        gd = datetime.fromisoformat(g["start"][:10]).date()
        tp = team_projection(ctx["model"], g["home"], g["away"])
        m = mk.get((_nick(g.get("home_name")), _nick(g.get("away_name")))) or {}
        sp, tot = m.get("spread_home"), m.get("total")
        imp = None
        if sp is not None and tot is not None:
            imp = {"home": round(tot / 2 - sp / 2, 1), "away": round(tot / 2 + sp / 2, 1)}
        spread_abs = abs(sp) if sp is not None else (abs(tp["spread_home"]) if tp else None)
        card = {"spread_home": sp, "total": tot, "implied": imp, "src": m.get("src", "market") if sp is not None else "model"}
        for side in ("home", "away"):
            t = g[side]
            prev = sorted({d for d in dates.get(t, []) if datetime.fromisoformat(d).date() < gd})
            pdates = [datetime.fromisoformat(d).date() for d in prev]
            b2b = bool(pdates) and (gd - pdates[-1]).days == 1
            three = sum(1 for d in pdates if 1 <= (gd - d).days <= 3) >= 2
            vd = round(imp[side] - tp[side], 1) if imp and tp else None
            flags[t] = {"b2b": b2b, "three_in_four": three and not b2b, "spread": spread_abs, "vegas_diff": vd,
                        "rest_days": (gd - pdates[-1]).days - 1 if pdates else None}
            card[side + "_rest"] = {"b2b": b2b, "three_in_four": three and not b2b}
        cards[g["espn_id"]] = card
    return flags, cards


def usage_bumps(ctx, logs, played, on_team):
    """When a rotation player is out, how much more each teammate produced in games he missed."""
    model, entries = ctx["model"], ctx["entries"]
    outs = [e for e in entries if e["out"] and model.minutes(e["pid"]) >= 20]
    bumps, opps = defaultdict(dict), []
    for o in outs:
        seasons = {s for (t, s) in on_team.get(o["pid"], set()) if t == o["team"]}
        for m in entries:
            if m["team"] != o["team"] or m["out"] or m["pid"] == o["pid"]:
                continue
            rows = [r for r in logs.get(m["pid"], []) if r["team"] == o["team"] and r["s"] in seasons and r["min"] > 0]
            wo = [r for r in rows if o["pid"] not in played.get(r["gid"], set())]
            w = [r for r in rows if o["pid"] in played.get(r["gid"], set())]
            if len(wo) < 3 or len(w) < 5:
                continue
            for s in ALL_STATS:
                f = lambda r: (r["reb"] + r["ast"]) if s == "ra" else (r["pts"] + r["reb"] + r["ast"]) if s == "pra" else r[s]
                aw, awo = sum(map(f, w)) / len(w), sum(map(f, wo)) / len(wo)
                if aw <= 0:
                    continue
                d = awo / aw - 1
                if d >= 0.12 and awo - aw >= 1.0:
                    prev = bumps[m["pid"]].get(s)
                    if not prev or d > prev["delta"]:
                        bumps[m["pid"]][s] = {"delta": round(d, 3), "out": o["name"].split()[-1], "n": len(wo)}
                    opps.append({"out": o["name"], "out_pid": o["pid"], "status": o["status"], "team": o["team"],
                                 "pid": m["pid"], "name": m["name"], "game": m["game"], "stat": s,
                                 "with": round(aw, 1), "without": round(awo, 1), "delta": round(d, 3), "n": len(wo),
                                 "min_with": round(sum(r["min"] for r in w) / len(w), 1),
                                 "min_without": round(sum(r["min"] for r in wo) / len(wo), 1)})
    # one entry per teammate and stat (the absence that helped most), ranked by real extra production
    best = {}
    for x in opps:
        k = (x["pid"], x["stat"])
        if k not in best or x["without"] - x["with"] > best[k]["without"] - best[k]["with"]:
            best[k] = x
    opps = sorted(best.values(), key=lambda x: -(x["without"] - x["with"]) / (1.0 if x["stat"] != "tpm" else 0.4))
    return bumps, opps


# ------------------------------------------------------------------ minutes model
def rotation_profile(model):
    """How each coach uses his starters, from this season and last (last season counts less):
    how many minutes they lose in blowouts (final margin 15+) and gain in close games (5 or less),
    compared with the league. Shrunk toward the league average when a team has few such games."""
    acc = defaultdict(lambda: {"all": [0.0, 0.0], "blow": [0.0, 0.0], "close": [0.0, 0.0], "nb": 0.0, "nc": 0.0})
    seen = set()
    for r in model.rows:
        if not r.get("start"):
            continue
        a = acc[r["team"]]; w = r["sw"]
        a["all"][0] += w * r["min"]; a["all"][1] += w
        m = abs(r.get("mar") or 0)
        key = (r["team"], r["d"])
        if m >= 15:
            a["blow"][0] += w * r["min"]; a["blow"][1] += w
            if key not in seen: a["nb"] += w
        elif m <= 5:
            a["close"][0] += w * r["min"]; a["close"][1] += w
            if key not in seen: a["nc"] += w
        seen.add(key)
    avg = lambda x: x[0] / x[1] if x[1] else None
    drops, bumps = [], []
    for a in acc.values():
        al, bl, cl = avg(a["all"]), avg(a["blow"]), avg(a["close"])
        if al and bl: drops.append(al - bl)
        if al and cl: bumps.append(cl - al)
    lg_drop = sum(drops) / len(drops) if drops else 4.0
    lg_bump = sum(bumps) / len(bumps) if bumps else 1.0
    out = {"_league": {"drop": round(lg_drop, 2), "bump": round(lg_bump, 2)}}
    for t, a in acc.items():
        al, bl, cl = avg(a["all"]), avg(a["blow"]), avg(a["close"])
        k = 8.0                                            # team games worth of league prior
        drop = ((al - bl) * a["nb"] + lg_drop * k) / (a["nb"] + k) if al and bl else lg_drop
        bump = ((cl - al) * a["nc"] + lg_bump * k) / (a["nc"] + k) if al and cl else lg_bump
        out[t] = {"drop": round(drop, 2), "bump": round(bump, 2), "nb": round(a["nb"], 1), "nc": round(a["nc"], 1)}
    return out


def minutes_model(model, games, inj, rw, flags, official_ok=None, eligible=None):
    """Tonight's minutes for every rotation player: recent minutes, then adjusted for teammates who are
    newly out (their with/without history), a change in starting role, back-to-backs and blowout risk.
    Returns {pid: (minutes, [reasons])}."""
    played = defaultdict(set)                     # (team, date) -> players who played
    by_tm = defaultdict(list)
    for r in model.rows:
        played[(r["team"], r["d"])].add(r["pid"])
    for (t, d) in played:
        by_tm[t].append(d)
    for t in by_tm:
        by_tm[t].sort()
    out, meta = {}, {}
    teams = {g[k] for g in games for k in ("home", "away")}
    rot = rotation_profile(model)
    lg = rot["_league"]
    for t in teams:
        rp = rot.get(t, lg)
        roster = [pid for pid, pl in model.players.items() if pl["team"] == t and model.by_player[pid]
                  and (model.by_player[pid][-1]["d"] >= (by_tm[t][-12] if len(by_tm[t]) >= 12 else "0"))]
        last2 = by_tm[t][-2:]
        is_out = lambda pid: _status_out(inj.get(pid, ""))
        fresh_outs = [o for o in roster if is_out(o) and model.minutes(o) >= 12
                      and any(o in played[(t, d)] for d in last2)]
        base = {pid: model.minutes(pid) for pid in roster}
        active = [pid for pid in roster if not is_out(pid) and (eligible is None or pid in eligible)]
        f = flags.get(t, {})
        tot_active = sum(base[p] for p in active) or 1
        sname = lambda pid: re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV)$", "", model.players[pid]["name"]).split()[-1]
        adds = {}
        for pid in active:
            rows = [r for r in model.by_player[pid] if r["team"] == t]
            for o in fresh_outs:
                seasons = {r["season"] for r in model.by_player[o] if r["team"] == t}
                w = [r["min"] for r in rows if r["season"] in seasons and o in played[(t, r["d"])]]
                wo = [r["min"] for r in rows if r["season"] in seasons and o not in played[(t, r["d"])]]
                if len(wo) >= 3 and len(w) >= 5:
                    d = sum(wo) / len(wo) - sum(w) / len(w)
                else:
                    d = model.minutes(o) * 0.8 * base[pid] / tot_active
                d = max(-4, min(10, d))
                if abs(d) >= 0.5:
                    adds.setdefault(pid, []).append((d, o))
        # the extra minutes can't exceed what the missing players actually played
        pos = sum(d for v in adds.values() for d, _ in v if d > 0)
        avail = sum(model.minutes(o) for o in fresh_outs)
        k_add = min(1.0, avail / pos) if pos > 0 else 1.0
        for pid in active:
            m, why = base[pid], []
            rows = [r for r in model.by_player[pid] if r["team"] == t]
            for d, o in adds.get(pid, []):
                d = d * k_add if d > 0 else d
                if abs(d) >= 0.5:
                    m += d; why.append(f"{d:+.1f} min with {sname(o)} out")
            r_ = rw.get(norm(model.players[pid]["name"]))
            recent = rows[-10:]
            srate = sum(r["start"] for r in recent) / len(recent) if recent else 0
            if r_ and r_.get("starter") and srate < 0.5:
                st = [r["min"] for r in rows if r["start"]][-10:]
                target = sum(st) / len(st) if len(st) >= 3 else m + 4
                bump = max(0.0, min(6.0, (target - m) * 0.6))
                if bump >= 0.5:
                    m += bump; why.append(f"+{bump:.1f} min starting tonight")
            elif r_ and r_.get("starter") is False and srate > 0.7 and r_.get("status"):
                m -= 4; why.append("-4.0 min coming off the bench")
            if f.get("b2b") and m >= 30:
                m -= 1; why.append("-1.0 min back-to-back")
            sp = abs(f.get("spread") or 0)
            if sp >= 10 and srate > 0.5:
                # this coach's own habit: how much his starters sit in blowouts compared with the league
                k_team = max(0.4, min(1.8, rp["drop"] / lg["drop"])) if lg["drop"] > 0 else 1.0
                cut = min(4.0, (sp - 8) * 0.3 * k_team)
                m -= cut; why.append(f"-{cut:.1f} min blowout risk" + (f" ({t} starters sit {rp['drop']:.1f} min in blowouts vs {lg['drop']:.1f} league)" if abs(k_team - 1) >= 0.15 else ""))
            elif f.get("spread") is not None and sp <= 3.5 and srate > 0.5 and rp["bump"] - lg["bump"] >= 0.8:
                # close game expected and this coach leans on his starters more than most when it's tight
                add = min(2.0, (rp["bump"] - lg["bump"]) * (1 - sp / 7))
                if add >= 0.4:
                    m += add; why.append(f"+{add:.1f} min close game ({t} starters play +{rp['bump']:.1f} in close games)")
            is_st = bool(srate > 0.5 or (r_ and r_.get("starter")))
            md = (TUNE.get("min_delta") or {}).get("starter" if is_st else "bench", 0)
            if abs(md) >= 0.3:
                m += md; why.append(f"{md:+.1f} min learned correction")
            out[pid] = [max(0.0, min(42.0, m)), why]
            meta[pid] = {"st": is_st, "pp": _play_prob(model, pid, t, by_tm[t], played, r_, official_ok and official_ok.get(pid))}
        _fit_240(t, [p for p in active if p in out], out, meta)
        for p in active:
            if p in out:
                out[p].append(round(min(1.0, max(0.0, meta[p]["pp"])), 2))
    return out


PP_W = (0.35, 0.25, 0.18, 0.12, 0.10)            # last five team games, most recent first


def _play_prob(model, pid, team, team_dates_, played, rw_row, cleared):
    """How likely he is to play tonight, from the team's last five games: a healthy player the coach has stopped
    using keeps a full projection otherwise (his average only counts games he played)."""
    if rw_row and rw_row.get("starter"):
        return 1.0
    if cleared:                                   # on tonight's official report as available or probable: he is back
        return 0.95
    cur = [d for d in team_dates_ if d >= f"{model.cur - 1}-10-01"]
    mine = [r["d"] for r in model.by_player[pid] if r["team"] == team and r["season"] == model.cur]
    if len(cur) < 3:
        return 1.0
    if not mine:
        return 0.0                                # hasn't played for this team this season
    since = [d for d in cur if d >= mine[0]][-5:][::-1]
    if len(since) < 2:
        return 1.0
    w = PP_W[:len(since)]
    return sum(wi for wi, d in zip(w, since) if pid in played[(team, d)]) / sum(w)


def _fit_240(team, pids, out, meta):
    """A team has 240 minutes. Players the coach isn't using drop out; if the rest add up to more than 240 the
    bench gives the extra back in proportion (a returning starter takes minutes from the players who covered for
    him); if they fall well short, everyone grows a little."""
    for pid in pids:
        pp = meta[pid]["pp"]
        if pp < 0.3 and not meta[pid]["st"]:
            out[pid][1].append("out of the rotation lately" if pp > 0 else "hasn't played this season")
            out[pid][0] = 0.0
        elif pp < 0.75:
            out[pid][1].append(f"played {pp * 100:.0f}% of recent games (recent ones count more)")
    live = [p for p in pids if out[p][0] > 0]
    exp = lambda p: out[p][0] * min(1.0, max(0.3, meta[p]["pp"]))      # minutes the team can count on
    tot = sum(exp(p) for p in live)
    if tot > 240.5:
        extra = tot - 240
        for group, cap in (([p for p in live if not meta[p]["st"]], 0.45), ([p for p in live if meta[p]["st"]], 0.25)):
            g_tot = sum(exp(p) for p in group)
            if extra <= 0.05 or g_tot <= 0:
                continue
            k = min(cap, extra / g_tot)
            for p in group:
                cut = out[p][0] * k
                if cut >= 0.3:
                    out[p][1].append(f"-{cut:.1f} min: the team's projections added up to {tot:.0f} of 240")
                out[p][0] -= cut
            extra -= g_tot * k
    elif tot < 226:
        need = 232 - tot                                               # leave a few minutes for the end of the bench
        room = {p: (38 if meta[p]["st"] else 32) - out[p][0] for p in live}
        r_tot = sum(max(0.0, v) for v in room.values()) or 1
        for p in live:
            add = min(max(0.0, room[p]), need * max(0.0, room[p]) / r_tot)
            if add >= 0.3:
                out[p][1].append(f"+{add:.1f} min: the team's projections only added up to {tot:.0f} of 240")
            out[p][0] += add

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
    on_team = defaultdict(set)
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
            on_team[p[nd.P_ID]].add((t, s))
            o = ab[1] if t == ab[0] else ab[0]
            if s == ctx["season"]:
                team_min[t][p[nd.P_ID]] += p[nd.P_MIN]
            if p[nd.P_ID] in want:
                logs[p[nd.P_ID]].append({"gid": gid, "d": g["d"], "opp": o, "home": 1 if g["teams"][t]["home"] else 0,
                                         "min": round(p[nd.P_MIN]), "pts": p[nd.P_PTS], "reb": p[nd.P_REB],
                                         "ast": p[nd.P_AST], "tpm": p[nd.P_TPM], "tpa": p[nd.P_TPA], "fga": p[nd.P_FGA],
                                         "fta": p[nd.P_FTA], "tov": p[nd.P_TOV],
                                         "start": p[nd.P_START], "s": s, "ts": g["teams"][t]["score"],
                                         "os": g["teams"][o]["score"], "team": t})
    flags = ctx.get("flags") or slate_flags(ctx, team_dates(model))[0]
    bumps, opps = usage_bumps(ctx, logs, played, on_team)
    ctx["opps"] = opps
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
                      r["fga"], r["start"], mask, r["s"], r["ts"], r["os"], r["team"], r["fta"], r["tov"]])
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
                    "minutes": e["minutes"], "min_base": e.get("min_base"), "min_why": e.get("min_why", []),
                    "pplay": e.get("pplay", 1.0), "pbp": e.get("pbp"), "onoff": e.get("onoff"),
                    "status": e["status"], "out": e["out"],
                    "rw": ({k: e["rw"].get(k) for k in ("starter", "pos", "status", "inj", "pct", "url", "note")} if e["rw"] else None),
                    "espn": f"https://www.espn.com/nba/player/_/id/{pid}",
                    "mates": [{"pid": m, "name": model.players[m]["name"] if m in model.players else m} for m in mate_ids],
                    "logs": L, "proj": proj, "dvp": dvp, "comps": comps(model, pid, opp)}
        bump = dict(bumps.get(pid, {}))
        for s_, v in (e.get("onoff") or {}).items():      # lineup data (possessions with him off the floor) beats whole-game splits
            if v["k"] - 1 >= 0.10 and v["poss"] >= 150 and (s_ not in bump or v["k"] - 1 > bump[s_]["delta"]):
                bump[s_] = {"delta": round(max(0.12, v["k"] - 1), 3), "out": v["out"], "n": v["poss"], "src": "lineups"}
        fl = {**flags.get(team, {}), "status": e["status"], "minutes": e["minutes"],
              "starter": (e["rw"] or {}).get("starter"), "bump": bump}
        cv = {}
        for s_, pr in e["proj"].items():
            pts, why, codes = cvm.base(pr, s_, fl, TUNE.get("factor_w"))
            n, _ = cvm.final(pts, why)
            cv[s_] = [pts, " · ".join(why), n, ",".join(codes)]
        out[pid]["cv"] = cv
        out[pid]["flags"] = {k: fl.get(k) for k in ("b2b", "three_in_four", "spread", "vegas_diff", "rest_days")}
    ctx["cv"] = {pid: v["cv"] for pid, v in out.items()}
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
        # game by game, newest first: date, minutes, and per stat [actual, what he normally does in those minutes]
        games = [[r["d"], round(r["min"]), {s: [round(r[s], 1), round(model.rate(p2, s, r["season"]) * r["min"], 1)]
                                              for s in ALL_STATS}]
                 for r in sorted(rs, key=lambda r: r["d"], reverse=True)[:8]]
        out.append({"pid": p2, "name": model.players[p2]["name"], "team": model.players[p2]["team"], "role": model.players[p2]["role"],
                    "sim": round(sm, 2), "n": len(rs), "min": round(mins / len(rs), 1), "stats": st, "games": games})
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


# ------------------------------------------------------------------ team rates for the simulator
_RATES = {}


def team_rates(season):
    """Per team, this season and last (last season counts less, recent games more): offensive rebound rate,
    turnovers per possession and free throws per shot, on offense and allowed on defense. Shrunk toward the
    league when a team has few games."""
    if season in _RATES:
        return _RATES[season]
    acc = defaultdict(lambda: defaultdict(float))
    games = sorted(nd.all_games([season - 1, season]), key=lambda x: x[2]["d"])
    n_all = len(games)
    for i, (s, gid, g) in enumerate(games):
        if g.get("st", 2) not in (2, 3) or len(g["teams"]) != 2 or len(g.get("tstats") or {}) != 2:
            continue
        w = (1.0 if s == season else 0.45) * 0.995 ** (n_all - 1 - i)
        ab = list(g["teams"])
        reb = {t: sum(p[nd.P_REB] for p in g["players"] if p[nd.P_TEAM] == t) for t in ab}
        for t in ab:
            o = ab[1] if t == ab[0] else ab[0]
            T, O = g["tstats"][t], g["tstats"][o]
            if not T.get("fga") or not O.get("fga"):
                continue
            o_dreb = max(0.0, reb[o] - O["oreb"])
            t_dreb = max(0.0, reb[t] - T["oreb"])
            poss_t = T["fga"] + 0.44 * T["fta"] - T["oreb"] + T["tov"]
            poss_o = O["fga"] + 0.44 * O["fta"] - O["oreb"] + O["tov"]
            a = acc[t]
            a["w"] += w
            a["orb"] += w * T["oreb"] / max(1.0, T["oreb"] + o_dreb)          # our offensive rebound rate
            a["orb_a"] += w * O["oreb"] / max(1.0, O["oreb"] + t_dreb)        # opponents' rate against us
            a["tov"] += w * T["tov"] / max(1.0, poss_t)
            a["tov_f"] += w * O["tov"] / max(1.0, poss_o)                       # turnovers we force
            a["ftr"] += w * T["fta"] / T["fga"]
            a["ftr_a"] += w * O["fta"] / O["fga"]
    keys = ("orb", "orb_a", "tov", "tov_f", "ftr", "ftr_a")
    W = sum(a["w"] for a in acc.values()) or 1
    lg = {k: sum(a[k] for a in acc.values()) / W for k in keys}
    out = {"_league": {k: round(v, 4) for k, v in lg.items()}}
    for t, a in acc.items():
        k0 = 6.0                                                      # games of league prior
        out[t] = {k: round((a[k] + k0 * lg[k]) / (a["w"] + k0), 4) for k in keys}
    _RATES[season] = out
    return out


def _safe_rates(ctx, g):
    try:
        return matchup_rates(ctx.get("season") or nd.season_of(ctx["day"]), g["home"], g["away"])
    except Exception as e:
        print(f"[research] team rates failed: {e}")
        return None


def matchup_rates(season, home, away):
    """What the sim uses for each side: offense vs the opponent's defense, relative to the league (log5-style)."""
    R = team_rates(season)
    lg = R["_league"]
    if not lg:
        return None
    def side(t, o):
        a, d = R.get(t), R.get(o)
        if not a or not d:
            return None
        return {"orb": round(a["orb"] * d["orb_a"] / lg["orb"], 4), "tov": round(a["tov"] * d["tov_f"] / lg["tov"], 4),
                "ftk": round((a["ftr"] * d["ftr_a"] / lg["ftr"]) / a["ftr"], 4)}
    h, w = side(home, away), side(away, home)
    return {"home": h, "away": w, "league": lg} if h and w else None


# ------------------------------------------------------------------ games for the slate
def game_cards(ctx):
    out = []
    for g in ctx["games"]:
        card = (ctx.get("slate_cards") or {}).get(g["espn_id"]) or {}
        tp = team_projection(ctx["model"], g["home"], g["away"], (card.get("spots") or {}).get("net_home") or 0.0)
        out.append({**{k: g[k] for k in ("espn_id", "start", "state", "home", "away", "home_name", "away_name")},
                    "model": tp, "vegas": (ctx.get("slate_cards") or {}).get(g["espn_id"]),
                    "spots": card.get("spots"), "refs": card.get("refs"),
                    "rates": _safe_rates(ctx, g)})
    return out


def _index_row(p):
    """The light version of a player for lists: no logs, no signals."""
    return {"pid": p["pid"], "name": p["name"], "team": p["team"], "opp": p["opp"], "home": p["home"], "game": p["game"],
            "start": p["start"], "state": p["state"], "role": p["role"], "minutes": p["minutes"], "status": p["status"],
            "out": p["out"], "starter": (p["rw"] or {}).get("starter"), "lineup": (p["rw"] or {}).get("status"),
            "proj": {s: [v["proj"], v["base"], v["mult"], v.get("sd")] for s, v in p["proj"].items()},
            "dvp": {s: v[1] for s, v in p["dvp"].items()},
            "cv": {s: v[:2] for s, v in (p.get("cv") or {}).items()},
            "st": 1 if (p["rw"] or {}).get("starter") else 0,
            "b2b": (p.get("flags") or {}).get("b2b") or None, "pplay": p.get("pplay", 1.0),
            "onoff": ({s: v["k"] for s, v in p["onoff"].items()} if p.get("onoff") else None)}


def write_all(ctx, log=print):
    players = build_players(ctx)
    shots = build_shots(ctx, players.keys())
    stamp = {"updated": store.iso(store.now()), "date": ctx["day"].isoformat()}
    cards = game_cards(ctx)
    # light index for lists, one detail file per game for player cards (keeps phone downloads small)
    store.save("players.json", {**stamp, "games": cards, "players": {k: _index_row(v) for k, v in players.items()},
                                "opps": ctx.get("opps", [])[:40], "injury_report": ctx.get("inj_report"),
                                "tuning": {k: TUNE.get(k) for k in ("sim_anchor", "stat_factor", "min_delta", "factor_w", "updated")},
                                "stat_names": {s: nm.STATS[s] for s in ALL_STATS}}, compact=True)
    # what the injury watch compares against between refreshes (ESPN + the official report, not RotoWire)
    store.save("inj_seen.json", {"date": ctx["day"].isoformat(), "at": stamp["updated"],
                                 "s": {pid: (ctx.get("inj") or {}).get(pid, "") for pid in players}}, compact=True)
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
    # DraftKings opening/closing lines for games and every projected prop (free, ESPN's odds feed)
    try:
        from . import dk
        dk.refresh(ctx, players, log=log)
        dk.recent_index(players)
    except Exception as e:
        log(f"[research] DraftKings lines failed: {e}")
    # conviction track record: snapshot tonight's leans (graded after the games)
    try:
        from . import track
        tdb = track.load()
        track.snapshot(tdb, ctx["day"].isoformat(), players)
        store.save(track.FILE, tdb, compact=True)
    except Exception as e:
        log(f"[research] track snapshot failed: {e}")
    # phone alerts: big usage bumps when a rotation player is out
    try:
        from . import alerts
        items = []
        for o in ctx.get("opps", []):
            gain = o["without"] - o["with"]
            if o["delta"] >= 0.20 and o["min_without"] >= 22 and gain >= (1.0 if o["stat"] == "tpm" else 3.0 if o["stat"] in ("pts", "pra") else 2.0):
                items.append((f"opp|{ctx['day']}|{o['out_pid']}|{o['pid']}|{o['stat']}",
                              f"{o['out']} ({o['team']}) is {o['status'] or 'out'}: {o['name']} averages {o['without']:g} "
                              f"{cvm.STATNAME[o['stat']]} without him vs {o['with']:g} with ({o['delta'] * 100:+.0f}%, {o['n']} games)"))
        alerts.send(items[:6], "Injury opportunity")
    except Exception as e:
        log(f"[research] alerts failed: {e}")
    log(f"[research] {ctx['day']}: {len(ctx['games'])} games, {len(players)} players, "
        f"{sum(len(v) for v in shots.values())} shots, lineups for {len(ctx['rw_games'])} games")
    return players


# ------------------------------------------------------------------ injury watch (between refreshes)
def injury_watch(log=print):
    """Re-read ESPN's injury list and the league's official report. Returns the rotation players (12+ projected
    minutes or a listed starter, in games that haven't tipped) whose out/in status changed since the last
    refresh, as [(pid, name, team, old, new, minutes)]. Cheap: no box scores, no model."""
    pj = store.load("players.json", None)
    seen = store.load("inj_seen.json", None)
    if not pj or not seen or seen.get("date") != pj.get("date") or pj.get("date") != et_today().isoformat():
        return []
    try:
        states = {g["espn_id"]: g["state"] for g in slate_all(et_today())}
    except Exception:
        states = {}
    idx = pj.get("players") or {}
    inj = nd.injuries()
    try:
        from . import injury_report
        official, _ = injury_report.fetch({pid: {"name": p["name"]} for pid, p in idx.items()}, norm)
        inj.update(official)
    except Exception as e:
        log(f"[injwatch] official report failed: {e}")
    changes = []
    for pid, p in idx.items():
        if states.get(p["game"], p.get("state")) != "pre":
            continue
        if (p.get("minutes") or 0) < 12 and not p.get("starter"):
            continue
        old, new = seen["s"].get(pid, ""), inj.get(pid, "")
        if _status_out(old) != _status_out(new):
            changes.append((pid, p["name"], p["team"], old or "available", new or "available", p.get("minutes") or 0))
    return changes
