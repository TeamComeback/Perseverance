"""Conviction model: how often does the model's lean actually win, given how strong the conviction is?

Every graded priced prop (the odds scans and DraftKings' free day files) becomes one row for the side the model
leaned to. A small ridge logistic regression starts from the market's own no-vig probability for that side and
learns how much each of these moves it:
  - conviction tier (2, 3, 4+)        the matchup conviction score, 1-5
  - under                              unders have beaten their price across the board so far
  - L10 agrees                         his last-10 hit rate at this exact line points the same way as the lean
  - model edge                         matchup model probability minus the market's
Hit rates on their own do not beat the price (the line already moves with form); they only count as agreement.
Refit nightly; leave-one-day-out scores are stored so the dashboard can show whether it is earning its place.
Output goes into scoreboard.json under "conviction"; tools/sim_all.js applies it to tonight's lines."""
import math
from collections import defaultdict

from . import nba_data as nd

FEATS = ("b0", "cv2", "cv3", "cv4", "under", "agree", "edge")
RIDGE = 25.0            # rows of pull toward "the price is right" on every coefficient
MIN_ROWS = 400
VAL = {"pts": lambda p: p[nd.P_PTS], "reb": lambda p: p[nd.P_REB], "ast": lambda p: p[nd.P_AST], "tpm": lambda p: p[nd.P_TPM],
       "pra": lambda p: p[nd.P_PTS] + p[nd.P_REB] + p[nd.P_AST], "ra": lambda p: p[nd.P_REB] + p[nd.P_AST]}


def _logit(p):
    p = min(0.97, max(0.03, p))
    return math.log(p / (1 - p))


def _sig(x):
    return 1 / (1 + math.exp(-x))


def feats(cv, under, agree, edge):
    c = abs(cv or 0)
    return [1.0, 1.0 if c >= 2 else 0.0, 1.0 if c >= 3 else 0.0, 1.0 if c >= 4 else 0.0, 1.0 if under else 0.0,
            1.0 if agree else 0.0, max(-0.25, min(0.25, edge or 0.0))]


def rows(entries, season=None):
    """One row per graded prop, for the lean side: (date, base logit, x, y, stat, |cv|)."""
    from . import dk, store
    import json
    season = season or nd.season_of(store.now().date())
    logs = defaultdict(list)
    for s, gid, g in nd.all_games([season - 1, season]):
        for p in g.get("players") or []:
            if p[nd.P_MIN] >= 8:
                logs[str(p[nd.P_ID])].append((g["d"], p))
    out, seen = [], set()

    def add(pid, stat, date, line, po, pu, actual, pm, cv, lean):
        if actual is None or line is None or not po or not pu or stat not in VAL or actual == line or lean not in ("Over", "Under") or not cv:
            return
        L = [x for x in logs.get(str(pid), []) if x[0] < date][-10:]
        if len(L) < 5:
            return
        hr = sum(1 for x in L if VAL[stat](x[1]) > line) / len(L)
        pk = (1 / po) / (1 / po + 1 / pu)
        under = lean == "Under"
        pks = 1 - pk if under else pk
        pms = None if pm is None else (1 - pm if under else pm)
        agree = (hr < 0.5) if under else (hr > 0.5)
        y = 1 if (actual < line) == under else 0
        out.append((date, _logit(pks), feats(cv, under, agree, (pms - pks) if pms is not None else 0.0), y, stat, abs(cv)))
    for e in entries.values():
        seen.add((e["pid"], e["stat"], e["date"]))
        add(e["pid"], e["stat"], e["date"], e.get("line"), e.get("over"), e.get("under"), e.get("actual"), e.get("p_model"), e.get("cv"), e.get("lean"))
    root = dk.config.DATA / dk.DIR
    for f in sorted(root.glob("*.json")):
        d = json.loads(f.read_text())
        for r in (d.get("props") or {}).values():
            if (r["pid"], r["stat"], d["date"]) in seen or not r.get("c"):
                continue
            add(r["pid"], r["stat"], d["date"], r["c"][0], r["c"][1], r["c"][2], r.get("actual"), r.get("pm"), r.get("cv"), r.get("lean"))
    return out


def fit(R, ridge=RIDGE, iters=300, lr=0.5):
    k = len(FEATS)
    b = [0.0] * k
    n = len(R) or 1
    for _ in range(iters):
        g = [0.0] * k
        for _, base, x, y, _, _ in R:
            e = _sig(base + sum(bi * xi for bi, xi in zip(b, x))) - y
            for j in range(k):
                g[j] += e * x[j]
        for j in range(k):
            b[j] -= lr * (g[j] + ridge * b[j]) / n
    return b


