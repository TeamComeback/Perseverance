"""The agent's decision-making: what to scan, what to bet, how much, and how to adapt."""
import json
import random
import urllib.request

from . import book as bk
from . import config, market, store


def new_state():
    return {"book": bk.new_book("Perseverance"), "params": dict(config.DEFAULT_PARAMS),
            "arms": {k: _blank() for k in config.PLAYBOOKS}, "sports": {},
            "journal": [], "learned_ids": [], "strategist": None}


def _blank():
    return {"bets": 0, "staked": 0.0, "profit": 0.0, "clv_n": 0, "clv_sum": 0.0}


def note(state, kind, text):
    state["journal"] = (state["journal"] + [{"ts": store.iso(store.now()), "kind": kind,
                                             "text": text}])[-300:]


# ---- Learning ---------------------------------------------------------------
def arm_weight(a):
    """>1 = lean in, <1 = pull back. Shrunk toward 1 until there's real sample size."""
    w = 1 + 2 * a["profit"] / (a["staked"] + 60)
    if a["clv_n"]:
        w += 3 * (a["clv_sum"] / a["clv_n"]) * min(1, a["clv_n"] / 20)
    return max(0.3, min(1.6, w))


def benched(a):
    return a.get("paused") or (a["bets"] >= 25 and arm_weight(a) <= 0.35)


def sport_weight(s):
    return max(0.4, min(1.5, 1 + 2 * s.get("profit", 0) / (s.get("staked", 0) + 60)))


def learn_from(state, settled):
    for b in settled:
        if b["id"] in state["learned_ids"]:
            continue
        state["learned_ids"] = (state["learned_ids"] + [b["id"]])[-3000:]
        a = state["arms"].setdefault(b["playbook"], _blank())
        a["bets"] += 1; a["staked"] += b["stake"]; a["profit"] += b["profit"]
        for l in b["legs"]:
            if l.get("close_p"):
                a["clv_n"] += 1; a["clv_sum"] += l["price"] * l["close_p"] - 1
        share = 1 / len(b["legs"])
        for l in b["legs"]:
            s = state["sports"].setdefault(l["sport"], {})
            s["bets"] = s.get("bets", 0) + share
            s["staked"] = s.get("staked", 0) + b["stake"] * share
            s["profit"] = s.get("profit", 0) + b["profit"] * share


def clamp(k, v):
    lo, hi = config.BOUNDS[k]
    v = max(lo, min(hi, v))
    return int(round(v)) if isinstance(config.DEFAULT_PARAMS[k], int) and not isinstance(
        config.DEFAULT_PARAMS[k], bool) else round(v, 4)


def adapt(state):
    """Self-tuning after every grading pass. Each change is written to the journal."""
    p, book = state["params"], state["book"]
    recent = [l for b in book["bets"][-80:] for l in b["legs"] if l.get("close_p")]
    if len(recent) >= 15:
        clv = sum(l["price"] * l["close_p"] - 1 for l in recent) / len(recent)
        if clv < 0 and p["min_edge"] < config.BOUNDS["min_edge"][1]:
            p["min_edge"] = clamp("min_edge", p["min_edge"] + 0.005)
            note(state, "adapt", f"Closing-line value is {clv:+.1%} over {len(recent)} legs, so the "
                                 f"market is beating my picks. Raising the minimum edge to {p['min_edge']:.1%}.")
        elif clv > 0.02 and p["min_edge"] > config.BOUNDS["min_edge"][0]:
            p["min_edge"] = clamp("min_edge", p["min_edge"] - 0.0025)
            note(state, "adapt", f"Beating the close by {clv:+.1%}. Lowering the minimum edge to "
                                 f"{p['min_edge']:.1%} to take more volume.")
    eq = bk.equity(book)
    dd = eq / book["peak"] if book["peak"] else 1
    if dd < 0.6 and p["kelly_mult"] > config.BOUNDS["kelly_mult"][0]:
        p["kelly_mult"] = clamp("kelly_mult", p["kelly_mult"] * 0.8)
        note(state, "adapt", f"Down {1 - dd:.0%} from the peak. Survival mode: Kelly fraction cut to "
                             f"{p['kelly_mult']:.2f}.")
    elif eq >= book["peak"] and eq > book["start"] * 1.25 and p["kelly_mult"] < 0.35:
        p["kelly_mult"] = clamp("kelly_mult", p["kelly_mult"] * 1.05)
        note(state, "adapt", f"New high at ${eq:.2f}. Pressing slightly: Kelly fraction "
                             f"{p['kelly_mult']:.2f}.")
    for name, a in state["arms"].items():
        if benched(a) and not a.get("bench_noted"):
            a["bench_noted"] = True
            note(state, "adapt", f"Benching the {name} playbook after {a['bets']} bets "
                                 f"(${a['profit']:+.2f}). It still gets the odd exploratory bet.")


