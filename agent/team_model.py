"""Team model, phase 0: opponent-adjusted team ratings, luck-adjusted for three-point shooting, and a
walk-forward backtest against ESPN's pregame (DraftKings) line. Free data only (stored ESPN box scores).

Every team-game becomes two rows (each side's offense vs the other's defense). Ratings are solved for all
teams at once with ridge regression (coordinate descent), with recent games weighted more, so a team is not
flattered by a soft schedule. Points are luck-adjusted: the threes a team took are re-scored at its own
long-run three-point percentage, so a defense is judged on the threes it allowed, not on how many fell.

Output: docs/data/team_model.json (ratings, four factors, model parameters, backtest). The Matchup Builder in
the Games tab reads it and prices any game. Tracked only: the agent does not bet game lines from it."""
import json
import math
from collections import defaultdict
from datetime import date, timedelta

from . import config
from . import nba_data as nd

OUT = config.ROOT / "docs" / "data" / "team_model.json"
HALF_LIFE = 60.0        # days; recency weight halves every HALF_LIFE days (tune(), 2025-26 walk-forward: 30/60/120 within 0.1 pt)
PRIOR_SEASON_W = 0.6    # last season's games count this much on top of the recency weight
WINDOW = 420            # days of games used in a fit
LAM = 6.0               # ridge: games' worth of weight pulling each rating to the league average
FT_PCT = 0.78           # league free-throw percentage (box scores store attempts only)
LUCK = 0.5              # share of three-point luck removed (tune(): 0.5 best on margin and win log loss, small gain)
K3 = 600.0              # three-point attempts of league prior when estimating a team's true 3P%
SD_MARGIN, SD_TOTAL = 12.5, 18.0     # defaults, replaced by backtest residuals when available
FACTORS = ("efg", "tov", "orb", "ftr", "tpar", "tpp")


# ------------------------------------------------------------------ rows from stored box scores
def rows_from_games(games):
    """games: [(season, gid, g)] sorted by date. Returns per team-game rows (raw, before luck adjustment)."""
    out = []
    for s, gid, g in games:
        if g.get("st", 2) not in (2, 3) or len(g.get("teams") or {}) != 2 or len(g.get("tstats") or {}) != 2:
            continue
        ab = list(g["teams"])
        agg = {t: {"reb": 0.0, "tpm": 0.0, "tpa": 0.0, "fga": 0.0} for t in ab}
        for p in g.get("players") or []:
            a = agg.get(p[nd.P_TEAM])
            if a is not None:
                a["reb"] += p[nd.P_REB]; a["tpm"] += p[nd.P_TPM]; a["tpa"] += p[nd.P_TPA]; a["fga"] += p[nd.P_FGA]
        T = g["tstats"]
        if not all(T[t].get("fga") for t in ab):
            continue
        poss = {t: T[t]["fga"] + 0.44 * T[t]["fta"] - T[t]["oreb"] + T[t]["tov"] for t in ab}
        pg = (poss[ab[0]] + poss[ab[1]]) / 2
        if pg < 70:
            continue
        for t in ab:
            o = ab[1] if t == ab[0] else ab[0]
            pts = g["teams"][t].get("score") or 0
            fga, fta = T[t]["fga"], T[t]["fta"]
            ftm = FT_PCT * fta
            o_dreb = max(0.0, agg[o]["reb"] - T[o]["oreb"])
            out.append({"d": g["d"], "s": s, "gid": gid, "t": t, "o": o, "home": bool(g["teams"][t].get("home")),
                        "pts": pts, "poss": pg, "tpm": agg[t]["tpm"], "tpa": agg[t]["tpa"],
                        "efg": (pts - ftm) / (2 * fga), "tov": T[t]["tov"] / pg,
                        "orb": T[t]["oreb"] / max(1.0, T[t]["oreb"] + o_dreb), "ftr": fta / fga,
                        "tpar": agg[t]["tpa"] / fga, "tpp": agg[t]["tpm"] / agg[t]["tpa"] if agg[t]["tpa"] else 0.35})
    _rest(out)
    return out