def _ll(p, y):
    p = min(0.97, max(0.03, p))
    return -(math.log(p) if y else math.log(1 - p))


def build(entries, log=print):
    R = rows(entries)
    if len(R) < MIN_ROWS:
        return {"n": len(R), "ready": False}
    days = sorted({r[0] for r in R})
    # leave-one-day-out: is the fitted model better than the price alone on days it has not seen?
    llm = llk = 0.0
    picks = defaultdict(lambda: [0, 0])
    for d in days:
        b = fit([r for r in R if r[0] != d], iters=120)
        for r in R:
            if r[0] != d:
                continue
            p = _sig(r[1] + sum(bi * xi for bi, xi in zip(b, r[2])))
            llm += _ll(p, r[3]); llk += _ll(_sig(r[1]), r[3])
            tier = "70" if p >= 0.62 else "60" if p >= 0.57 else "55" if p >= 0.535 else "lo"
            picks[tier][0] += 1; picks[tier][1] += r[3]
    b = fit(R)
    by_cv = defaultdict(lambda: [0, 0])
    for r in R:
        by_cv[str(min(5, int(r[5])))][0] += 1; by_cv[str(min(5, int(r[5])))][1] += r[3]
    out = {"ready": True, "n": len(R), "days": len(days), "from": days[0], "to": days[-1],
           "coef": {k: round(v, 4) for k, v in zip(FEATS, b)},
           "cv_ll": {"model": round(llm / len(R), 4), "market": round(llk / len(R), 4)},
           "tiers": {k: {"n": v[0], "w": v[1]} for k, v in picks.items()},
           "by_cv": {k: {"n": v[0], "w": v[1]} for k, v in sorted(by_cv.items())},
           "unders": {"n": len(R), "r": round(sum(1 for r in R if (r[3] == 1) == bool(r[2][4])) / len(R), 3)},
           "under_rate": round(sum(1 for r in R if r[2][4]) and sum(r[3] for r in R if r[2][4]) / sum(1 for r in R if r[2][4]), 3)}
    log(f"[conviction] {out['n']} rows over {out['days']} days: {out['coef']} cv ll {out['cv_ll']} tiers {out['tiers']}")
    return out


# ====================================================================== line-side model (framework Nov 15, part A)
# The model above is trained on the MATCHUP side (lean = his projection up or down vs his own norm), but the case builder
# applies it to the LINE side (his projection vs DraftKings' line). On ~46% of props those differ, and the matchup dots
# get credited to a side they didn't point at. The line-side model is trained on the side it is applied to, and only
# counts the dots when the matchup agrees with that side ("split" when it doesn't).
# It stays off until compare() says it is better, night after night, and the owner switches it on (conv_switch.json).
FEATS_LINE = ("b0", "cv2a", "cv3a", "cv4a", "split", "under", "agree", "edge")
GATE = {"n": 3000, "days": 14, "ll_margin": 0.001, "streak": 3}


def feats_line(cv, under, agree, edge, ma):
    c = abs(cv or 0) if ma else 0
    return [1.0, 1.0 if c >= 2 else 0.0, 1.0 if c >= 3 else 0.0, 1.0 if c >= 4 else 0.0, 0.0 if ma else 1.0,
            1.0 if under else 0.0, 1.0 if agree else 0.0, max(-0.25, min(0.25, edge or 0.0))]


def rows_line(season=None):
    """One row per graded DraftKings prop for the LINE side (projection vs line), with both feature sets:
    (date, base logit, x_line, y, stat, |cv|, x_current_as_applied)."""
    from . import dk, store
    import json
    season = season or nd.season_of(store.now().date())
    logs = defaultdict(list)
    for s, gid, g in nd.all_games([season - 1, season]):
        for p in g.get("players") or []:
            if p[nd.P_MIN] >= 8:
                logs[str(p[nd.P_ID])].append((g["d"], p))
    out = []
    for f in sorted((dk.config.DATA / dk.DIR).glob("*.json")):
        d = json.loads(f.read_text())
        for r in (d.get("props") or {}).values():
            c, actual, proj, cv, mlean = r.get("c"), r.get("actual"), r.get("proj"), r.get("cv"), r.get("lean")
            if not c or c[0] is None or not c[1] or not c[2] or actual is None or proj is None or not cv or mlean not in ("Over", "Under"):
                continue
            line, stat = c[0], r["stat"]
            if actual == line or stat not in VAL or proj == line:
                continue
            L = [x for x in logs.get(str(r["pid"]), []) if x[0] < d["date"]][-10:]
            if len(L) < 5:
                continue
            under = proj < line
            hr = sum(1 for x in L if VAL[stat](x[1]) > line) / len(L)
            pk = (1 / c[1]) / (1 / c[1] + 1 / c[2])
            pks = 1 - pk if under else pk
            pm = r.get("pm")
            pms = None if pm is None else (1 - pm if under else pm)
            agree = (hr < 0.5) if under else (hr > 0.5)
            edge = (pms - pks) if pms is not None else 0.0
            ma = (mlean == "Under") == under
            y = 1 if (actual < line) == under else 0
            out.append((d["date"], _logit(pks), feats_line(cv, under, agree, edge, ma), y, stat, abs(cv), feats(cv, under, agree, edge)))
    return out