def drought(state, scanned, placed):
    """If the bar is so high that nothing gets bet, the agent can't learn. Ease it down."""
    if not scanned:
        return
    state["dry_scans"] = 0 if placed else state.get("dry_scans", 0) + 1
    p = state["params"]
    if state["dry_scans"] >= 3 and p["min_edge"] > config.BOUNDS["min_edge"][0]:
        p["min_edge"] = clamp("min_edge", p["min_edge"] - 0.0025)
        state["dry_scans"] = 0
        note(state, "adapt", f"Three scans in a row with nothing worth betting. Easing the minimum "
                             f"edge to {p['min_edge']:.2%} so I get enough bets to learn from.")


# ---- Choosing what to scan --------------------------------------------------
def pick_sports(state, client, budget):
    """Free calls decide where to spend paid ones: which sports have games soon."""
    try:
        sports = [s for s in client.sports() if s.get("active") and not s.get("has_outrights")]
    except Exception as e:
        note(state, "error", f"Couldn't list sports: {e}")
        return []
    scored = []
    for s in sports:
        key = s["key"]
        base = config.SPORT_PRIORITY.get(key, config.DEFAULT_PRIORITY)
        if base < 0.45 and random.random() > 0.15:   # minor sports: occasional exploration
            continue
        try:
            evs = [e for e in client.events(key) if market.window_ok(e)]
        except Exception:
            continue
        if not evs:
            continue
        st = state["sports"].get(key, {})
        recency = 1.0
        if st.get("last_scanned"):
            hrs = (store.now() - store.parse(st["last_scanned"])).total_seconds() / 3600
            recency = min(1.3, 0.6 + hrs / 24)       # rotate the board
        score = base * sport_weight(st) * recency * min(1.5, 0.7 + len(evs) / 10)
        scored.append((score * random.uniform(0.9, 1.1), key, len(evs)))
    scored.sort(reverse=True)
    return scored


# ---- Placing bets -----------------------------------------------------------
def size(state, p, dec, weight, mult=1.0):
    pr, book = state["params"], state["book"]
    eq = bk.equity(book)
    raw = eq * pr["kelly_mult"] * weight * mult * market.kelly(p, dec)
    room = eq * pr["max_exposure_pct"] - sum(b["stake"] for b in bk.open_bets(book))
    if raw <= 0:
        return 0.0
    raw = max(raw, 1.0)   # books take $1 bets; a real edge shouldn't be skipped for being small
    return round(max(0, min(raw, eq * pr["max_bet_pct"], room, book["cash"])), 2)


