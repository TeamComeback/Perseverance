"""Backtester (multi-agent framework, Nov 17 2026): uses the paid plan's historical odds to answer
"does the model beat the market?" with real closing lines, without waiting weeks for paper results.

Two jobs, most valuable first, inside its own allotment (config.BACKTEST_MONTHLY credits a billing cycle,
ledger in docs/data/backtest.json; historical odds cost 10 credits per market per snapshot):

  1. Closing prices for past bets. Every bet in both books that never got a closing price (the free key could only
     scan three times a day) gets one from the snapshot taken just before its tip. Stored in clv_backfill.json and
     merged into the books by the next run, so the record's CLV covers (nearly) every bet.
  2. Prop lean study. For every player prop line the model priced (lines.json), the closing line and no-vig price
     from the snapshot just before tip. Did the market move toward the model's side between when we saw the line and
     the close? What was the model side's closing-line value? By stat and by how strongly the model disagreed.
     Raw closes are kept permanently in data/backtest/prop-closes.json, so each game is bought once.

The report (docs/data/backtest.json) is shown on the dashboard under Model > Health."""
import json
from datetime import timedelta

from . import config, market, store
from . import nba_data as nd
from . import nba_model as nm

FILE = "backtest.json"
BACKFILL = "clv_backfill.json"
SPORT = "basketball_nba"
CLOSE_BEFORE_MIN = 3                       # the snapshot taken this long before tip is the close
PROP_CLOSES = config.ROOT / "data" / "backtest" / "prop-closes.json"


def ledger():
    return (store.load(FILE, {}) or {}).get("credits", {})


# ------------------------------------------------------------------ historical endpoints (10 credits/market/snapshot)
def _hist(client, path, params, cost):
    q = {**params, "oddsFormat": "decimal", "bookmakers": ",".join(config.ALLOWED_BOOKS)}
    import urllib.error
    try:
        return client._get(path, q, cost)
    except urllib.error.HTTPError as e:
        if e.code != 422:
            raise
        q.pop("bookmakers"); q["regions"] = config.REGIONS
        return client._get(path, q, cost)


def hist_odds(client, sport, at, markets):
    r = _hist(client, f"/historical/sports/{sport}/odds", {"date": store.iso(at), "markets": ",".join(markets)}, 10 * len(markets))
    return r.get("timestamp"), market_only(r.get("data") or [])


def hist_event_odds(client, sport, eid, at, markets):
    r = _hist(client, f"/historical/sports/{sport}/events/{eid}/odds", {"date": store.iso(at), "markets": ",".join(markets)},
              10 * len(markets))
    return r.get("timestamp"), market_only(r.get("data") or {})


def hist_events(client, sport, at):
    r = client._get(f"/historical/sports/{sport}/events", {"date": store.iso(at)}, 1)
    return r.get("data") or []


def market_only(data):
    from .odds import only_allowed
    return only_allowed(data)


def _nick(name):
    return (name or "").split()[-1].lower()


