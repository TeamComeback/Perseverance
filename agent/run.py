"""Entry point.  python -m agent.run cycle | nightly | manual-issue"""
import json
import os
import re
import sys

from . import book as bk
from . import brain, config, market, store
from . import props as nba_props
from .odds import Budget, OddsClient, OutOfBudget


def load_all():
    state = store.load("state.json", None) or brain.new_state()
    for k, v in config.DEFAULT_PARAMS.items():
        state["params"].setdefault(k, v)
    for k in config.PLAYBOOKS:
        state["arms"].setdefault(k, brain._blank())
    manual = store.load("manual.json", None) or {"book": bk.new_book("Your book")}
    return state, manual


def save_all(state, manual, budget=None):
    state["updated"] = store.iso(store.now())
    state["stats"] = bk.record_stats(state["book"])
    state["weights"] = {k: round(brain.arm_weight(v), 3) for k, v in state["arms"].items()}
    state["paused"] = [k for k, v in state["arms"].items() if brain.benched(v)]
    manual["updated"] = state["updated"]
    manual["stats"] = bk.record_stats(manual["book"])
    store.save("state.json", state)
    store.save("manual.json", manual)
    if budget:
        budget.save()


def history_point(state, manual):
    h = store.load("history.json", [])
    h.append({"ts": store.iso(store.now()), "a": bk.equity(state["book"]),
              "m": bk.equity(manual["book"]), "run": state["book"]["run"]})
    store.save("history.json", h[-3000:])


def signed(v):
    return f"{'+' if v > 0 else '-' if v < 0 else ''}${abs(v):.2f}"


def describe(bet):
    l = bet["legs"][0]
    if len(bet["legs"]) > 1:
        return f"{len(bet['legs'])}-leg parlay @ {market.to_american(bet['dec']):+d}"
    pt = "" if l.get("point") is None else (f" {l['point']:+g}" if l["market"] == "spreads" else f" {l['point']:g}")
    return f"{l['outcome']}{pt} @ {market.to_american(l['price']):+d} ({l.get('book') or 'manual'})"


def grade(state, manual, client):
    try:
        done = nba_props.grade(state["book"])
        brain.learn_from(state, done)
        for b in done:
            brain.note(state, "result", f"{b['status'].upper()} {signed(b['profit'])}: {describe(b)} — {b['review']['headline']}"
                       + (f" [{', '.join(b['review']['tags'])}]" if b["review"].get("tags") else ""))
        for b in nba_props.grade(manual["book"]):
            if b.get("issue"):
                store.gh_comment(b["issue"], f"**{b['status'].upper()}** {signed(b['profit'])}. "
                                 f"Your bankroll: ${bk.equity(manual['book']):.2f}")
                store.gh_close(b["issue"])
    except Exception as e:
        brain.note(state, "error", f"Prop grading failed: {e}")
    sports = bk.due_sports([state["book"], manual["book"]])
    for sport in sorted(sports):
        try:
            games = client.scores(sport)
        except OutOfBudget:
            brain.note(state, "budget", f"Held off grading {sport}: credits are at the reserve floor.")
            break
        except Exception as e:
            brain.note(state, "error", f"Scores for {sport} failed: {e}")
            continue
        done = bk.apply_scores(state["book"], sport, games)
        brain.learn_from(state, done)
        for b in done:
            brain.note(state, "result", f"{b['status'].upper()} {signed(b['profit'])}: {describe(b)} — {b['review']['headline']}"
                       + (f" [{', '.join(b['review']['tags'])}]" if b["review"].get("tags") else ""))
        for b in bk.apply_scores(manual["book"], sport, games):
            if b.get("issue"):
                store.gh_comment(b["issue"], f"**{b['status'].upper()}** {signed(b['profit'])}. "
                                 f"Your bankroll: ${bk.equity(manual['book']):.2f}")
                store.gh_close(b["issue"])
    for name, bkobj in (("Perseverance", state["book"]), ("Your book", manual["book"])):
        r = bk.check_race(bkobj, name)
        if r:
            brain.note(state, "race", f"{name} run #{r['run']} {r['result'].upper()} at ${r['final']:.2f} "
                                      f"(peak ${r['peak']:.2f}, {r['bets']} bets). "
                                      + ("New run started from $100; lessons carry over." if config.AUTO_RESTART else ""))