def _rest(rows):
    last = {}
    for r in sorted(rows, key=lambda r: r["d"]):
        d = date.fromisoformat(r["d"])
        p = last.get(r["t"])
        r["b2b"] = 1.0 if p is not None and (d - p).days == 1 else 0.0
        last[r["t"]] = d
    b = {(r["gid"], r["t"]): r["b2b"] for r in rows}
    for r in rows:
        r["ob2b"] = b.get((r["gid"], r["o"]), 0.0)


# ------------------------------------------------------------------ ridge solver
def _weights(rows, day, season):
    ws = []
    for r in rows:
        age = (day - date.fromisoformat(r["d"])).days
        w = 0.5 ** (age / HALF_LIFE) * (1.0 if r["s"] == season else PRIOR_SEASON_W)
        ws.append(w)
    return ws


def solve(rows, ws, key, extras=(), lam=LAM, sweeps=25, init=None):
    """y = mu + O[t] + D[o] + sum(beta_k * x_k). Weighted ridge on O and D; mu and betas unpenalized."""
    st = init or {}
    O = defaultdict(float, st.get("O", {})); D = defaultdict(float, st.get("D", {}))
    beta = dict(st.get("beta", {k: 0.0 for k in extras}))
    W = sum(ws) or 1.0
    mu = st.get("mu", sum(w * r[key] for r, w in zip(rows, ws)) / W)
    by_t, by_o = defaultdict(list), defaultdict(list)
    for i, r in enumerate(rows):
        by_t[r["t"]].append(i); by_o[r["o"]].append(i)

    def res(i, skip=None):
        r = rows[i]
        v = r[key] - mu - O[r["t"]] - D[r["o"]] - sum(beta[k] * r[k] for k in extras)
        return v
    for _ in range(sweeps):
        mu += sum(ws[i] * res(i) for i in range(len(rows))) / W
        for k in extras:
            num = sum(ws[i] * rows[i][k] * res(i) for i in range(len(rows)))
            den = sum(ws[i] * rows[i][k] ** 2 for i in range(len(rows)))
            if den > 1e-9:
                beta[k] += num / den
        for t, idx in by_t.items():
            num = sum(ws[i] * res(i) for i in idx); den = sum(ws[i] for i in idx) + lam
            O[t] += num / den - O[t] * lam / den
        for t, idx in by_o.items():
            num = sum(ws[i] * res(i) for i in idx); den = sum(ws[i] for i in idx) + lam
            D[t] += num / den - D[t] * lam / den
    return {"mu": mu, "O": dict(O), "D": dict(D), "beta": beta}


def _luck_adjust(rows, ws):
    """Re-score each team's threes at its own long-run 3P% (shrunk to the league), then points per 100."""
    lg = sum(w * r["tpm"] for r, w in zip(rows, ws)) / max(1e-9, sum(w * r["tpa"] for r, w in zip(rows, ws)))
    m, a = defaultdict(float), defaultdict(float)
    for r, w in zip(rows, ws):
        m[r["t"]] += w * r["tpm"]; a[r["t"]] += w * r["tpa"]
    # weights are decayed, so convert to "attempts" scale with the team's average weight per row
    p3 = {}
    for t in a:
        n = sum(1 for r in rows if r["t"] == t) or 1
        wsum = sum(w for r, w in zip(rows, ws) if r["t"] == t) or 1
        att = a[t] * n / wsum                                       # effective attempts
        made = m[t] * n / wsum
        p3[t] = (made + K3 * lg) / (att + K3)
    venue = {}
    for hv in (True, False):                                        # home teams shoot threes a bit better: keep that
        mm = sum(w * r["tpm"] for r, w in zip(rows, ws) if r["home"] == hv)
        aa = sum(w * r["tpa"] for r, w in zip(rows, ws) if r["home"] == hv)
        venue[hv] = (mm / aa - lg) if aa else 0.0
    for r in rows:
        exp = p3.get(r["t"], lg) + venue[r["home"]]
        adj = r["pts"] - LUCK * 3 * (r["tpm"] - r["tpa"] * exp)
        r["ppp"] = 100 * r["pts"] / r["poss"]
        r["ppp_adj"] = 100 * adj / r["poss"]
        r["home_s"] = 0.5 if r["home"] else -0.5
    return p3, lg


