"""DraftKings lines, free from ESPN's odds feed, kept per day in docs/data/dk/YYYY-MM-DD.json:

  games   opening and closing spread/total/moneyline, then live snapshots through the game (score, clock,
          DraftKings' live line, and the sim's chance of each live bet from that exact score and clock)
  props   DraftKings' opening and closing line for every player prop the model projects, the model's own
          probability at the closing line, and (after the game) what the player scored

The next morning the live snapshots are graded: would each live bet have won, and did it have an edge?
Results by situation go to live_track.json. Props feed the model-vs-market scoreboard."""
import json
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import config, store
from . import espn_odds as eo
from . import nba_data as nd
from . import nba_model as nm

ET = ZoneInfo("America/New_York")      # real Eastern time (handles daylight saving)
DIR = "dk"
KEEP_DAYS = 45


def path(day):
    return f"{DIR}/{day}.json"


def load(day):
    return store.load(path(day), {"date": day, "games": {}, "props": {}})


def save(day, db):
    (config.DATA / DIR).mkdir(parents=True, exist_ok=True)
    db["updated"] = store.iso(store.now())
    store.save(path(day), db, compact=True)


def _clock_sec(s):
    s = str(s or "0")
    try:
        if ":" in s:
            m, x = s.split(":")
            return int(m) * 60 + float(x)
        return float(s)
    except ValueError:
        return 0.0


def refresh(ctx, players, log=print):
    """Pre-game: opening and closing game lines and props for every game still to tip off."""
    day = ctx["day"].isoformat()
    db = load(day)
    entries = {e["pid"]: e for e in ctx["entries"]}
    n_props = 0
    for g in ctx["games"]:
        eid = g["espn_id"]
        G = db["games"].setdefault(eid, {"home": g["home"], "away": g["away"], "home_name": g.get("home_name"),
                                         "away_name": g.get("away_name"), "start": g["start"], "live": [], "final": None})
        if g.get("state") != "pre":
            continue
        try:
            o = eo.game_odds(eid)
        except Exception as e:
            log(f"[dk] odds {eid} failed: {e}")
            o = None
        if o:
            G["open"] = o.get("open") or G.get("open")
            if o.get("current"):
                G["close"], G["close_at"] = o["current"], store.iso(store.now())
        try:
            pr = eo.props(eid)
        except Exception as e:
            log(f"[dk] props {eid} failed: {e}")
            pr = {}
        for (pid, st), v in pr.items():
            e = entries.get(pid)
            if not e or e.get("out") or st not in e["proj"] or v["cur"][0] is None:
                continue
            line = v["cur"][0]
            proj = e["proj"][st]
            cv = ((players.get(pid) or {}).get("cv") or {}).get(st)
            rec = db["props"].setdefault(f"{pid}|{st}", {"pid": pid, "name": e["name"], "team": e["team"], "opp": e["opp"],
                                                        "espn_id": eid, "stat": st, "actual": None, "res": None})
            rec.update(o=v["open"], c=v["cur"], pm=round(nm.p_over(proj, line), 4), lean="Over" if proj["mult"] > 1 else "Under",
                       cv=cv[2] if cv else None, proj=round(proj["proj"], 1))
            n_props += 1
    save(day, db)
    log(f"[dk] {day}: lines for {sum(1 for G in db['games'].values() if G.get('close'))} games, {n_props} props")
    return db


def live_snapshot(log=print):
    """During games: record DraftKings' live line with the score and clock. Covers today and last night
    (games past midnight). Returns the days touched."""
    now = store.now().astimezone(ET)
    touched = []
    for d in {now.date(), (now - timedelta(hours=8)).date()}:
        day = d.isoformat()
        db = store.load(path(day), None)
        if not db or not db.get("games"):
            continue
        try:
            sb = {x["espn_id"]: x for x in eo.live_games(d.strftime("%Y%m%d"))}
        except Exception as e:
            log(f"[dk] scoreboard {day} failed: {e}")
            continue
        changed = False
        for eid, G in db["games"].items():
            x = sb.get(eid)
            if not x or G.get("final"):
                continue
            if x["state"] == "post":
                G["final"] = [x["away_score"], x["home_score"]]
                changed = True
                continue
            if x["state"] != "in":
                continue
            try:
                o = eo.game_odds(eid, live=True)
            except Exception as e:
                log(f"[dk] live odds {eid} failed: {e}")
                continue
            cur = (o or {}).get("current")
            snap = {"ts": store.iso(store.now()), "period": x["period"], "clock": _clock_sec(x["clock"]), "detail": x["detail"],
                    "score": [x["away_score"], x["home_score"]], "line": cur}
            last = G["live"][-1] if G["live"] else None
            if last and last["period"] == snap["period"] and last["clock"] == snap["clock"] and last["score"] == snap["score"]:
                continue
            G["live"].append(snap)
            changed = True
        if changed:
            save(day, db)
            touched.append(day)
    return touched