def bet_on_board(state, events):
    pr, book = state["params"], state["book"]
    values, arbs = market.find_opportunities(events, pr)
    held = {(l["event_id"], l["market"]) for b in bk.open_bets(book) for l in b["legs"]}
    held_events = {l["event_id"] for b in bk.open_bets(book) for l in b["legs"]}
    placed = []

    # 1) Arbitrage: locked profit, staked so every side pays the same.
    arm = state["arms"]["arb"]
    for a in arbs[:2]:
        if len(placed) >= pr["max_bets_per_run"] or (benched(arm) and random.random() > 0.15):
            break
        if (a["event"]["id"], a["line"]["market"]) in held:
            continue
        eq = bk.equity(book)
        room = eq * pr["max_exposure_pct"] - sum(b["stake"] for b in bk.open_bets(book))
        total = min(eq * pr["arb_pct"] * arm_weight(arm), room, book["cash"])
        inv = sum(1 / l["price"] for l in a["legs"])
        if total < len(a["legs"]):
            continue
        gid = f"arb-{a['event']['id'][:10]}"
        for l in a["legs"]:
            b = bk.place(book, [l], total * (1 / l["price"]) / inv, "arb", ev=a["profit"],
                         p=1 / (l["price"] * inv), group=gid,
                         note=f"Arb: {a['profit']:.2%} locked across {len({x['book'] for x in a['legs']})} books")
            if b:
                placed.append(b)
        held.add((a["event"]["id"], a["line"]["market"]))
        held_events.add(a["event"]["id"])

    # 2) Single value bets, best edge × playbook confidence first.
    ranked = sorted(values, key=lambda c: -c["ev"] * arm_weight(state["arms"][c["playbook"]]))
    spare = []
    for c in ranked:
        if len(placed) >= pr["max_bets_per_run"]:
            break
        arm = state["arms"][c["playbook"]]
        eid = c["event"]["id"]
        if eid in held_events:
            spare.append(c)
            continue
        if benched(arm) and random.random() > 0.15:
            continue
        stake = size(state, c["p"], c["dec"], arm_weight(arm))
        if stake < 1:
            continue
        b = bk.place(book, [c["leg"]], stake, c["playbook"], ev=c["ev"], p=c["p"],
                     note=f"{c['ev']:.1%} edge vs {c['line']['outcomes'][0]['n']}-book consensus")
        if b:
            placed.append(b); held_events.add(eid)

    # 3) One +EV two-leg parlay from different games (edges multiply, so does variance).
    arm = state["arms"]["parlay_ev"]
    pool = [c for c in values if c["p"] >= 0.4 and c["event"]["id"] not in held_events]
    if pr["parlay_enabled"] and len(pool) >= 2 and len(placed) < pr["max_bets_per_run"] \
            and not (benched(arm) and random.random() > 0.15):
        a, b2 = pool[0], next((c for c in pool[1:] if c["event"]["id"] != pool[0]["event"]["id"]), None)
        if b2:
            p, dec = a["p"] * b2["p"], a["dec"] * b2["dec"]
            ev = p * dec - 1
            if ev >= pr["min_edge"] * 2:
                stake = size(state, p, dec, arm_weight(arm), mult=0.5)
                bt = bk.place(book, [a["leg"], b2["leg"]], stake, "parlay_ev", ev=ev, p=p,
                              note=f"2-leg parlay, {ev:.1%} combined edge")
                if bt:
                    placed.append(bt)
    return placed


# ---- Optional overnight strategist ------------------------------------------
STRATEGIST_PROMPT = """You are the overnight strategist for Perseverance, a PAPER (fake money) sports-betting agent
racing to turn ${start:.0f} into ${goal:.0f}. Review its numbers and propose parameter changes.
It bets value gaps against the multi-book consensus, arbitrage, and small +EV parlays.

Current params: {params}
Hard bounds (anything outside is clipped): {bounds}
Overall: {stats}
Playbooks: {arms}
Sports: {sports}
Recent journal: {journal}

Reply with ONLY a JSON object: {{"params": {{...only keys you want to change...}},
"bench": [playbook names to pause], "unbench": [...], "note": "2-3 sentences explaining your reasoning"}}"""


def strategist(state):
    if not config.ANTHROPIC_API_KEY:
        return
    book = state["book"]
    prompt = STRATEGIST_PROMPT.format(
        start=config.START_BANKROLL, goal=config.GOAL, params=json.dumps(state["params"]),
        bounds=json.dumps(config.BOUNDS), stats=json.dumps(bk.record_stats(book)),
        arms=json.dumps({k: {**v, "weight": round(arm_weight(v), 2)} for k, v in state["arms"].items()}),
        sports=json.dumps({k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()}
                           for k, v in state["sports"].items()}),
        journal=json.dumps([j["text"] for j in state["journal"][-12:]]))
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", method="POST",
        data=json.dumps({"model": config.STRATEGIST_MODEL, "max_tokens": 800,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = json.loads(r.read())["content"][0]["text"]
        plan = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception as e:
        note(state, "error", f"Strategist unavailable: {e}")
        return
    changes = []
    for k, v in (plan.get("params") or {}).items():
        if k in config.BOUNDS and isinstance(v, (int, float)):
            new = clamp(k, v)
            if new != state["params"][k]:
                changes.append(f"{k} {state['params'][k]} → {new}")
                state["params"][k] = new
    for name in plan.get("bench") or []:
        if name in state["arms"]:
            state["arms"][name]["paused"] = True; changes.append(f"pause {name}")
    for name in plan.get("unbench") or []:
        if name in state["arms"]:
            state["arms"][name]["paused"] = False; changes.append(f"resume {name}")
    state["strategist"] = {"ts": store.iso(store.now()), "note": plan.get("note", ""), "changes": changes}
    note(state, "strategist", (plan.get("note", "") + (" Changes: " + "; ".join(changes) if changes else "")).strip())