def fit(rows, day, season, init=None, factors=False):
    """Fit on rows strictly before `day`. Returns ratings and parameters."""
    rows = [r for r in rows if r["d"] < day.isoformat() and (day - date.fromisoformat(r["d"])).days <= WINDOW]
    if len(rows) < 200:
        return None
    ws = _weights(rows, day, season)
    p3, lg3 = _luck_adjust(rows, ws)
    init = init or {}
    eff = solve(rows, ws, "ppp_adj", ("home_s", "b2b", "ob2b"), init=init.get("eff"))
    pace = solve(rows, ws, "poss", ("b2b", "ob2b"), init=init.get("pace"))
    out = {"eff": eff, "pace": pace, "p3": p3, "lg3": lg3, "n": len(rows)}
    if factors:
        out["ff"] = {k: solve(rows, ws, k) for k in FACTORS}
        out["raw"] = _raw(rows, ws)
        out["recent"] = _recent(rows, day)
    return out


def _raw(rows, ws):
    acc = defaultdict(lambda: defaultdict(float))
    for r, w in zip(rows, ws):
        a, b = acc[r["t"]], acc[r["o"]]
        a["w"] += w; a["off"] += w * r["ppp"]; b["dw"] += w; b["def"] += w * r["ppp"]
        b["o3m"] += w * r["tpm"]; b["o3a"] += w * r["tpa"]
    return {t: {"ortg": a["off"] / a["w"], "drtg": a["def"] / a["dw"],
                "opp3": a["o3m"] / a["o3a"] if a["o3a"] else None} for t, a in acc.items() if a["w"] and a["dw"]}


def _recent(rows, day, n=10):
    by = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["d"]):
        by[r["t"]].append(r)
    opp = {(r["gid"], r["t"]): r for r in rows}
    out = {}
    for t, rs in by.items():
        last = rs[-n:]
        net = [r["ppp"] - opp[(r["gid"], r["o"])]["ppp"] for r in last if (r["gid"], r["o"]) in opp]
        out[t] = {"net10": round(sum(net) / len(net), 1) if net else None, "n": len(net)}
    return out


# ------------------------------------------------------------------ prediction
def predict(m, home, away, b2b_home=0.0, b2b_away=0.0):
    e, p = m["eff"], m["pace"]
    if home not in e["O"] or away not in e["O"]:
        return None
    be, bp = e["beta"], p["beta"]
    pace = (2 * p["mu"] + p["O"].get(home, 0) + p["D"].get(away, 0) + p["O"].get(away, 0) + p["D"].get(home, 0)) / 2 \
        + (bp["b2b"] + bp["ob2b"]) * (b2b_home + b2b_away) / 2
    hp = e["mu"] + e["O"][home] + e["D"].get(away, 0) + be["home_s"] * 0.5 + be["b2b"] * b2b_home + be["ob2b"] * b2b_away
    ap = e["mu"] + e["O"][away] + e["D"].get(home, 0) - be["home_s"] * 0.5 + be["b2b"] * b2b_away + be["ob2b"] * b2b_home
    h, a = pace * hp / 100, pace * ap / 100
    return {"home": h, "away": a, "margin": h - a, "total": h + a, "pace": pace}


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _devig(mh, ma):
    def p(o):
        return 100 / (o + 100) if o > 0 else -o / (-o + 100)
    if not mh or not ma:
        return None
    a, b = p(mh), p(ma)
    return a / (a + b)