# ------------------------------------------------------------------ 1. closing prices for past bets
def backfill_bets(client, budget, books, log=print):
    out = store.load(BACKFILL, {}) or {}
    now = store.now()
    board, props = {}, {}
    for book in books:
        for b in book["bets"]:
            for i, l in enumerate(b["legs"]):
                if l.get("close_p") is not None or str(i) in out.get(b["id"], {}):
                    continue
                eid = l.get("event_id") or ""
                if not eid or ":" in eid or not l.get("commence"):
                    continue
                c = store.parse(l["commence"])
                if c > now - timedelta(minutes=10) or c < now - timedelta(days=330):
                    continue
                at = c - timedelta(minutes=CLOSE_BEFORE_MIN)
                if str(l.get("market", "")).startswith("player_"):
                    g = props.setdefault((eid, store.iso(at)), {"m": set(), "legs": []})
                    g["m"].add(l["market"]); g["legs"].append((b, i, l))
                else:
                    board.setdefault((l["sport"], store.iso(at), l["market"]), []).append((b, i, l))
    filled = tried = 0
    for (sport, at, mk), legs in sorted(board.items(), key=lambda x: x[0][1], reverse=True):
        if not budget.can(10):
            break
        tried += len(legs)
        try:
            ts, evs = hist_odds(client, sport, store.parse(at), [mk])
        except Exception as e:
            log(f"[backtest] board {sport} {at}: {e}")
            continue
        by_id = {e["id"]: e for e in evs}
        for b, i, l in legs:
            ev = by_id.get(l["event_id"])
            if not ev:
                continue
            for line in market.analyse_event(ev):
                if line["market"] != l["market"]:
                    continue
                for o in line["outcomes"]:
                    if o["name"] == l["outcome"] and o.get("desc") == l.get("team"):
                        if o["point"] == l["point"]:
                            out.setdefault(b["id"], {})[str(i)] = _close(o["all_fair"], ts, l)
                            filled += 1
                        elif o["point"] is not None and l.get("point") is not None:
                            out.setdefault(b["id"], {}).setdefault(str(i), {"close_line": o["point"], "src": "historical"})
    from . import props as P
    for (eid, at), g in sorted(props.items(), key=lambda x: x[0][1], reverse=True):
        cost = 10 * len(g["m"])
        if not budget.can(cost):
            break
        tried += len(g["legs"])
        try:
            ts, od = hist_event_odds(client, SPORT, eid, store.parse(at), sorted(g["m"]))
        except Exception as e:
            log(f"[backtest] props {eid} {at}: {e}")
            continue
        pp = P.parse_props(od)
        for b, i, l in g["legs"]:
            who = P.norm(l.get("player") or l.get("outcome", "").rsplit(" ", 1)[0])
            entry = pp.get((who, l["market"], float(l["point"])))
            if entry:
                p_over, _, _ = P.price_line(entry)
                out.setdefault(b["id"], {})[str(i)] = _close(p_over if l.get("side") == "Over" else 1 - p_over, ts, l)
                filled += 1
            else:
                alts = [pt for (w, m, pt) in pp if w == who and m == l["market"]]
                if alts:
                    out.setdefault(b["id"], {})[str(i)] = {"close_line": min(alts, key=lambda x: abs(x - l["point"])),
                                                           "src": "historical"}
    store.save(BACKFILL, out, compact=True)
    log(f"[backtest] closing prices: {filled} filled of {tried} tried")
    return filled


