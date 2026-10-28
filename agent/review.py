"""Post-mortems: when a bet settles, record what happened and why, for later analysis.

Everything here is derived from facts stored on the bet (final score, box score line, the
model's projection at bet time, prices) so the review is honest and reproducible.
"""
import math

NEAR = {"basketball": 3.0, "americanfootball": 3.0, "icehockey": 1.0, "baseball": 1.0, "soccer": 1.0}
STATNAME = {"pts": "points", "reb": "rebounds", "ast": "assists", "tpm": "threes", "pra": "pts+reb+ast"}


def _g(x):
    return f"{x:g}"


def _near(leg):
    return NEAR.get((leg.get("sport") or "").split("_")[0], 2.0)


def _final_txt(leg):
    f = leg.get("final")
    if not f or not leg.get("home"):
        return None
    return f"{leg['away']} {_g(f['away'])} – {leg['home']} {_g(f['home'])}"


def _price_lines(leg, bet):
    out, tags = [], []
    price, fair, close = leg.get("price"), leg.get("fair_p"), leg.get("close_p")
    if price:
        be = 1 / price
        if bet.get("p") is not None and len(bet["legs"]) == 1:
            out.append(f"We priced it at {bet['p']:.0%} to hit vs {be:.0%} needed to break even at {_g(price)}x "
                       f"({(bet.get('ev') or 0):+.1%} expected edge).")
        if close is not None:
            beat = close > be
            out.append(f"Closing consensus {close:.0%} vs {be:.0%} implied by our price: "
                       + ("we beat the closing line." if beat else "the market moved against us before the game."))
            tags.append("beat-closing-line" if beat else "lost-closing-line")
        elif fair is not None:
            out.append(f"Consensus fair probability when placed: {fair:.0%} (no closing price captured).")
    return out, tags


def _game_leg(leg, bet):
    f = leg.get("final")
    why, tags = [], []
    if f:
        hs, as_ = f["home"], f["away"]
        m, o, pt = leg["market"], leg["outcome"], leg.get("point")
        margin_home = hs - as_
        if m == "totals":
            tot = hs + as_
            over = o == "Over"
            diff = tot - pt if over else pt - tot
            why.append(f"Game total was {_g(tot)} against a line of {_g(pt)}: {o.lower()} "
                       + (f"cleared by {_g(abs(diff))}." if diff > 0 else f"missed by {_g(abs(diff))}." if diff < 0 else "landed on the number."))
            head = f"Total {_g(tot)} vs {o} {_g(pt)}"
        elif o == "Draw":
            diff = 0 if hs == as_ else -1
            head = "Draw" if hs == as_ else "No draw"
            why.append("Match finished level." if hs == as_ else "Match had a winner.")
        else:
            mine, theirs = (hs, as_) if o == leg["home"] else (as_, hs)
            if m == "spreads":
                diff = mine + (pt or 0) - theirs
                why.append(f"{o} {'won' if mine > theirs else 'lost' if mine < theirs else 'tied'} the game by "
                           f"{_g(abs(mine - theirs))} and needed to {'win or lose by less than ' if (pt or 0) > 0 else 'win by more than '}{_g(abs(pt or 0))}: "
                           + (f"covered by {_g(abs(diff))}." if diff > 0 else f"missed the spread by {_g(abs(diff))}." if diff < 0 else "pushed."))
                head = f"{o} {'+' if (pt or 0) > 0 else ''}{_g(pt or 0)}: {'covered' if diff > 0 else 'missed' if diff < 0 else 'pushed'} by {_g(abs(diff))}"
            else:
                diff = mine - theirs
                why.append(f"{o} {'won' if diff > 0 else 'lost' if diff < 0 else 'tied'} by {_g(abs(diff))}.")
                head = f"{o} {'won' if diff > 0 else 'lost' if diff < 0 else 'tied'} by {_g(abs(diff))}"
                if bet.get("p") is not None and len(bet["legs"]) == 1:
                    if diff < 0 and bet["p"] < 0.5:
                        tags.append("underdog-lost")
                    if diff > 0 and bet["p"] < 0.5:
                        tags.append("upset-hit")
                    if diff < 0 and bet["p"] >= 0.55:
                        tags.append("favorite-upset")
        if diff != 0 and abs(diff) <= _near(leg):
            tags.append("near-miss" if diff < 0 else "narrow-win")
        if abs(margin_home) >= {"basketball": 18, "americanfootball": 21, "icehockey": 4, "baseball": 7, "soccer": 3}.get((leg.get("sport") or "").split("_")[0], 99):
            tags.append("blowout")
    else:
        head = "Result recorded"
    if leg.get("result") == "void":
        head = "Voided (no final score)"
        why = ["No final score was found within the grading window, so the stake was returned."]
        tags = ["void"]
    return head, why, tags


