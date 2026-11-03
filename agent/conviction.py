"""Conviction 1-5 for a matchup play: the things that have to line up before a projection deserves trust.

Points (clamped to 1-5 at the end):
  matchup size 0-3   |ln(mult)| >= .08 / .055 / .035 (roughly the top 3% / 12% / 35% of a slate)
  signals agree +1   defense-vs-position, similar players and head-to-head all point the same way
  minutes +1 / -1    starter or 26+ projected minutes; -1 for an injury tag
  rest -1            back-to-back or 3rd game in 4 nights, for overs
  blowout -1         |spread| >= 11 and he plays starter minutes, for overs
  Vegas +1 / -1      the market's implied team total is 3+ points above/below the model, for scoring stats
  usage bump +1/-1   a teammate is out and he produces 12%+ more without him (over +1, under -1)
  price +1 / -1      (priced lines only) edge >= 5% / no edge at this price
  line move +1 / -1  (priced lines only) the line moved toward / against the lean since it opened
"""
SCORING = {"pts", "pra", "ast", "tpm"}
STATNAME = {"pts": "points", "reb": "rebounds", "ast": "assists", "tpm": "threes", "pra": "PRA", "ra": "R+A"}


# Factors the agent re-weights from its own track record (learn.py). 1.0 = as designed, 0 = ignored, up to 2.
FACTORS = ("agree", "minutes", "inj", "rest", "blowout", "vegas", "bump")


def base(pr, stat, f, W=None):
    """pr: model projection (mult, signals); f: flags; W: learned factor weights.
    Returns (points, reasons, codes) before price and line movement. codes are 'factor:+1' / 'factor:-1'."""
    import math
    W = W or {}
    w = lambda k: W.get(k, 1.0)
    st = abs(math.log(pr["mult"])) if pr.get("mult") else 0.0
    up = pr["mult"] > 1
    pts = 3 if st >= 0.08 else 2 if st >= 0.055 else 1 if st >= 0.035 else 0
    why = ["big matchup effect" if st >= 0.055 else "moderate matchup" if st >= 0.035 else "small matchup effect"]
    codes = []

    def add(code, sign, text):
        nonlocal pts
        pts += sign * w(code)
        codes.append(f"{code}:{sign:+d}")
        why.append(text)
    sig = [g for g in pr.get("signals", []) if g.get("v") is not None and g["k"] in ("dvp", "sim", "h2h")
           and (g["k"] != "h2h" or (g.get("n") or 0) >= 2)]
    if len(sig) >= 2 and all(abs(g["v"] - 1) >= 0.02 and (g["v"] > 1) == up for g in sig):
        add("agree", 1, "signals agree")
    stt = (f.get("status") or "").lower()
    if any(x in stt for x in ("quest", "gtd", "doubt", "day-to-day", "day to day")):
        add("inj", -1, "injury tag")
    elif f.get("starter") or (f.get("minutes") or 0) >= 26:
        add("minutes", 1, "secure minutes")
    if up and (f.get("b2b") or f.get("three_in_four")):
        add("rest", -1, "back-to-back" if f.get("b2b") else "3rd game in 4 nights")
    if up and (f.get("spread") or 0) >= 11 and (f.get("starter") or (f.get("minutes") or 0) >= 26):
        add("blowout", -1, f"blowout risk ({f['spread']:g}-pt spread)")
    vd = f.get("vegas_diff")
    if vd is not None and stat in SCORING and abs(vd) >= 3:
        agree = (vd > 0) == up
        add("vegas", 1 if agree else -1, ("" if agree else "against: ") + f"Vegas team total {vd:+.0f} vs model")
    bump = (f.get("bump") or {}).get(stat)
    if bump and bump["delta"] >= 0.12:
        add("bump", 1 if up else -1, ("" if up else "against: ") + f"+{bump['delta'] * 100:.0f}% without {bump['out']}")
    return round(pts, 2), why, codes


def final(pts, why, ev=None, open_line=None, line=None, lean_over=True):
    why = list(why)
    if ev is not None:
        if ev >= 0.05:
            pts += 1; why.append("priced edge")
        elif ev <= 0:
            pts -= 1; why.append("no edge at this price")
    if open_line is not None and line is not None and open_line != line:
        toward = (line > open_line) == lean_over
        pts += 1 if toward else -1
        why.append("line moving his way" if toward else "line moving against him")
    return max(1, min(5, int(round(pts)))), why