def _fit_k(R, xi, k, ridge=RIDGE, iters=150, lr=0.5):
    b = [0.0] * k
    n = len(R) or 1
    for _ in range(iters):
        g = [0.0] * k
        for r in R:
            x = r[xi]
            e = _sig(r[1] + sum(bj * xj for bj, xj in zip(b, x))) - r[3]
            for j in range(k):
                g[j] += e * x[j]
        for j in range(k):
            b[j] -= lr * (g[j] + ridge * b[j]) / n
    return b


def compare(entries, log=print):
    """Leave-one-day-out, on the props the conviction model is actually applied to (the line side):
      market  = DraftKings' no-vig price alone
      current = today's model (trained on matchup-side rows), applied the way the dashboard applies it
      line    = the line-side model
    Scored by log loss (lower is better) and by the win rate of the plays each one rates 57%+ (about Confident)."""
    R_m = rows(entries)
    R_l = rows_line()
    days = sorted({r[0] for r in R_l})
    tot = {"market": 0.0, "current": 0.0, "line": 0.0}
    top = {"current": [0, 0], "line": [0, 0]}
    by_day = []
    for d in days:
        test = [r for r in R_l if r[0] == d]
        bc = _fit_k([r for r in R_m if r[0] != d], 2, len(FEATS))
        bl = _fit_k([r for r in R_l if r[0] != d], 2, len(FEATS_LINE))
        dl = {"market": 0.0, "current": 0.0, "line": 0.0}
        for r in test:
            pc = _sig(r[1] + sum(b * x for b, x in zip(bc, r[6])))
            pl = _sig(r[1] + sum(b * x for b, x in zip(bl, r[2])))
            for k, p in (("market", _sig(r[1])), ("current", pc), ("line", pl)):
                v = _ll(p, r[3]); tot[k] += v; dl[k] += v
            for k, p in (("current", pc), ("line", pl)):
                if p >= 0.57:
                    top[k][0] += 1; top[k][1] += r[3]
        by_day.append({"d": d, "n": len(test), **{k: round(v / len(test), 4) for k, v in dl.items()}})
    n = len(R_l)
    if not n:
        return {"n": 0, "days": 0, "pass": False, "why": "no graded line-side rows yet"}
    ll = {k: round(v / n, 4) for k, v in tot.items()}
    days_better = sum(1 for x in by_day if x["line"] < x["current"])
    checks = [
        (n >= GATE["n"], f"{n:,} graded props (needs {GATE['n']:,})"),
        (len(days) >= GATE["days"], f"{len(days)} days (needs {GATE['days']})"),
        (ll["line"] <= ll["current"] - GATE["ll_margin"], f"log loss {ll['line']} vs current {ll['current']} (needs {GATE['ll_margin']} better)"),
        (ll["line"] < ll["market"], f"beats the market alone ({ll['market']})"),
    ]
    out = {"n": n, "days": len(days), "from": days[0], "to": days[-1], "ll": ll,
           "top": {k: {"n": v[0], "w": v[1]} for k, v in top.items()}, "days_better": days_better, "by_day": by_day[-21:],
           "coef_line": {k: round(v, 4) for k, v in zip(FEATS_LINE, _fit_k(R_l, 2, len(FEATS_LINE), iters=300))},
           "checks": [{"ok": bool(ok), "t": t} for ok, t in checks], "pass": all(ok for ok, _ in checks), "gate": GATE}
    log(f"[conviction] line-side test: {n} rows / {len(days)} days, ll {ll}, top {out['top']}, pass {out['pass']}")
    return out


def build_line(entries, log=print):
    """The line-side model in the same shape as build(), used only once the owner has switched it on."""
    R = rows_line()
    if len(R) < MIN_ROWS:
        return {"n": len(R), "ready": False, "mode": "line"}
    b = _fit_k(R, 2, len(FEATS_LINE), iters=300)
    days = sorted({r[0] for r in R})
    base = build(entries, log=lambda m: None)            # keeps the tier table and under rate the dashboard shows
    base.update({"mode": "line", "n": len(R), "days": len(days), "from": days[0], "to": days[-1],
                 "coef": {k: round(v, 4) for k, v in zip(FEATS_LINE, b)}})
    return base