def checkpoint(s):
    """Which part of the game a live snapshot was taken in."""
    p, c, det = s["period"], s["clock"], (s.get("detail") or "").lower()
    if "half" in det or (p == 2 and c <= 60) or (p == 3 and c >= 660):
        return "half"
    if (p == 1 and c <= 60) or (p == 2 and c >= 660):
        return "q1"
    if (p == 3 and c <= 60) or (p == 4 and c >= 660):
        return "q3"
    return f"q{min(p, 4)}"


def _settle(margin_or_diff):
    return "won" if margin_or_diff > 0 else "lost" if margin_or_diff < 0 else "push"


def _clock_lbl(s):
    p, c = s.get("period") or 0, int(float(s.get("clock") or 0))
    return f"{'Q' + str(p) if p <= 4 else 'OT' + (str(p - 4) if p > 5 else '')} {c // 60}:{c % 60:02d}"


def recent_live_bets(days=KEEP_DAYS, keep=400):
    """Every graded live bet that fit a tracked situation (sim saw a 5%+ edge, or a team trailing by 10+), one row each,
    newest first: when it was taken (time and game clock), the score, the line and price, the sim's chance, the result."""
    root = config.DATA / DIR
    out = []
    if not root.exists():
        return out
    for f in sorted(root.glob("*.json"), reverse=True)[:days]:
        try:
            db = json.loads(f.read_text())
        except Exception:
            continue
        for eid, G in (db.get("games") or {}).items():
            if not G.get("final"):
                continue
            fa, fh = G["final"]
            for s in G.get("live", []):
                L, sim, res = s.get("line") or {}, s.get("sim") or {}, s.get("res") or {}
                if not res or res.get("stale"):
                    continue
                a, h = s["score"]
                trail = h - a
                sp = L.get("sp") or 0
                cand = [("spread_home", f"{G['home']} {sp:+g}", L.get("sp_h"), sim.get("p_home_cover")),
                        ("spread_away", f"{G['away']} {-sp:+g}", L.get("sp_a"), None if sim.get("p_home_cover") is None else 1 - sim["p_home_cover"]),
                        ("over", f"Over {L.get('tot')}", L.get("ov"), sim.get("p_over")),
                        ("under", f"Under {L.get('tot')}", L.get("un"), None if sim.get("p_over") is None else 1 - sim["p_over"]),
                        ("ml_home", f"{G['home']} ML", L.get("ml_h"), sim.get("p_home_win")),
                        ("ml_away", f"{G['away']} ML", L.get("ml_a"), None if sim.get("p_home_win") is None else 1 - sim["p_home_win"])]
                for name, label, price, pr in cand:
                    if name not in res or not price:
                        continue
                    ev = pr * price - 1 if pr is not None else None
                    sits = []
                    if ev is not None and ev >= 0.05:
                        sits.append("sim_edge")
                    trailer = (name.endswith("home") and trail < 0) or (name.endswith("away") and trail > 0)
                    if abs(trail) >= 10 and trailer and name.startswith(("spread", "ml")):
                        sits.append("trailing10")
                    if not sits:
                        continue
                    out.append({"day": f.stem, "ts": s.get("ts"), "clock": _clock_lbl(s), "cp": checkpoint(s),
                                "game": f"{G['away']} @ {G['home']}", "score": [a, h], "bet": label, "mk": name.split("_")[0],
                                "dec": price, "p": round(pr, 3) if pr is not None else None, "ev": round(ev, 3) if ev is not None else None,
                                "res": res[name], "sits": sits, "final": [fa, fh]})
    out.sort(key=lambda r: (r["day"], r["ts"] or ""), reverse=True)
    return out[:keep]


