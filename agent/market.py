"""Reads a sportsbook board: fair odds from the market consensus, value gaps, arbitrage."""
from statistics import mean

from . import config, store


def to_american(dec: float) -> int:
    if dec >= 2:
        return round((dec - 1) * 100)
    return -round(100 / (dec - 1))


def from_american(a: float) -> float:
    a = float(a)
    return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)


def kelly(p: float, dec: float) -> float:
    b = dec - 1
    return max(0.0, (b * p - (1 - p)) / b) if b > 0 else 0.0


def _signature(market_key, outcomes):
    """Books are only comparable when they offer the same lines."""
    return (market_key, tuple(sorted((o["name"], o.get("point")) for o in outcomes)))


def analyse_event(ev):
    """Return per-line consensus data for one event:
    [{market, outcomes:[{name, point, best, book, fair_p, n}], arb}]"""
    groups = {}
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            outs = m.get("outcomes", [])
            if len(outs) < 2 or any(o.get("price", 0) <= 1 for o in outs):
                continue
            groups.setdefault(_signature(m["key"], outs), []).append(
                (bk["key"], {(o["name"], o.get("point")): o["price"] for o in outs}))
    lines = []
    for (mkey, sig), books in groups.items():
        if len(books) < config.MIN_BOOKS:
            continue
        devig = {}
        for bk, prices in books:
            tot = sum(1 / p for p in prices.values())
            devig[bk] = {k: (1 / p) / tot for k, p in prices.items()}
        outs = []
        for k in sig:
            best_bk, best = max(((bk, pr[k]) for bk, pr in books), key=lambda x: x[1])
            others = [devig[bk][k] for bk, _ in books if bk != best_bk]
            outs.append({"name": k[0], "point": k[1], "best": best, "book": best_bk,
                         "fair_p": mean(others), "n": len(books),
                         "all_fair": mean(devig[bk][k] for bk, _ in books)})
        arb = 1 - sum(1 / o["best"] for o in outs)
        lines.append({"market": mkey, "outcomes": outs, "arb": arb})
    return lines


def window_ok(ev, now=None):
    now = now or store.now()
    h = (store.parse(ev["commence_time"]) - now).total_seconds() / 3600
    return config.WINDOW_MIN_H <= h <= config.WINDOW_MAX_H


def leg_from(ev, line, o):
    return {"event_id": ev["id"], "sport": ev["sport_key"], "commence": ev["commence_time"],
            "home": ev["home_team"], "away": ev["away_team"], "market": line["market"],
            "outcome": o["name"], "point": o["point"], "price": round(o["best"], 3),
            "book": o["book"], "fair_p": round(o["fair_p"], 4), "close_p": None, "result": None}


def classify(p, dec):
    if p >= 0.5:
        return "value_fav"
    if p >= 0.2:
        return "value_dog"
    return "longshot"


def find_opportunities(events, params):
    """Every value gap and arbitrage on the board, before bankroll rules."""
    values, arbs = [], []
    for ev in events:
        if not window_ok(ev):
            continue
        for line in analyse_event(ev):
            if line["arb"] >= params["arb_min"] and line["arb"] <= config.MAX_ARB:
                arbs.append({"event": ev, "line": line, "profit": line["arb"],
                             "legs": [leg_from(ev, line, o) for o in line["outcomes"]]})
            for o in line["outcomes"]:
                evv = o["fair_p"] * o["best"] - 1
                if evv < params["min_edge"] or evv > config.MAX_EV or o["best"] > params["max_dec"]:
                    continue
                values.append({"event": ev, "line": line, "ev": evv, "p": o["fair_p"],
                               "dec": o["best"], "playbook": classify(o["fair_p"], o["best"]),
                               "leg": leg_from(ev, line, o)})
    values.sort(key=lambda c: -c["ev"])
    arbs.sort(key=lambda a: -a["profit"])
    return values, arbs


def board_summary(events):
    """Compact board for the dashboard's 'bet this' list."""
    out = []
    for ev in events:
        if not window_ok(ev):
            continue
        lines = []
        for line in analyse_event(ev):
            lines.append({"market": line["market"], "outcomes": [
                {"name": o["name"], "point": o["point"], "price": round(o["best"], 3),
                 "am": to_american(o["best"]), "book": o["book"],
                 "fair_p": round(o["fair_p"], 4),
                 "ev": round(o["fair_p"] * o["best"] - 1, 4)} for o in line["outcomes"]]})
        if lines:
            out.append({"id": ev["id"], "sport": ev["sport_key"], "commence": ev["commence_time"],
                        "home": ev["home_team"], "away": ev["away_team"], "lines": lines})
    return out


def closing_update(open_bets, events):
    """Refresh the market's fair probability for open legs (closing-line value)."""
    by_id = {e["id"]: e for e in events}
    for bet in open_bets:
        for leg in bet["legs"]:
            ev = by_id.get(leg["event_id"])
            if not ev or store.parse(ev["commence_time"]) <= store.now():
                continue
            for line in analyse_event(ev):
                if line["market"] != leg["market"]:
                    continue
                for o in line["outcomes"]:
                    if o["name"] == leg["outcome"] and o["point"] == leg["point"]:
                        leg["close_p"] = round(o["all_fair"], 4)
                        # When the "close" was taken: the last scan before the game. Minutes
                        # before start show how close to the real closing line it is.
                        leg["close_at"] = store.iso(store.now())
                        leg["close_mins"] = int((store.parse(ev["commence_time"]) - store.now()).total_seconds() // 60)
