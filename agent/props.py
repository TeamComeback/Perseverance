"""Evening NBA props run: find the matchup spots, buy prop odds only where they matter, bet, and grade.

Flow:
 1. Free: refresh box scores (ESPN), build the matchup model, read tonight's slate and injuries.
 2. Free: project every rotation player in every stat; rank spots by how far the matchup moves him.
 3. Paid: pull player-prop odds only for the games holding the strongest spots, within the credit share.
 4. Price every line we paid for: model probability blended with the market's no-vig price.
 5. Bet the best overs and unders. Grade later from ESPN box scores (no credits).
"""
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from . import book as bk
from . import brain, config, market, store
from . import nba_data as nd
from . import nba_model as nm

SPORT = "basketball_nba"
MIN_MINUTES = 18
SKIP_STATUS = {"Out", "Doubtful", "Suspension"}


def norm(name):
    s = re.sub(r"[.'’\-]", "", name.lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def nick(team_name):
    return team_name.split()[-1].lower()


# ---------------- grading (free, from ESPN box scores) ----------------
def grade(book):
    """Settle player-prop legs from box scores. Returns settled bets."""
    cut = store.now() - timedelta(hours=config.GRADE_AFTER_H)
    void_cut = store.now() - timedelta(days=config.VOID_AFTER_D)
    boxes, settled = {}, []
    for b in bk.open_bets(book):
        for l in b["legs"]:
            if l["result"] is not None or not str(l.get("market", "")).startswith("player_"):
                continue
            if store.parse(l["commence"]) > cut:
                continue
            eid = l.get("espn_id")
            if eid and eid not in boxes:
                try:
                    boxes[eid] = nd.parse_summary(eid)
                except Exception as e:
                    print(f"[props] box {eid} failed: {e}")
                    boxes[eid] = None
            g = boxes.get(eid)
            if g:
                tm = list(g["teams"].values())
                hh = next((t for t in tm if t["home"]), None)
                aa = next((t for t in tm if not t["home"]), None)
                if hh and aa:
                    l["final"] = {"home": hh["score"], "away": aa["score"]}
                row = next((p for p in g["players"] if p[nd.P_ID] == l.get("player_id")), None)
                if row is None:
                    row = next((p for p in g["players"] if norm(p[nd.P_NAME]) == norm(l.get("player", ""))), None)
                if row is None:
                    l["result"] = "void"          # did not play: books void the bet
                else:
                    v = nm._val(row, l["stat"])
                    l["actual"] = v
                    l["box"] = {"min": row[nd.P_MIN], "pts": row[nd.P_PTS], "reb": row[nd.P_REB], "ast": row[nd.P_AST],
                                "tpm": row[nd.P_TPM], "tpa": row[nd.P_TPA], "fga": row[nd.P_FGA], "fta": row[nd.P_FTA],
                                "tov": row[nd.P_TOV]}
                    over = l["side"] == "Over"
                    diff = (v - l["point"]) if over else (l["point"] - v)
                    l["result"] = "win" if diff > 0 else "loss" if diff < 0 else "push"
            elif store.parse(l["commence"]) < void_cut:
                l["result"] = "void"
        if bk.settle(book, b):
            settled.append(b)
    return settled


# ---------------- pricing ----------------
def parse_props(ev_odds):
    """{(player_norm, market, point): {"name", "books": {book: {"Over": p, "Under": p}}}}"""
    out = {}
    for bkm in ev_odds.get("bookmakers", []):
        for m in bkm.get("markets", []):
            for o in m.get("outcomes", []):
                who, side, pt, price = o.get("description"), o.get("name"), o.get("point"), o.get("price")
                if not who or side not in ("Over", "Under") or pt is None or not price or price <= 1:
                    continue
                k = (norm(who), m["key"], float(pt))
                e = out.setdefault(k, {"name": who, "books": {}})
                e["books"].setdefault(bkm["key"], {})[side] = price
    return out


def price_line(entry):
    """Market no-vig over probability and the best price on each side."""
    fair = []
    best = {"Over": (0, None), "Under": (0, None)}
    for bkey, sides in entry["books"].items():
        for side, pr in sides.items():
            if pr > best[side][0]:
                best[side] = (pr, bkey)
        if "Over" in sides and "Under" in sides:
            io, iu = 1 / sides["Over"], 1 / sides["Under"]
            fair.append(io / (io + iu))
    if fair:
        p_mkt = sum(fair) / len(fair)
    elif best["Over"][0]:
        p_mkt = (1 / best["Over"][0]) / 1.045       # one-sided: strip a typical vig
    else:
        p_mkt = 1 - (1 / best["Under"][0]) / 1.045
    return p_mkt, best, len(entry["books"])


# ---------------- the run ----------------
def run(state, manual, client, budget):
    p = state["params"]
    today = store.now().astimezone(nd.ET).date()
    if not store.os.environ.get("PERSEVERANCE_NOW"):
        nd.update(log=lambda m: None)
    model = nm.Model(nd.season_of(today))
    games = [g for g in nd.slate(today) if store.parse(g["start"]) > store.now() + timedelta(minutes=15)]
    if not games:
        return {"games": [], "spots": [], "note": "No NBA games left to bet today."}
    inj = nd.injuries()
    W = {k: p[k] for k in ("w_dvp", "w_sim", "w_h2h", "matchup_strength")}

    # 1) project every rotation player on the slate (free)
    roster = defaultdict(list)
    for pid, pl in model.players.items():
        roster[pl["team"]].append(pid)
    spots, by_game = [], defaultdict(list)
    for g in games:
        for team, opp, home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
            for pid in roster.get(team, []):
                pl = model.players[pid]
                if pl["games"] < 8 or inj.get(pid) in SKIP_STATUS:
                    continue
                if model.minutes(pid) < MIN_MINUTES:
                    continue
                last = model.by_player[pid][-1]["d"]
                if (today - datetime.fromisoformat(last).date()).days > 25:
                    continue                               # hasn't played lately
                for s in nm.STATS:
                    pr = model.project(pid, s, opp, home, W)
                    if pr["base"] < (0.8 if s == "tpm" else 2.0):
                        continue
                    pr.update(game=g["espn_id"], status=inj.get(pid, ""), strength=abs(math.log(pr["mult"])))
                    spots.append(pr)
                    by_game[g["espn_id"]].append(pr)
    spots.sort(key=lambda x: -x["strength"])

    # 2) map ESPN games to Odds API events (free) and choose which to price
    try:
        evs = client.events(SPORT)
    except Exception as e:
        evs = []
        brain.note(state, "error", f"Couldn't list NBA events: {e}")
    ev_by_pair = {frozenset((nick(e["home_team"]), nick(e["away_team"]))): e for e in evs}
    plan = []
    for g in games:
        e = ev_by_pair.get(frozenset((g["home_nick"].lower(), g["away_nick"].lower())))
        if not e:
            continue
        top = sorted(by_game[g["espn_id"]], key=lambda x: -x["strength"])[:8]
        if not top:
            continue
        stats = []
        for x in top:
            if x["stat"] not in stats:
                stats.append(x["stat"])
        plan.append({"g": g, "e": e, "score": sum(x["strength"] for x in top[:5]), "stats": stats[:3]})
    plan.sort(key=lambda x: -x["score"])

    lines, bought = {}, []
    if p.get("props_enabled", True):
        for item in plan:
            left = budget.allowance - budget.spent_this_run
            if left < 1:
                break
            stats = item["stats"][:max(1, min(len(item["stats"]), left))]
            markets = [nm.ODDS_KEY[s] for s in stats]
            if not budget.can(len(markets)):
                continue
            try:
                od = client.event_odds(SPORT, item["e"]["id"], markets)
            except Exception as ex:
                brain.note(state, "error", f"Prop odds failed for {item['e']['away_team']} @ {item['e']['home_team']}: {ex}")
                if "422" in str(ex) or "401" in str(ex):
                    break
                continue
            lines[item["g"]["espn_id"]] = (item["e"], parse_props(od))
            bought.append(f"{item['g']['away']}@{item['g']['home']} ({', '.join(stats)})")

    # 3) price every line we paid for
    key_stat = {v: k for k, v in nm.ODDS_KEY.items()}
    cands = []
    for eid, (e, props) in lines.items():
        g = next(x for x in games if x["espn_id"] == eid)
        projs = {(norm(x["name"]), x["stat"]): x for x in by_game[eid]}
        for (who, mkey, pt), entry in props.items():
            s = key_stat.get(mkey)
            pr = projs.get((who, s))
            if not pr:
                continue
            p_model = nm.p_over(pr, pt)
            p_mkt, best, nbooks = price_line(entry)
            w = p["prop_model_weight"]
            p_fin = w * p_model + (1 - w) * p_mkt
            for side, pf, pm in (("Over", p_fin, p_model), ("Under", 1 - p_fin, 1 - p_model)):
                price, bkey = best[side]
                if not price:
                    continue
                ev = pf * price - 1
                agrees = (pr["proj"] > pt) if side == "Over" else (pr["proj"] < pt)
                cands.append({"pr": pr, "g": g, "e": e, "side": side, "point": pt, "price": price, "book": bkey,
                              "p": pf, "p_model": pm, "p_mkt": p_mkt if side == "Over" else 1 - p_mkt, "ev": ev,
                              "agrees": agrees, "nbooks": nbooks, "market": mkey})
    cands.sort(key=lambda c: -c["ev"])

    # 4) bet: matchup must point the same way as the bet, edge must clear the bar
    placed, used = [], {l.get("player_id") for b in bk.open_bets(state["book"]) for l in b["legs"]}
    for c in cands:
        if len(placed) >= p["prop_max_bets"]:
            break
        pr = c["pr"]
        need = p["prop_min_edge"] * (1.5 if pr["status"] == "Questionable" else 1)
        if not c["agrees"] or c["ev"] < need or c["ev"] > config.MAX_EV or pr["pid"] in used:
            continue
        pb = "matchup_over" if c["side"] == "Over" else "matchup_under"
        arm = state["arms"].setdefault(pb, brain._blank())
        if brain.benched(arm) and __import__("random").random() > 0.15:
            continue
        stake = brain.size(state, c["p"], c["price"], brain.arm_weight(arm))
        if stake < config.MIN_STAKE:
            continue
        leg = {"event_id": c["e"]["id"], "sport": SPORT, "commence": c["e"]["commence_time"],
               "home": c["e"]["home_team"], "away": c["e"]["away_team"], "market": c["market"],
               "outcome": f"{pr['name']} {c['side']}", "side": c["side"], "player": pr["name"],
               "player_id": pr["pid"], "stat": pr["stat"], "espn_id": c["g"]["espn_id"], "point": c["point"],
               "price": round(c["price"], 3), "book": c["book"], "fair_p": round(c["p_mkt"], 4),
               "close_p": None, "result": None,
               "model": {"proj": pr["proj"], "base": pr["base"], "minutes": pr["minutes"], "mult": pr["mult"],
                         "sd": round(math.sqrt(max(pr.get("var") or 0, 0)), 2), "p_model": round(c["p_model"], 4),
                         "p_mkt": round(c["p_mkt"], 4),
                         "signals": [{"k": sg["k"], "v": sg.get("v"), "txt": sg["txt"]} for sg in pr["signals"] if sg["k"] != "ha"][:4]}}
        why = " · ".join(sg["txt"] for sg in pr["signals"][:3])
        b = bk.place(state["book"], [leg], stake, pb, ev=c["ev"], p=c["p"],
                     note=f"Proj {pr['proj']:.1f} vs line {c['point']:g} (norm {pr['base']:.1f}, {pr['minutes']:.0f} min). "
                          f"Model {c['p_model']:.0%} vs market {c['p_mkt']:.0%}. {why}")
        if b:
            placed.append(b)
            used.add(pr["pid"])

    # 5) the matchup board for the dashboard
    priced = {}
    for c in cands:
        k = (c["pr"]["pid"], c["pr"]["stat"])
        if k not in priced or c["ev"] > priced[k]["ev"]:
            priced[k] = c
    board = []
    for pr in spots[:60]:
        c = priced.get((pr["pid"], pr["stat"]))
        g = next(x for x in games if x["espn_id"] == pr["game"])
        item = {"pid": pr["pid"], "name": pr["name"], "team": pr["team"], "opp": pr["opp"], "home": pr["home"],
                "role": pr["role"], "stat": pr["stat"], "stat_name": nm.STATS[pr["stat"]], "proj": pr["proj"],
                "base": pr["base"], "mult": pr["mult"], "minutes": pr["minutes"], "status": pr["status"],
                "signals": pr["signals"], "start": g["start"], "espn_id": g["espn_id"],
                "lean": "Over" if pr["mult"] > 1 else "Under"}
        if c:
            item.update(line=c["point"], side=c["side"], price=round(c["price"], 3), am=market.to_american(c["price"]),
                        book=c["book"], ev=round(c["ev"], 4), p_model=round(c["p_model"], 3),
                        p_mkt=round(c["p_mkt"], 3), event_id=c["e"]["id"], market=c["market"])
        board.append(item)
    events_map = {}
    for eid, (e, _) in lines.items():
        events_map[e["id"]] = {"espn_id": eid, "commence": e["commence_time"], "home": e["home_team"], "away": e["away_team"]}
    for item in plan:
        events_map.setdefault(item["e"]["id"], {"espn_id": item["g"]["espn_id"], "commence": item["e"]["commence_time"],
                                                "home": item["e"]["home_team"], "away": item["e"]["away_team"]})
    players = {norm(model.players[x["pid"]]["name"]): x["pid"] for x in spots}
    store.save("props.json", {"updated": store.iso(store.now()), "date": today.isoformat(),
                              "games": [{"home": g["home"], "away": g["away"], "start": g["start"]} for g in games],
                              "spots": board, "events": events_map, "players": players,
                              "bought": bought, "teams": model.teams})
    return {"games": games, "spots": spots, "bought": bought, "placed": placed, "cands": len(cands)}