def cycle():
    budget = Budget()
    client = OddsClient(budget)
    state, manual = load_all()
    try:
        client.sports()                      # free: refreshes the credit count
    except Exception as e:
        brain.note(state, "error", f"Odds API unreachable: {e}")
        save_all(state, manual, budget)
        return
    budget.start_run()                       # this run's share of the remaining credits

    grade(state, manual, client)
    brain.adapt(state)

    events, scanned = [], []
    for score, sport, n in brain.pick_sports(state, client, budget):
        cost = len(config.markets_for(sport).split(","))
        if not budget.can(cost):
            continue
        try:
            evs = client.odds(sport)
        except OutOfBudget:
            break
        except Exception as e:
            brain.note(state, "error", f"Odds for {sport} failed: {e}")
            continue
        events += evs
        scanned.append(f"{sport} ({len(evs)})")
        state["sports"].setdefault(sport, {})["last_scanned"] = store.iso(store.now())

    market.closing_update(bk.open_bets(state["book"]) + bk.open_bets(manual["book"]), events)
    placed = brain.bet_on_board(state, events) if events else []
    brain.drought(state, scanned, placed)

    board = store.load("board.json", {"events": []})
    fresh = market.board_summary(events)
    fresh_sports = {e["sport_key"] for e in events}
    keep = [e for e in board.get("events", [])
            if e["sport"] not in fresh_sports and market.window_ok({"commence_time": e["commence"]})]
    store.save("board.json", {"updated": store.iso(store.now()),
                              "events": sorted(fresh + keep, key=lambda e: e["commence"])})

    eq = bk.equity(state["book"])
    if scanned:
        brain.note(state, "scan", f"Scanned {', '.join(scanned)} for {budget.spent_this_run} credits. "
                   + (f"Placed {len(placed)}: " + "; ".join(describe(b) for b in placed) if placed
                      else "Nothing cleared the bar.") + f" Equity ${eq:.2f}.")
    else:
        brain.note(state, "scan", f"No scan this run (allowance {budget.allowance} credits, "
                                  f"{budget.b.get('remaining')} left this month).")
    history_point(state, manual)
    save_all(state, manual, budget)
    print(state["journal"][-1]["text"])


def props_run():
    """Evening NBA matchup run (about 6pm ET, after most injury news)."""
    budget = Budget()
    client = OddsClient(budget)
    state, manual = load_all()
    try:
        client.sports()
    except Exception as e:
        brain.note(state, "error", f"Odds API unreachable: {e}")
        save_all(state, manual, budget)
        return
    budget.start_run(units=config.PROPS_UNITS)
    grade(state, manual, client)
    try:
        out = nba_props.run(state, manual, client, budget)
    except Exception as e:
        import traceback; traceback.print_exc()
        brain.note(state, "error", f"NBA props run failed: {e}")
        save_all(state, manual, budget)
        return
    if not out["games"]:
        brain.note(state, "props", out.get("note", "No NBA games today."))
    else:
        top = out["spots"][:3]
        tops = "; ".join(f"{x['name']} {x['stat']} {(x['mult'] - 1) * 100:+.0f}% vs {x['opp']}" for x in top)
        placed = out.get("placed", [])
        brain.note(state, "props",
                   f"NBA matchups: {len(out['games'])} games, strongest spots: {tops}. "
                   + (f"Bought prop odds for {', '.join(out['bought'])} ({budget.spent_this_run} credits). " if out.get("bought")
                      else f"No credits to price props this run (allowance {budget.allowance}). ")
                   + (f"Placed {len(placed)}: " + "; ".join(describe(b) for b in placed) if placed
                      else ("Nothing cleared the bar." if out.get("bought") else "")))
    history_point(state, manual)
    save_all(state, manual, budget)
    print(state["journal"][-1]["text"])


def nightly():
    budget = Budget()                       # no scan allowance: only essential grading
    client = OddsClient(budget)
    state, manual = load_all()
    try:
        client.sports()
    except Exception:
        pass
    grade(state, manual, client)
    try:
        from . import nba_data
        nba_data.update(log=lambda m: None)
    except Exception as e:
        brain.note(state, "error", f"NBA box-score refresh failed: {e}")
    brain.adapt(state)
    brain.strategist(state)
    s = bk.record_stats(state["book"])
    brain.note(state, "daily", f"Day close — equity ${s['equity']:.2f} (run #{state['book']['run']}), "
                               f"{s['won']}-{s['lost']}-{s['push']}, ROI {s['roi']:+.1%}"
               + (f", CLV {s['clv']:+.1%}" if s["clv"] is not None else "") + ".")
    history_point(state, manual)
    save_all(state, manual, budget)


# ---- Manual bets through GitHub issues --------------------------------------
FIELDS = {"sport key": "sport", "event id": "event_id", "market": "market", "selection": "selection",
          "line": "point", "odds (american)": "odds", "sportsbook": "book", "stake ($)": "stake",
          "notes": "notes"}


def parse_form(body):
    out = {}
    for label, val in re.findall(r"###\s*(.+?)\s*\n+(.*?)(?=\n###|\Z)", body or "", re.S):
        key = FIELDS.get(label.strip().lower())
        val = val.strip()
        if key and val and val != "_No response_":
            out[key] = val
    return out