def _prop_leg(leg, bet):
    why, tags = [], []
    stat, line, side = leg.get("stat"), leg.get("point"), leg.get("side")
    name = leg.get("player", "Player")
    if leg.get("result") == "void" or leg.get("actual") is None:
        return ("Player did not play: voided",
                [f"{name} was not in the box score (did not play), so the bet is voided and the stake returned."], ["dnp-void"])
    v, over = leg["actual"], side == "Over"
    diff = (v - line) if over else (line - v)
    sn = STATNAME.get(stat, stat)
    why.append(f"{name} finished with {_g(v)} {sn} against a line of {_g(line)}: {side.lower()} "
               + (f"cleared by {_g(abs(diff))}." if diff > 0 else f"missed by {_g(abs(diff))}." if diff < 0 else "landed on the number."))
    head = f"{_g(v)} {sn} vs {side} {_g(line)}"
    box, mdl = leg.get("box") or {}, leg.get("model") or {}
    pm, mp = box.get("min"), mdl.get("minutes")
    if pm is not None and mp:
        why.append(f"Minutes: played {_g(pm)} vs {_g(mp)} projected.")
        if pm < mp * 0.8:
            tags.append("minutes-short")
        elif pm > mp * 1.2:
            tags.append("minutes-long")
    f = leg.get("final")
    if f:
        mg = abs(f["home"] - f["away"])
        if mg >= 18:
            tags.append("blowout")
            if pm is not None and mp and pm < mp * 0.9:
                why.append(f"Game was a {_g(mg)}-point blowout, which cuts starters' minutes.")
            else:
                why.append(f"Game was a {_g(mg)}-point blowout, but his minutes held up.")
    if box:
        bits = []
        if stat in ("pts", "pra"):
            bits.append(f"{_g(box.get('pts', 0))} pts on {_g(box.get('fga', 0))} FGA / {_g(box.get('fta', 0))} FTA")
        if stat in ("reb", "pra"):
            bits.append(f"{_g(box.get('reb', 0))} reb")
        if stat in ("ast", "pra"):
            bits.append(f"{_g(box.get('ast', 0))} ast, {_g(box.get('tov', 0))} TO")
        if stat == "tpm":
            bits.append(f"{_g(box.get('tpm', 0))}-of-{_g(box.get('tpa', 0))} from three")
        if bits:
            why.append("Box score: " + " · ".join(bits) + ".")
    if mdl:
        proj, base, sd = mdl.get("proj"), mdl.get("base"), mdl.get("sd")
        if proj is not None:
            err = v - proj
            z = err / sd if sd else 0
            why.append(f"Model projected {_g(round(proj, 1))} ({_g(round(base, 1))} norm × {mdl.get('mult', 1):.2f} matchup); "
                       f"actual was {err:+.1f} off" + (f" ({z:+.1f} standard deviations)." if sd else "."))
            if pm is not None and mp and pm >= mp * 0.8:
                tags.append("ordinary-variance" if abs(z) <= 1.0 else "model-miss" if diff < 0 else "model-beat")
            if pm and mp and base:
                norm_here = base * pm / mp
                beat_norm = v > norm_here
                soft = mdl.get("mult", 1) > 1
                if abs(v - norm_here) > 0.5:
                    tags.append("matchup-read-right" if beat_norm == soft else "matchup-read-wrong")
                    why.append(f"Matchup read was {'right' if beat_norm == soft else 'wrong'}: he {'beat' if beat_norm else 'fell short of'} his own norm "
                               f"for the minutes played ({_g(round(norm_here, 1))}) in a matchup the model rated {'soft' if soft else 'tough'}.")
        for s in (mdl.get("signals") or [])[:3]:
            if s.get("txt"):
                why.append("Signal: " + s["txt"])
    if abs(diff) <= max(1.0, 0.05 * line) and diff < 0:
        tags.append("near-miss")
    return head, why, tags


def build(bet):
    """A post-mortem dict for a settled bet. Never raises."""
    try:
        legs = []
        all_tags = []
        for l in bet["legs"]:
            prop = str(l.get("market", "")).startswith("player_")
            if l.get("result") is None and not l.get("final") and l.get("actual") is None:
                head, why, tags = "settled manually", [], []
            elif prop:
                head, why, tags = _prop_leg(l, bet)
            else:
                head, why, tags = _game_leg(l, bet)
            pl, ptags = _price_lines(l, bet)
            why += pl
            tags += ptags
            legs.append({"outcome": l.get("outcome"), "result": l.get("result"), "headline": head,
                         "final": _final_txt(l), "why": why, "tags": tags})
            all_tags += tags
        res = bet["status"].upper()
        if len(legs) == 1:
            headline = f"{res}: {legs[0]['headline']}"
        else:
            headline = f"{res}: " + "; ".join(f"{(x['result'] or '?').upper()} {x['headline']}" for x in legs)
        return {"headline": headline, "final": legs[0]["final"] if legs else None, "legs": legs,
                "tags": sorted(set(all_tags))}
    except Exception as e:  # a post-mortem must never block settlement
        return {"headline": bet.get("status", "").upper(), "error": str(e), "legs": [], "tags": []}