def grade(log=print):
    """After the games: grade props (box scores) and every live snapshot. Returns games graded."""
    lt = store.load("live_track.json", {"agg": {}, "graded": []})
    agg, done = lt["agg"], 0
    today = store.now().astimezone(ET).date()
    for back in range(1, 5):
        day = (today - timedelta(days=back)).isoformat()
        db = store.load(path(day), None)
        if not db or db.get("graded"):
            continue
        boxes = {}
        all_done = True
        for eid, G in db["games"].items():
            if eid not in boxes:
                try:
                    boxes[eid] = nd.parse_summary(eid)
                except Exception:
                    boxes[eid] = None
            box = boxes[eid]
            if not box:
                all_done = False
                continue
            sc = {("home" if t["home"] else "away"): t["score"] for t in box["teams"].values()}
            G["final"] = [sc.get("away"), sc.get("home")]
            fa, fh = G["final"]
            if G.get("open") and G.get("close"):
                G["move"] = {"sp": (G["close"].get("sp") or 0) - (G["open"].get("sp") or 0),
                             "tot": (G["close"].get("tot") or 0) - (G["open"].get("tot") or 0)}
            for s in G.get("live", []):
                L, sim = s.get("line") or {}, s.get("sim") or {}
                if sim.get("stale"):
                    s["res"] = {"stale": True}                 # ESPN's line had fallen behind DraftKings': not a real price, not graded
                    continue
                a, h = s["score"]
                cp = checkpoint(s)
                trail = h - a                                   # + home leads
                sit = "trail10" if abs(trail) >= 10 else "trail5" if abs(trail) >= 5 else "close"
                bets = []
                if L.get("sp") is not None and L.get("sp_h") and L.get("sp_a"):
                    hm = fh + L["sp"] - fa
                    bets.append(("spread_home", _settle(hm), L["sp_h"], sim.get("p_home_cover")))
                    bets.append(("spread_away", _settle(-hm), L["sp_a"], None if sim.get("p_home_cover") is None else 1 - sim["p_home_cover"]))
                if L.get("tot") is not None and L.get("ov") and L.get("un"):
                    d = fh + fa - L["tot"]
                    bets.append(("over", _settle(d), L["ov"], sim.get("p_over")))
                    bets.append(("under", _settle(-d), L["un"], None if sim.get("p_over") is None else 1 - sim["p_over"]))
                if L.get("ml_h") and L.get("ml_a"):
                    bets.append(("ml_home", _settle(fh - fa), L["ml_h"], sim.get("p_home_win")))
                    bets.append(("ml_away", _settle(fa - fh), L["ml_a"], None if sim.get("p_home_win") is None else 1 - sim["p_home_win"]))
                s["res"] = {b[0]: b[1] for b in bets}
                for name, res, price, p in bets:
                    units = (price - 1) if res == "won" else -1.0 if res == "lost" else 0.0
                    ev = p * price - 1 if p is not None else None
                    keys = []
                    if ev is not None and ev >= 0.05:
                        keys.append(f"sim_edge|{cp}|{name.split('_')[0]}")
                    is_trailer = (name.endswith("home") and trail < 0) or (name.endswith("away") and trail > 0)
                    if sit == "trail10" and is_trailer and name.startswith(("spread", "ml")):
                        keys.append(f"trailing10|{cp}|{name.split('_')[0]}")
                    for k in keys:
                        t = agg.setdefault(k, [0, 0, 0, 0, 0.0, 0.0])
                        t[0] += 1
                        t[1 if res == "won" else 2 if res == "lost" else 3] += 1
                        t[4] = round(t[4] + units, 3)
                        t[5] = round(t[5] + (ev or 0), 3)
            done += 1
        # props: what each player scored
        for k, r in db.get("props", {}).items():
            box = boxes.get(r["espn_id"])
            if not box or r.get("actual") is not None:
                continue
            row = next((p for p in box["players"] if p[nd.P_ID] == r["pid"]), None)
            if row is None:
                r["res"] = "dnp"
                continue
            v = nm._val(row, r["stat"])
            r["actual"] = v
            r["res"] = "over" if v > r["c"][0] else "under" if v < r["c"][0] else "push"
        if all_done:
            db["graded"] = True
        save(day, db)
    try:
        lt["bets"] = recent_live_bets()
    except Exception as e:
        log(f"[dk] live bet list failed: {e}")
    lt["updated"] = store.iso(store.now())
    store.save("live_track.json", lt)
    prune()
    return done


def prune():
    root = config.DATA / DIR
    if not root.exists():
        return
    cut = (store.now() - timedelta(days=KEEP_DAYS)).date().isoformat()
    for f in root.glob("*.json"):
        if f.stem < cut:
            f.unlink()


def scoreboard_rows(days=45):
    """(model p over, DraftKings no-vig close p over, outcome, stat, conviction) for graded props."""
    rows = []
    root = config.DATA / DIR
    if not root.exists():
        return rows
    for f in sorted(root.glob("*.json"))[-days:]:
        db = store.load(f"{DIR}/{f.name}", {})
        for r in (db.get("props") or {}).values():
            if r.get("res") not in ("over", "under") or r.get("pm") is None or not r["c"][1] or not r["c"][2]:
                continue
            io, iu = 1 / r["c"][1], 1 / r["c"][2]
            rows.append((r["pm"], io / (io + iu), 1 if r["res"] == "over" else 0, r["stat"], r.get("cv")))
    return rows


def recent_index(players, days=30, per=15):
    """DraftKings line history for tonight's players (opening, closing, what he scored) for the player card."""
    want = set(players)
    out = {}
    root = config.DATA / DIR
    if root.exists():
        for f in sorted(root.glob("*.json"))[-days:]:
            db = store.load(f"{DIR}/{f.name}", {})
            for r in (db.get("props") or {}).values():
                if r["pid"] in want:
                    out.setdefault(f"{r['pid']}|{r['stat']}", []).append([f.stem, r["o"][0], r["c"][0], r["c"][1], r["c"][2], r.get("actual"), r.get("res"), r.get("opp")])
    store.save("dk_recent.json", {k: v[-per:] for k, v in out.items()}, compact=True)