# ------------------------------------------------------------------ walk-forward backtest
def backtest(rows, seasons, log=print):
    """Refit before each game day; compare to ESPN's pregame line. Returns per-game records."""
    games = {}
    for r in rows:
        if r["home"]:
            games.setdefault(r["gid"], r)
    lines = {}
    for s, gid, g in nd.all_games(seasons):
        if g.get("line") and g["line"].get("spread") is not None and g["line"].get("total"):
            lines[gid] = g["line"]
    by_day = defaultdict(list)
    for gid, r in games.items():
        if r["s"] in seasons and gid in lines:
            by_day[r["d"]].append(r)
    recs, init = [], None
    days = sorted(by_day)
    for i, d in enumerate(days):
        day = date.fromisoformat(d)
        m = fit(rows, day, nd.season_of(day), init=init)
        if not m:
            continue
        init = {"eff": m["eff"], "pace": m["pace"]}
        opp = {(x["gid"], x["t"]): x for x in rows if x["d"] == d}
        for r in by_day[d]:
            a = opp.get((r["gid"], r["o"]))
            if not a:
                continue
            pr = predict(m, r["t"], r["o"], r["b2b"], r["ob2b"])
            if not pr:
                continue
            L = lines[r["gid"]]
            recs.append({"d": d, "s": r["s"], "gid": r["gid"], "home": r["t"], "away": r["o"],
                         "pm": pr["margin"], "pt": pr["total"], "am": r["pts"] - a["pts"], "at": r["pts"] + a["pts"],
                         "lm": -L["spread"], "lt": L["total"], "pml": _devig(L.get("ml_home"), L.get("ml_away"))})
        if i % 40 == 0:
            log(f"[team] backtest {d}: {len(recs)} games")
    return recs


def summarize(recs, sd_m=None, sd_t=None):
    if not recs:
        return None
    n = len(recs)
    mae = lambda xs: round(sum(abs(x) for x in xs) / len(xs), 2)
    out = {"n": n, "margin_mae": {"model": mae([r["pm"] - r["am"] for r in recs]), "line": mae([r["lm"] - r["am"] for r in recs])},
           "total_mae": {"model": mae([r["pt"] - r["at"] for r in recs]), "line": mae([r["lt"] - r["at"] for r in recs])}}
    # best blend weight on the model (0 = line only)
    for k, pm, lm, am in (("margin", "pm", "lm", "am"), ("total", "pt", "lt", "at")):
        best = min((sum((w * r[pm] + (1 - w) * r[lm] - r[am]) ** 2 for r in recs), w) for w in [i / 20 for i in range(21)])
        out[k + "_best_w"] = best[1]
    # against the spread / total, by how far the model disagrees with the line
    def rec(kind, cut):
        w = l = 0
        for r in recs:
            diff = (r["pm"] - r["lm"]) if kind == "ats" else (r["pt"] - r["lt"])
            res = (r["am"] - r["lm"]) if kind == "ats" else (r["at"] - r["lt"])
            if abs(diff) < cut or res == 0:
                continue
            if (diff > 0) == (res > 0):
                w += 1
            else:
                l += 1
        return {"w": w, "l": l, "pct": round(w / (w + l), 3) if w + l else None}
    out["ats"] = {str(c): rec("ats", c) for c in (0, 2, 4, 6)}
    out["ou"] = {str(c): rec("ou", c) for c in (0, 3, 6, 9)}
    # win probability log loss vs the moneyline
    sd = sd_m or SD_MARGIN
    ll_m = ll_l = 0.0; k = 0
    for r in recs:
        if r["pml"] is None or r["am"] == 0:
            continue
        y = 1 if r["am"] > 0 else 0
        p = min(0.99, max(0.01, _phi(r["pm"] / sd)))
        q = min(0.99, max(0.01, r["pml"]))
        ll_m -= math.log(p if y else 1 - p); ll_l -= math.log(q if y else 1 - q); k += 1
    if k:
        out["win_ll"] = {"model": round(ll_m / k, 4), "line": round(ll_l / k, 4), "n": k}
    out["sd_margin"] = round(math.sqrt(sum((r["pm"] - r["am"]) ** 2 for r in recs) / n), 2)
    out["sd_total"] = round(math.sqrt(sum((r["pt"] - r["at"]) ** 2 for r in recs) / n), 2)
    return out