def manual_issue():
    ev = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    issue = ev["issue"]
    num = issue["number"]
    state, manual = load_all()
    book = manual["book"]
    existing = next((b for b in book["bets"] if b.get("issue") == num), None)

    if "comment" in ev:
        cmd = ev["comment"]["body"].strip().lower()
        m = re.match(r"/(settle|cancel)\s*(won|win|lost|loss|push)?", cmd)
        if not m or not existing or existing["status"] != "open":
            return
        if m.group(1) == "cancel":
            bk.settle(book, existing, "push"); existing["status"] = "cancelled"
            msg = "Cancelled, stake returned."
        else:
            res = {"win": "won", "loss": "lost"}.get(m.group(2), m.group(2))
            if not res:
                store.gh_comment(num, "Use `/settle won`, `/settle lost` or `/settle push`."); return
            bk.settle(book, existing, res)
            msg = f"**{res.upper()}** {signed(existing['profit'])}."
        store.gh_comment(num, f"{msg} Your bankroll: ${bk.equity(book):.2f}")
        store.gh_close(num)
    else:
        if existing:
            return
        f = parse_form(issue.get("body"))
        try:
            dec = market.from_american(f["odds"].replace("+", ""))
            stake = float(f.get("stake", "5").replace("$", ""))
            if stake < config.MIN_STAKE:
                store.gh_comment(num, f"Minimum bet is ${config.MIN_STAKE:.0f}. Edit the stake and open a new bet.")
                store.gh_close(num)
                return
        except Exception:
            store.gh_comment(num, "Couldn't read the odds or stake. Odds look like `-110` or `+150`.")
            return
        if stake > book["cash"]:
            store.gh_comment(num, f"Not enough fake cash: you have ${book['cash']:.2f}.")
            return
        evinfo = None
        if f.get("event_id"):
            board = store.load("board.json", {"events": []})
            evinfo = next((e for e in board["events"] if e["id"] == f["event_id"]), None)
            if evinfo and store.parse(evinfo["commence"]) <= store.now():
                store.gh_comment(num, "That game has already started, so it can't be bet. "
                                      "Pick something else from the board.")
                store.gh_close(num)
                return
        point = float(f["point"]) if f.get("point") not in (None, "", "None") else None
        if str(f.get("market", "")).startswith("player_") and f.get("event_id"):
            # a prop from the NBA matchup board: grade it from the box score
            pb = store.load("props.json", {})
            pe = (pb.get("events") or {}).get(f["event_id"])
            sel = f.get("selection", "")
            side = "Under" if sel.lower().rstrip().endswith("under") else "Over"
            who = re.sub(r"\s+(over|under)\s*$", "", sel, flags=re.I).strip()
            key_stat = {v: k for k, v in __import__("agent.nba_model", fromlist=["x"]).ODDS_KEY.items()}
            if pe and point is not None and f["market"] in key_stat:
                if store.parse(pe["commence"]) <= store.now():
                    store.gh_comment(num, "That game has already started, so it can't be bet.")
                    store.gh_close(num)
                    return
                leg = {"event_id": f["event_id"], "sport": "basketball_nba", "commence": pe["commence"],
                       "home": pe["home"], "away": pe["away"], "market": f["market"], "outcome": f"{who} {side}",
                       "side": side, "player": who, "player_id": (pb.get("players") or {}).get(nba_props.norm(who)),
                       "stat": key_stat[f["market"]], "espn_id": pe["espn_id"], "point": point,
                       "price": round(dec, 3), "book": f.get("book"), "fair_p": None, "close_p": None, "result": None}
                b = bk.place(book, [leg], stake, "manual", note=f.get("notes", ""), source="you", issue=num)
                store.gh_comment(num, f"Logged: ${stake:.2f} on {describe(b)}. I'll grade it from the box score. "
                                      f"Cash left: ${book['cash']:.2f}")
                save_all(state, manual)
                return
        leg = {"event_id": evinfo and evinfo["id"], "sport": evinfo and evinfo["sport"],
               "commence": evinfo["commence"] if evinfo else store.iso(store.now()),
               "home": evinfo["home"] if evinfo else "", "away": evinfo["away"] if evinfo else "",
               "market": f.get("market", "custom") if evinfo else "custom",
               "outcome": f.get("selection", issue["title"]), "point": point, "price": round(dec, 3),
               "book": f.get("book"), "fair_p": None, "close_p": None, "result": None}
        b = bk.place(book, [leg], stake, "manual", note=f.get("notes", ""), source="you", issue=num)
        if not b:
            store.gh_comment(num, f"Minimum bet is ${config.MIN_STAKE:.0f}."); return
        how = ("I'll grade it automatically from the final score." if evinfo else
               "Comment `/settle won`, `/settle lost` or `/settle push` when it's decided.")
        store.gh_comment(num, f"Logged: ${stake:.2f} on {describe(b)}. {how} "
                              f"Cash left: ${book['cash']:.2f}")
    save_all(state, manual)


if __name__ == "__main__":
    {"cycle": cycle, "nightly": nightly, "props": props_run, "manual-issue": manual_issue}[sys.argv[1]]()
