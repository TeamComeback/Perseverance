"""Calibration shadow (Nov 27 2026): the first piece of the self-improving loop.

Every night it looks at every graded live snapshot (the sim's chance of a bet, DraftKings' price at that moment, what
happened) and learns how much to trust the sim against the market's own no-vig number:

    calibrated chance = w * sim + (1 - w) * market      (w between 0 and 1, learned per market, shrunk to the overall w)

This is the same blend the prop scoreboard already does for props, now for live spread / total / moneyline bets.
SHADOW ONLY: nothing here changes a bet, a screen's verdict or a setting. It is graded two ways and shown in the live
tracker, so the owner can see whether the calibrated numbers would have done better before anything is promoted.
The honest part: every day's rows are scored with a weight learned from the OTHER days (leave-one-day-out), so the
shadow record can't flatter itself by having seen the answers.

Promotion rule (owner approves, never automatic): at least PROMOTE_GAMES games, out-of-sample log loss better than the
raw sim's, and the calibrated 5%+ edge bucket hitting at least as often as the raw one's.
Hard limits: w is bounded, one value per market, never touches ALLOWED_BOOKS, bylaws, stakes or the $5 minimum."""
import json
import math

from . import config, store

FILE = "calibration.json"
DIR = "dk"
GRID = [i / 20 for i in range(21)]
MARKETS = ("spread", "total", "ml")
PROMOTE_GAMES = 150           # graded games before the owner is asked to look at promoting it
MIN_FIT = 40                  # snapshots a market needs before it gets its own weight
PRIOR = 200                   # pseudo-snapshots pulling a market's weight toward the overall one
EDGE = 0.05                   # what counts as "a bet" in the shadow record: 5%+ expected value


def _ll(p, y):
    p = min(max(p, 0.02), 0.98)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _two(a, b):
    """No-vig chance of the first side from the two decimal prices."""
    if not a or not b:
        return None
    x, y = 1 / a, 1 / b
    return x / (x + y)


def rows(days=60):
    """One row per graded live snapshot per bet: (day, game, market, bet name, sim p, market p, price, won)."""
    root = config.DATA / DIR
    out = []
    if not root.exists():
        return out
    for f in sorted(root.glob("*.json"))[-days:]:
        try:
            db = json.loads(f.read_text())
        except Exception:
            continue
        for eid, G in (db.get("games") or {}).items():
            if not G.get("final"):
                continue
            for s in sorted(G.get("live", []), key=lambda x: x.get("ts") or ""):
                L, sim, res = s.get("line") or {}, s.get("sim") or {}, s.get("res") or {}
                if not res or res.get("stale"):
                    continue
                spec = (("spread", "spread_home", "sp_h", "sp_a", sim.get("p_home_cover")),
                        ("total", "over", "ov", "un", sim.get("p_over")),
                        ("ml", "ml_home", "ml_h", "ml_a", sim.get("p_home_win")))
                for mk, name, a, b, p in spec:
                    pa, pb = L.get(a), L.get(b)
                    if p is None or not pa or not pb:
                        continue
                    nv = _two(pa, pb)
                    for nm_, pr, mp, price in ((name, p, nv, pa), (_other(name), 1 - p, 1 - nv, pb)):
                        r = res.get(nm_)
                        if r in ("won", "lost"):
                            out.append((f.stem, eid, mk, nm_, pr, mp, price, 1 if r == "won" else 0))
    return out


def _other(name):
    return {"spread_home": "spread_away", "over": "under", "ml_home": "ml_away"}[name]


def _best_w(rs):
    """The blend weight with the lowest log loss on these rows."""
    if not rs:
        return None
    return min((sum(_ll(w * r[4] + (1 - w) * r[5], r[7]) for r in rs), w) for w in GRID)[1]