def _close(p, ts, l):
    mins = int((store.parse(l["commence"]) - store.parse(ts)).total_seconds() // 60) if ts else None
    return {"close_p": round(p, 4), "close_at": ts, "close_mins": mins, "src": "historical"}


def merge_backfill(books, state=None):
    """Called by every run: copy backfilled closing prices into legs that still have none. For the agent's bets that
    already settled (and were learned from without a closing price), the playbook's CLV is updated too."""
    bf = store.load(BACKFILL, {}) or {}
    if not bf:
        return 0
    n = 0
    learned = set(state.get("learned_ids") or []) if state else set()
    for book in books:
        for b in book["bets"]:
            got = bf.get(b["id"])
            if not got:
                continue
            for i, l in enumerate(b["legs"]):
                x = got.get(str(i))
                if not x:
                    continue
                if x.get("close_p") is not None and l.get("close_p") is None:
                    l.update(close_p=x["close_p"], close_at=x.get("close_at"), close_mins=x.get("close_mins"), close_src="historical")
                    n += 1
                    if state and book is state["book"] and b["id"] in learned:
                        a = state["arms"].get(b["playbook"])
                        if a is not None:
                            a["clv_n"] = a.get("clv_n", 0) + 1
                            a["clv_sum"] = a.get("clv_sum", 0) + l["price"] * l["close_p"] - 1
                if x.get("close_line") is not None and l.get("close_line") is None:
                    l["close_line"] = x["close_line"]
    return n


# ------------------------------------------------------------------ 2. prop lean study
def _espn_games():
    out = {}
    for s in (nd.season_of(store.now().astimezone(nd.ET).date()) - 1, nd.season_of(store.now().astimezone(nd.ET).date())):
        try:
            for _s, gid, g in nd.all_games([s]):
                out[str(gid)] = g
        except Exception:
            pass
    return out


def prop_study(client, budget, log=print, max_games=None):
    ldb = store.load("lines.json", {"entries": {}}).get("entries", {})
    PROP_CLOSES.parent.mkdir(parents=True, exist_ok=True)
    try:
        closes = json.loads(PROP_CLOSES.read_text())
    except Exception:
        closes = {"games": {}}
    games = {}
    now = store.now()
    for k, e in ldb.items():
        if not e.get("espn_id") or not e.get("start") or e.get("line") is None:
            continue
        st = store.parse(e["start"])
        if st > now - timedelta(minutes=10):
            continue
        g = games.setdefault(e["espn_id"], {"start": e["start"], "date": e["date"], "stats": set()})
        g["stats"].add(e["stat"])
    espn = _espn_games()
    ev_cache = {}
    bought = 0
    todo = sorted((gid for gid in games if gid not in closes["games"]), key=lambda g: games[g]["start"], reverse=True)
    for gid in todo:
        if max_games is not None and bought >= max_games:
            break
        g = games[gid]
        mk = sorted(nm.ODDS_KEY[s] for s in g["stats"] if s in nm.ODDS_KEY)
        if not mk or not budget.can(10 * len(mk) + 1):
            break
        eg = espn.get(str(gid))
        if not eg:
            closes["games"][gid] = {"skip": "no ESPN game"}
            continue
        home = next((t["name"] for t in eg["teams"].values() if t.get("home")), None)
        away = next((t["name"] for t in eg["teams"].values() if not t.get("home")), None)
        st = store.parse(g["start"])
        day_key = g["date"]
        if day_key not in ev_cache:
            try:
                ev_cache[day_key] = hist_events(client, SPORT, st - timedelta(hours=6))
            except Exception as e:
                log(f"[backtest] events {day_key}: {e}")
                ev_cache[day_key] = []
        ev = next((x for x in ev_cache[day_key] if _nick(x["home_team"]) == _nick(home) and _nick(x["away_team"]) == _nick(away)), None)
        if not ev:
            closes["games"][gid] = {"skip": "no matching odds event"}
            continue
        try:
            ts, od = hist_event_odds(client, SPORT, ev["id"], st - timedelta(minutes=CLOSE_BEFORE_MIN), mk)
        except Exception as e:
            log(f"[backtest] prop closes {gid}: {e}")
            continue
        from . import props as P
        pp = P.parse_props(od)
        lines = {}
        for (who, m, pt), entry in pp.items():
            p_over, best, n = P.price_line(entry)
            lines[f"{who}|{m}|{pt:g}"] = [round(p_over, 4), n]
        closes["games"][gid] = {"eid": ev["id"], "ts": ts, "start": g["start"], "lines": lines}
        bought += 1
    PROP_CLOSES.write_text(json.dumps(closes, separators=(",", ":")))
    log(f"[backtest] prop closes: {bought} games bought, {len(closes['games'])} on file")
    return closes, ldb


def analyze_props(closes, ldb):
    from . import props as P
    rows = []
    for k, e in ldb.items():
        g = closes["games"].get(e.get("espn_id") or "")
        if not g or "lines" not in g or e.get("line") is None or e.get("p_over") is None or e.get("p_model") is None:
            continue
        mk = nm.ODDS_KEY.get(e["stat"])
        who = P.norm(e["name"])
        x = g["lines"].get(f"{who}|{mk}|{float(e['line']):g}")
        alts = [float(kk.split("|")[2]) for kk in g["lines"] if kk.startswith(f"{who}|{mk}|")]
        side = "Over" if e["p_model"] > e["p_over"] else "Under"
        edge = abs(e["p_model"] - e["p_over"])
        r = {"stat": e["stat"], "side": side, "edge": edge, "result": e.get("result"), "cv": e.get("cv")}
        if x:
            pc = x[0] if side == "Over" else 1 - x[0]
            ps = e["p_over"] if side == "Over" else 1 - e["p_over"]
            price = e.get("over") if side == "Over" else e.get("under")
            r.update(same=True, moved=pc - ps, clv=(price * pc - 1) if price else None)
        elif alts:
            cl = min(alts, key=lambda a: abs(a - e["line"]))
            toward = (cl > e["line"]) if side == "Over" else (cl < e["line"])
            r.update(same=False, line_move=cl - e["line"], toward=toward)
        else:
            continue
        rows.append(r)

    def summ(rs):
        same = [r for r in rs if r.get("same")]
        mv = [r for r in rs if not r.get("same")]
        clvs = [r["clv"] for r in same if r.get("clv") is not None]
        moved = [r for r in same if abs(r["moved"]) >= 0.005]
        toward = sum(1 for r in moved if r["moved"] > 0) + sum(1 for r in mv if r["toward"])
        n_dir = len(moved) + len(mv)
        res = [r for r in rs if r.get("result") in ("over", "under")]
        hit = sum(1 for r in res if r["result"] == r["side"].lower())
        return {"n": len(rs), "clv_n": len(clvs), "clv_avg": round(sum(clvs) / len(clvs), 4) if clvs else None,
                "clv_pos": round(sum(1 for c in clvs if c > 0) / len(clvs), 3) if clvs else None,
                "moved_n": n_dir, "toward_model": round(toward / n_dir, 3) if n_dir else None,
                "line_moves": len(mv), "hit_n": len(res), "hit": round(hit / len(res), 3) if res else None}
    by_stat = {}
    for r in rows:
        by_stat.setdefault(r["stat"], []).append(r)
    buckets = (("under 3%", 0, 0.03), ("3-6%", 0.03, 0.06), ("6-10%", 0.06, 0.10), ("10%+", 0.10, 9))
    return {"all": summ(rows),
            "by_stat": {s: summ(v) for s, v in sorted(by_stat.items())},
            "by_edge": {name: summ([r for r in rows if lo <= r["edge"] < hi]) for name, lo, hi in buckets}}


# ------------------------------------------------------------------ bets CLV report
def bets_report(state):
    legs = [(b, l) for b in state["book"]["bets"] for l in b["legs"] if b["status"] in ("won", "lost", "push")]
    have = [(b, l) for b, l in legs if l.get("close_p")]
    clv = [l["price"] * l["close_p"] - 1 for b, l in have]
    by = {}
    for (b, l), c in zip(have, clv):
        by.setdefault(b["playbook"], []).append(c)
    return {"settled_legs": len(legs), "with_close": len(have),
            "clv_avg": round(sum(clv) / len(clv), 4) if clv else None,
            "clv_pos": round(sum(1 for c in clv if c > 0) / len(clv), 3) if clv else None,
            "by_playbook": {k: {"n": len(v), "clv_avg": round(sum(v) / len(v), 4)} for k, v in sorted(by.items())}}


def verdict(rep):
    out = []
    b = rep.get("bets") or {}
    if b.get("clv_avg") is not None:
        out.append(f"Agent's bets: {b['with_close']} of {b['settled_legs']} settled legs have a closing price; average CLV "
                   f"{b['clv_avg'] * 100:+.1f}% ({'beating' if b['clv_avg'] > 0 else 'behind'} the close).")
    p = (rep.get("props") or {}).get("all") or {}
    if p.get("clv_n"):
        out.append(f"Prop model side, {p['clv_n']} lines: CLV {p['clv_avg'] * 100:+.1f}% on average, positive on "
                   f"{p['clv_pos'] * 100:.0f}%; the market moved toward the model {p['toward_model'] * 100:.0f}% of the time "
                   f"it moved ({p['moved_n']} lines). Above 50% means the model sees moves before the market makes them.")
        best = [(s, v) for s, v in (rep["props"].get("by_stat") or {}).items() if v.get("clv_n", 0) >= 20 and v.get("clv_avg") is not None]
        if best:
            s, v = max(best, key=lambda x: x[1]["clv_avg"])
            w, wv = min(best, key=lambda x: x[1]["clv_avg"])
            out.append(f"Best stat by CLV: {s} ({v['clv_avg'] * 100:+.1f}%, {v['clv_n']} lines); worst: {w} ({wv['clv_avg'] * 100:+.1f}%).")
    if not out:
        out.append("Not enough closing data yet.")
    return out


def run(client, budget, state, manual, log=print):
    """One Backtester pass. Spends only from its own allotment."""
    t0 = budget.spent_this_run
    backfill_bets(client, budget, [state["book"], manual["book"]], log=log)
    merge_backfill([state["book"], manual["book"]], state)
    closes, ldb = prop_study(client, budget, log=log)
    rep = store.load(FILE, {}) or {}
    cyc = budget.b["month"]
    cred = rep.get("credits", {})
    cred[cyc] = cred.get(cyc, 0) + (budget.spent_this_run - t0)
    rep.update(updated=store.iso(store.now()), credits=cred, cap=config.BACKTEST_MONTHLY,
               bets=bets_report(state), props=analyze_props(closes, ldb),
               prop_games=sum(1 for g in closes["games"].values() if "lines" in g))
    rep["verdict"] = verdict(rep)
    store.save(FILE, rep)
    log("[backtest] " + " ".join(rep["verdict"]))
    return rep