# ------------------------------------------------------------------ Matchup Builder extras: splits and shot zones
ZONES = ("rim", "paint", "mid", "corner3", "above3")


def _zone(x, y, three):
    if three:
        return "corner3" if y <= 9 else "above3"
    dist = math.hypot(x - 25, y)
    return "rim" if dist <= 4 else "paint" if dist <= 14 else "mid"


def splits(rows, season):
    """Raw four factors for the filters: last 5, last 10, home only, road only (this season)."""
    keys = FACTORS + ("ppp", "poss")
    opp = {(r["gid"], r["t"]): r for r in rows}
    by = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["d"]):
        if r["s"] == season and (r["gid"], r["o"]) in opp:
            by[r["t"]].append(r)
    def avg(rs):
        if not rs:
            return None
        o = {k: round(sum(r[k] for r in rs) / len(rs), 4) for k in keys}
        d = {k: round(sum(opp[(r["gid"], r["o"])][k] for r in rs) / len(rs), 4) for k in keys}
        return {"n": len(rs), "off": o, "def": d}
    return {t: {"l5": avg(rs[-5:]), "l10": avg(rs[-10:]), "home": avg([r for r in rs if r["home"]]),
                "road": avg([r for r in rs if not r["home"]])} for t, rs in by.items()}


def zones(season):
    """Share of shots and FG% by zone, on offense and allowed on defense (current season's shot charts)."""
    sdb = nd.load_shots(season)
    db = nd.load(season)["games"]
    acc = defaultdict(lambda: {"off": defaultdict(lambda: [0, 0]), "def": defaultdict(lambda: [0, 0])})
    for gid, sh in sdb["games"].items():
        g = db.get(gid)
        if not g or len(g.get("teams") or {}) != 2:
            continue
        team_of = {str(p[nd.P_ID]): p[nd.P_TEAM] for p in g.get("players") or []}
        ab = list(g["teams"])
        for pid, x, y, made, three, _ in sh:
            t = team_of.get(str(pid))
            if t not in ab:
                continue
            o = ab[1] if t == ab[0] else ab[0]
            z = _zone(x, y, three)
            for side, team in (("off", t), ("def", o)):
                c = acc[team][side][z]; c[0] += 1; c[1] += made
    out, lg = {}, defaultdict(lambda: [0, 0])
    for t, a in acc.items():
        out[t] = {}
        for side in ("off", "def"):
            n = sum(c[0] for c in a[side].values()) or 1
            out[t][side] = {z: [round(a[side][z][0] / n, 3), round(a[side][z][1] / a[side][z][0], 3) if a[side][z][0] else None]
                            for z in ZONES}
            if side == "off":
                for z in ZONES:
                    lg[z][0] += a[side][z][0]; lg[z][1] += a[side][z][1]
    n = sum(c[0] for c in lg.values()) or 1
    out["_league"] = {z: [round(lg[z][0] / n, 3), round(lg[z][1] / lg[z][0], 3) if lg[z][0] else None] for z in ZONES}
    return out


# ------------------------------------------------------------------ build the dashboard file
def _team_rows(m):
    e, p, ff, raw, rec = m["eff"], m["pace"], m.get("ff", {}), m.get("raw", {}), m.get("recent", {})
    teams = {}
    for t in e["O"]:
        if t not in e["D"]:
            continue
        o = e["mu"] + e["O"][t]; d = e["mu"] + e["D"][t]
        row = {"ortg": round(o, 1), "drtg": round(d, 1), "net": round(o - d, 1),
               "pace": round(p["mu"] + p["O"].get(t, 0) + p["D"].get(t, 0), 1),
               "p3": round(m["p3"].get(t, m["lg3"]), 3)}
        if t in raw:
            row["raw"] = {"ortg": round(raw[t]["ortg"], 1), "drtg": round(raw[t]["drtg"], 1),
                          "opp3": round(raw[t]["opp3"], 3) if raw[t]["opp3"] else None}
        row["off"] = {k: round(ff[k]["mu"] + ff[k]["O"].get(t, 0), 4) for k in ff}
        row["def"] = {k: round(ff[k]["mu"] + ff[k]["D"].get(t, 0), 4) for k in ff}
        row.update(rec.get(t, {}))
        teams[t] = row
    for k in ("net", "ortg", "pace"):
        for i, t in enumerate(sorted(teams, key=lambda t: -teams[t][k])):
            teams[t].setdefault("rank", {})[k] = i + 1
    for i, t in enumerate(sorted(teams, key=lambda t: teams[t]["drtg"])):
        teams[t]["rank"]["drtg"] = i + 1
    return teams