def fit(rs):
    """{market: w, "all": w}, each market shrunk toward the overall weight until it has a real sample."""
    wa = _best_w(rs)
    if wa is None:
        return {"all": None}
    out = {"all": wa}
    for mk in MARKETS:
        sub = [r for r in rs if r[2] == mk]
        if len(sub) < MIN_FIT:
            out[mk] = wa
            continue
        w = _best_w(sub)
        out[mk] = round((len(sub) * w + PRIOR * wa) / (len(sub) + PRIOR), 3)
    return out


def apply(w, mk, p, mp):
    ww = w.get(mk) if w.get(mk) is not None else w.get("all")
    return p if ww is None else ww * p + (1 - ww) * mp


def build():
    rs = rows()
    if not rs:
        return None
    days = sorted({r[0] for r in rs})
    games = {(r[0], r[1]) for r in rs}
    w_now = fit(rs)
    # leave-one-day-out: each day scored with the weight learned from every other day
    oos = []
    for d in days:
        wd = fit([r for r in rs if r[0] != d]) if len(days) > 1 else {"all": None}
        for r in rs:
            if r[0] == d:
                oos.append((r, apply(wd, r[2], r[4], r[5])))
    def ll(f):
        return round(sum(_ll(f(r, c), r[7]) for r, c in oos) / len(oos), 4)
    score = {"sim": ll(lambda r, c: r[4]), "market": ll(lambda r, c: r[5]), "cal": ll(lambda r, c: c)}
    by = {}
    for mk in MARKETS:
        sub = [(r, c) for r, c in oos if r[2] == mk]
        if sub:
            by[mk] = {"n": len(sub), "w": w_now.get(mk),
                      "sim": round(sum(_ll(r[4], r[7]) for r, c in sub) / len(sub), 4),
                      "market": round(sum(_ll(r[5], r[7]) for r, c in sub) / len(sub), 4),
                      "cal": round(sum(_ll(c, r[7]) for r, c in sub) / len(sub), 4)}
    # shadow record: a bet counts once per game per bet, at the first snapshot with 5%+ edge; calibrated chances are out-of-sample
    rec = {"raw": _empty(), "cal": _empty()}
    seen = {"raw": set(), "cal": set()}
    for r, c in sorted(oos, key=lambda x: (x[0][0], x[0][1])):
        for which, p in (("raw", r[4]), ("cal", c)):
            if p * r[6] - 1 >= EDGE and (r[0], r[1], r[3]) not in seen[which]:
                seen[which].add((r[0], r[1], r[3]))
                a = rec[which]
                a["n"] += 1
                a["w" if r[7] else "l"] += 1
                a["units"] = round(a["units"] + ((r[6] - 1) if r[7] else -1), 3)
                a["p"] = round(a["p"] + p, 4)
                a["need"] = round(a["need"] + 1 / r[6], 4)
    n_games = len(games)
    ready = (n_games >= PROMOTE_GAMES and score["cal"] < score["sim"] and
             _hit(rec["cal"]) >= _hit(rec["raw"]))
    return {"updated": store.iso(store.now()), "w": w_now, "games": n_games, "snaps": len(rs), "days": len(days),
            "score": score, "by_market": by, "shadow": rec, "edge": EDGE, "promote_at": PROMOTE_GAMES, "ready": bool(ready)}


def _empty():
    return {"n": 0, "w": 0, "l": 0, "units": 0.0, "p": 0.0, "need": 0.0}


def _hit(a):
    d = a["w"] + a["l"]
    return a["w"] / d if d else 0.0


def run(log=print):
    out = build()
    if not out:
        return None
    hist = store.load(FILE, {}).get("history", [])
    day = out["updated"][:10]
    hist = [h for h in hist if h["day"] != day] + [{"day": day, "w": out["w"], "games": out["games"], "sim": out["score"]["sim"], "cal": out["score"]["cal"]}]
    out["history"] = hist[-120:]
    store.save(FILE, out)
    log(f"[calib] shadow: w {out['w']} on {out['games']} games; log loss sim {out['score']['sim']} market {out['score']['market']} calibrated {out['score']['cal']}")
    return out
