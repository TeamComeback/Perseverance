"""A paper bankroll: place bets, grade them from final scores, keep the books honest."""
import uuid
from datetime import timedelta

from . import config, review, store


def new_book(name, run=1):
    t = store.iso(store.now())
    return {"name": name, "run": run, "cash": config.START_BANKROLL, "start": config.START_BANKROLL,
            "goal": config.GOAL, "peak": config.START_BANKROLL, "status": "racing",
            "started": t, "bets": [], "runs": []}


def open_bets(book):
    return [b for b in book["bets"] if b["status"] == "open"]


def cur_open(book):
    """Open bets of the current run. After a run is lost or ended, its open bets still grade, but under the old run."""
    return [b for b in open_bets(book) if b.get("run", book["run"]) == book["run"]]


def equity(book):
    return round(book["cash"] + sum(b["stake"] for b in cur_open(book)), 2)


def place(book, legs, stake, playbook, ev=None, p=None, note="", group=None, source="agent", issue=None):
    if book.get("wind_down"):          # run is being ended: open bets settle, nothing new is placed
        return None
    stake = round(min(stake, book["cash"]), 2)
    if stake < config.MIN_STAKE:
        return None
    dec = 1.0
    for l in legs:
        dec *= l["price"]
    bet = {"id": uuid.uuid4().hex[:8], "placed": store.iso(store.now()), "run": book["run"],
           "source": source, "playbook": playbook, "legs": legs, "dec": round(dec, 3),
           "stake": stake, "ev": round(ev, 4) if ev is not None else None,
           "p": round(p, 4) if p is not None else None, "note": note, "group": group,
           "issue": issue, "status": "open", "payout": None, "profit": None, "settled": None}
    book["cash"] = round(book["cash"] - stake, 2)
    book["bets"].append(bet)
    return bet


def grade_leg(leg, game):
    sc = {s["name"]: float(s["score"]) for s in (game.get("scores") or [])}
    if leg["home"] not in sc or leg["away"] not in sc:
        return None
    hs, as_ = sc[leg["home"]], sc[leg["away"]]
    m, o, pt = leg["market"], leg["outcome"], leg["point"]
    if m == "team_totals":
        if leg.get("team") not in sc:
            return None
        tt = sc[leg["team"]]
        diff = tt - pt if o == "Over" else pt - tt
        return "win" if diff > 0 else "loss" if diff < 0 else "push"
    if m == "totals":
        tot = hs + as_
        diff = tot - pt if o == "Over" else pt - tot
    else:
        if o == "Draw":
            return "win" if hs == as_ else "loss"
        mine, theirs = (hs, as_) if o == leg["home"] else (as_, hs)
        if m == "h2h" and mine == theirs:
            return "loss" if leg["sport"].startswith("soccer") else "push"
        diff = mine + (pt or 0) - theirs
    return "win" if diff > 0 else "loss" if diff < 0 else "push"


def settle(book, bet, result=None):
    """Settle from leg results, or force a result ('won'/'lost'/'push') for manual bets."""
    if result is None:
        rs = [l["result"] for l in bet["legs"]]
        if None in rs and "loss" not in rs:
            return False
        if "loss" in rs:
            result, mult = "lost", 0.0
        else:
            mult = 1.0
            for l in bet["legs"]:
                mult *= l["price"] if l["result"] == "win" else 1.0
            result = "push" if all(r in ("push", "void") for r in rs) else "won"
    else:
        mult = {"won": bet["dec"], "lost": 0.0, "push": 1.0}[result]
    payout = round(bet["stake"] * mult, 2)
    bet.update(status=result, payout=payout, profit=round(payout - bet["stake"], 2),
               settled=store.iso(store.now()))
    bet["review"] = review.build(bet)
    if bet.get("run", book["run"]) != book["run"]:
        # a bet from a run that is already over: it counts for that run's record, never for the new run's cash
        old = next((r for r in book["runs"] if r["run"] == bet["run"]), None)
        if old:
            old["final"] = round(old["final"] + bet["profit"], 2)
            old.update(run_summary(book, bet["run"]))
            old["late"] = True
        return True
    book["cash"] = round(book["cash"] + payout, 2)
    book["peak"] = max(book["peak"], equity(book))
    return True


def due_sports(books):
    """Sports with legs old enough to have a final score."""
    cut = store.now() - timedelta(hours=config.GRADE_AFTER_H)
    out = set()
    for book in books:
        for b in open_bets(book):
            for l in b["legs"]:
                if l["result"] is None and l.get("sport") and l.get("event_id") \
                        and not str(l.get("market", "")).startswith("player_") \
                        and store.parse(l["commence"]) < cut:
                    out.add(l["sport"])
    return out


def apply_scores(book, sport, games):
    """Grade every open leg in this sport. Returns the bets that settled."""
    by_id = {g["id"]: g for g in games}
    settled = []
    void_cut = store.now() - timedelta(days=config.VOID_AFTER_D)
    for b in open_bets(book):
        for l in b["legs"]:
            if l["result"] is not None or l.get("sport") != sport \
                    or str(l.get("market", "")).startswith("player_"):
                continue
            g = by_id.get(l["event_id"])
            if g and g.get("completed"):
                l["result"] = grade_leg(l, g)
                sc = {x["name"]: float(x["score"]) for x in (g.get("scores") or [])}
                if l["home"] in sc and l["away"] in sc:
                    l["final"] = {"home": sc[l["home"]], "away": sc[l["away"]]}
            if l["result"] is None and store.parse(l["commence"]) < void_cut:
                l["result"] = "void"
        if settle(book, b):
            settled.append(b)
    return settled