def build(today=None, with_backtest=False, log=print):
    from . import store
    today = today or store.now().astimezone(nd.ET).date()
    season = nd.season_of(today)
    seasons = [season - 2, season - 1, season]
    rows = rows_from_games(nd.all_games(seasons))
    m = fit(rows, today + timedelta(days=1), season, factors=True)
    if not m:
        log("[team] not enough games to fit")
        return None
    old = {}
    try:
        old = json.loads(OUT.read_text())
    except Exception:
        pass
    bt = old.get("backtest")
    if with_backtest or not bt:
        recs = backtest(rows, [season - 1, season], log=log)
        bt = {"all": summarize(recs), "by_season": {str(s): summarize([r for r in recs if r["s"] == s]) for s in (season - 1, season)},
              "updated": today.isoformat()}
    sd_m = (bt.get("all") or {}).get("sd_margin") or SD_MARGIN
    sd_t = (bt.get("all") or {}).get("sd_total") or SD_TOTAL
    e, p = m["eff"], m["pace"]
    out = {"updated": store.now().isoformat(timespec="seconds"), "date": today.isoformat(), "n_rows": m["n"],
           "params": {"mu": round(e["mu"], 3), "home": round(e["beta"]["home_s"], 3), "b2b_off": round(e["beta"]["b2b"], 3),
                      "b2b_def": round(e["beta"]["ob2b"], 3), "pace_mu": round(p["mu"], 2),
                      "pace_b2b": round(p["beta"]["b2b"] + p["beta"]["ob2b"], 3),
                      "sd_margin": sd_m, "sd_total": sd_t, "lg3": round(m["lg3"], 4), "half_life": HALF_LIFE},
           "eff": {"O": {t: round(v, 3) for t, v in e["O"].items()}, "D": {t: round(v, 3) for t, v in e["D"].items()}},
           "pace": {"O": {t: round(v, 3) for t, v in p["O"].items()}, "D": {t: round(v, 3) for t, v in p["D"].items()}},
           "league": {k: round(m["ff"][k]["mu"], 4) for k in FACTORS},
           "teams": _team_rows(m), "backtest": bt}
    try:
        out["splits"] = splits(rows, season)
    except Exception as ex:
        log(f"[team] splits failed: {ex}")
    try:
        out["zones"] = zones(season)
    except Exception as ex:
        log(f"[team] zones failed: {ex}")
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    log(f"[team] ratings for {len(out['teams'])} teams written")
    return out


def tune(hls=(30.0, 60.0, 120.0), lucks=(0.0, 0.5, 1.0), log=print):
    """One-off: pick HALF_LIFE and LUCK by last season's walk-forward error."""
    global HALF_LIFE, LUCK
    season = nd.season_of(date.today())
    rows = rows_from_games(nd.all_games([season - 2, season - 1, season]))
    res = {}
    for hl in hls:
        for lk in lucks:
            HALF_LIFE, LUCK = hl, lk
            s = summarize(backtest(rows, [season - 1], log=lambda m: None))
            res[(hl, lk)] = s
            log(f"hl {hl} luck {lk}: margin {s['margin_mae']} total {s['total_mae']} w {s['margin_best_w']}/{s['total_best_w']} "
                f"ats2 {s['ats']['2']} ou3 {s['ou']['3']} winll {s.get('win_ll')}")
    return res