def request_end(book, note):
    """End the current run once its open bets have settled (the owner's call, HQ item t20261123run3).
    Nothing new is placed meanwhile, so the next run never inherits a stake from this one."""
    book["wind_down"] = note
    return book


def run_summary(book, run):
    """Record of one run, from its own bets: record, staked, profit, ROI and average closing-line value."""
    mine = [b for b in book["bets"] if b["run"] == run]
    settled = [b for b in mine if b["status"] in ("won", "lost", "push")]
    staked = sum(b["stake"] for b in settled)
    profit = sum(b["profit"] for b in settled)
    clvs = [l["price"] * l["close_p"] - 1 for b in mine for l in b["legs"] if l.get("close_p")]
    return {"bets": len(mine), "won": sum(b["status"] == "won" for b in settled),
            "lost": sum(b["status"] == "lost" for b in settled),
            "push": sum(b["status"] == "push" for b in settled), "staked": round(staked, 2),
            "profit": round(profit, 2), "roi": round(profit / staked, 4) if staked else 0,
            "clv": round(sum(clvs) / len(clvs), 4) if clvs else None}


def check_race(book, label, force=None):
    """Goal hit, out of funds or ended by the owner? Archive the run with a loss mark and start the next one.
    Out of funds = cash under the bust line, even with bets still open: those bets finish under the old run.
    force="ended" is the owner's reset button: the run is lost now."""
    eq = equity(book)
    done = force
    if not done:
        if eq >= book["goal"] and not cur_open(book):
            done = "won"
        elif book["cash"] < config.BUST_LINE:
            done = "busted"
        elif book.get("wind_down") and not cur_open(book):
            done = "ended"
    if not done:
        return None
    summary = {"run": book["run"], "result": done, "started": book["started"],
               "ended": store.iso(store.now()), "final": eq, "peak": book["peak"],
               "loss": done != "won", "open_at_end": len(cur_open(book)),
               **run_summary(book, book["run"])}
    if book.get("wind_down"):
        summary["note"] = book["wind_down"]
    if force:
        summary["note"] = summary.get("note") or "Ended by the owner."
    book["runs"].append(summary)
    book.pop("wind_down", None)
    if config.AUTO_RESTART:
        book.update(run=book["run"] + 1, cash=config.START_BANKROLL, peak=config.START_BANKROLL,
                    status="racing", started=store.iso(store.now()))
    else:
        book["status"] = done
    return summary


def playbook_scorecard(book, paused=()):
    """Settled bets grouped by playbook, for the current run and for all runs:
    bets, record, staked, profit, ROI, average closing-line value, open count and whether it is paused."""
    def rows(bets):
        out = {}
        for pb in sorted({b["playbook"] for b in bets}):
            mine = [b for b in bets if b["playbook"] == pb]
            settled = [b for b in mine if b["status"] in ("won", "lost", "push")]
            staked = sum(b["stake"] for b in settled)
            profit = sum(b["profit"] for b in settled)
            clvs = [l["price"] * l["close_p"] - 1 for b in mine for l in b["legs"] if l.get("close_p")]
            out[pb] = {"bets": len(settled), "won": sum(b["status"] == "won" for b in settled),
                       "lost": sum(b["status"] == "lost" for b in settled),
                       "push": sum(b["status"] == "push" for b in settled),
                       "staked": round(staked, 2), "profit": round(profit, 2),
                       "roi": round(profit / staked, 4) if staked else None,
                       "clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
                       "open": len(mine) - len(settled), "paused": pb in paused}
        return out
    cur = [b for b in book["bets"] if b["run"] == book["run"]]
    return {"run": book["run"], "current": rows(cur), "all": rows(book["bets"])}


def record_stats(book):
    settled = [b for b in book["bets"] if b["status"] in ("won", "lost", "push")]
    staked = sum(b["stake"] for b in settled)
    profit = sum(b["profit"] for b in settled)
    won = sum(b["status"] == "won" for b in settled)
    lost = sum(b["status"] == "lost" for b in settled)
    clvs = [l["price"] * l["close_p"] - 1 for b in book["bets"] for l in b["legs"] if l.get("close_p")]
    settled_legs = [l for b in settled for l in b["legs"]]
    no_close = sum(1 for l in settled_legs if not l.get("close_p"))
    mins = [l["close_mins"] for b in book["bets"] for l in b["legs"] if l.get("close_mins") is not None]
    shops = [l["shop"]["gain"] for b in book["bets"] for l in b["legs"] if (l.get("shop") or {}).get("gain") is not None]
    return {"bets": len(settled), "won": won, "lost": lost,
            "shop_n": len(shops), "shop_avg": round(sum(shops) / len(shops), 4) if shops else None,
            "push": len(settled) - won - lost, "staked": round(staked, 2),
            "profit": round(profit, 2), "roi": round(profit / staked, 4) if staked else 0,
            "clv": round(sum(clvs) / len(clvs), 4) if clvs else None, "clv_n": len(clvs),
            "clv_no_close": no_close,
            "close_mins_median": sorted(mins)[len(mins) // 2] if mins else None,
            "open": len(open_bets(book)), "equity": equity(book)}
