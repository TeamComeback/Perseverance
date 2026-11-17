"""The agent team (multi-agent framework, Nov 17 2026).

In-program agents ride the agent's own runs: Scout and Closer (run.scout_run), Prop Pricer (run.props_run slots),
Shopper (every bet's `shop` field), Backtester (backtest.py). Four Claude agents run as scheduled tasks and each
writes one file under docs/data/agents/:

  Newsroom    news.json      ~11:05am and ~5:05pm ET   injury, rest and lineup news beyond the official report
  Skeptic     holds.json     ~5:45pm and ~9:15pm ET    the day's candidates and open bets that look stale,
                                                       correlated or contradicted by news
  Strategist  strategy.json  nightly ~3:10am ET        setting changes, always inside config.BOUNDS
  Auditor     audit.json     Sundays ~9:20am ET        weekly scorecard: promote, keep or bench each agent

SHADOW FIRST (owner rule in the framework): nothing these files say changes a bet or a setting yet. Every run tags
the agent's bets with what each Claude agent would have done (`shadow` on the bet: held by the Newsroom? vetoed by the
Skeptic?), only counting calls made before the game tipped, and the scorecard compares those bets' results and
closing-line value with the rest. An agent goes live only after about 150 graded calls show it helps, and only when
the owner approves (`LIVE` below, changed by hand).

  python3 -m agent.agents brief <newsroom|skeptic|strategist|auditor>   what that agent needs, as JSON
  python3 -m agent.agents save <role> <file.json>                       check the file and put it in place
  python3 -m agent.agents shift <role> start "<what you're doing>"      on shift (shown live in the city)
"""
import json
import sys

from . import book as bk
from . import config, store

ROLES = {
    "newsroom": ("news.json", "Newsroom", "~11:05am and ~5:05pm ET"),
    "skeptic": ("holds.json", "Skeptic", "~5:45pm and ~9:15pm ET"),
    "strategist": ("strategy.json", "Strategist", "nightly ~3:10am ET"),
    "auditor": ("audit.json", "Auditor", "Sundays ~9:20am ET"),
}
LIVE = set()               # Claude agents whose calls the agent acts on (owner approval only); empty = all in shadow
PROMOTE_MIN = 150          # graded calls before an agent can be considered for promotion
FLAG_STATUSES = {"out", "doubtful", "questionable", "rest", "minutes_limit", "benched"}
DIR = "agents"


def _load(role):
    return store.load(f"{DIR}/{ROLES[role][0]}", None)


def _norm(s):
    from .props import norm
    return norm(s or "")


def _nick(s):
    return (s or "").split()[-1].lower()


# ------------------------------------------------------------------ shadow tagging and scoring
def _matches(item, leg):
    """Does a Newsroom item or Skeptic hold point at this leg?"""
    if item.get("bet_id"):
        return False
    if item.get("pid") and leg.get("player_id") and str(item["pid"]) == str(leg["player_id"]):
        return not item.get("stat") or item["stat"] == leg.get("stat")
    if item.get("player") and leg.get("player") and _norm(item["player"]) == _norm(leg["player"]):
        return not item.get("stat") or item["stat"] == leg.get("stat")
    g = item.get("game")
    if g and not item.get("player") and not item.get("pid") and "@" in g:
        a, h = [x.strip() for x in g.split("@", 1)]
        return _team_is(a, leg.get("away")) and _team_is(h, leg.get("home"))
    return False


_ABBR = None


def _team_is(token, name):
    """'NY', 'Knicks' or 'New York Knicks' against a leg's full team name."""
    global _ABBR
    if _ABBR is None:
        _ABBR = {}
        for g in (store.load("players.json", {}) or {}).get("games", []):
            for side in ("home", "away"):
                if g.get(side) and g.get(side + "_name"):
                    _ABBR[g[side].lower()] = g[side + "_name"].lower()
    if not token or not name:
        return False
    t, n = token.strip().lower(), name.lower()
    return t == n or _nick(t) == _nick(n) or _ABBR.get(t) == n


def tag(state):
    """Every run: mark the agent's open bets with what the Newsroom and Skeptic would have done. Calls count only if
    the file was written before the game tipped."""
    news, holds = _load("newsroom"), _load("skeptic")
    now = store.now()
    for b in bk.open_bets(state["book"]):
        sh = b.setdefault("shadow", {})
        for role, doc, items in (("newsroom", news, [i for i in (news or {}).get("items", []) if i.get("flag")]),
                                 ("skeptic", holds, (holds or {}).get("holds", []))):
            if not doc or role in sh and sh[role].get("final"):
                continue
            at = store.parse(doc["updated"]) if doc.get("updated") else None
            hit = None
            for l in b["legs"]:
                if not l.get("commence") or not at or at >= store.parse(l["commence"]):
                    continue
                for it in items:
                    if it.get("bet_id") == b["id"] or _matches(it, l):
                        hit = it.get("reason") or it.get("detail") or it.get("status") or "flagged"
                        break
            started = all(store.parse(l["commence"]) <= now for l in b["legs"] if l.get("commence"))
            if hit:
                sh[role] = {"hold": True, "why": str(hit)[:160], "at": doc.get("updated"), "final": started}
            elif started:
                sh.setdefault(role, {"hold": False})["final"] = True


def scores(state):
    """Shadow record per Claude agent: the agent's settled bets it would have held vs the rest."""
    settled = [b for b in state["book"]["bets"] if b["status"] in ("won", "lost", "push") and b.get("shadow")]
    out = {}
    for role in ("newsroom", "skeptic"):
        held = [b for b in settled if (b["shadow"].get(role) or {}).get("hold")]
        rest = [b for b in settled if role in b["shadow"] and not b["shadow"][role].get("hold")]

        def summ(bs):
            clv = [l["price"] * l["close_p"] - 1 for b in bs for l in b["legs"] if l.get("close_p")]
            return {"n": len(bs), "profit": round(sum(b["profit"] for b in bs), 2),
                    "won": sum(b["status"] == "won" for b in bs), "lost": sum(b["status"] == "lost" for b in bs),
                    "clv": round(sum(clv) / len(clv), 4) if clv else None}
        out[role] = {"held": summ(held), "rest": summ(rest), "calls": len(held) + len(rest),
                     "saved": round(-sum(b["profit"] for b in held), 2)}
    return out


# ------------------------------------------------------------------ the scorecard (docs/data/agents.json)
def _age_h(ts):
    return (store.now() - store.parse(ts)).total_seconds() / 3600 if ts else None


def scorecard(state, budget=None):
    b = (budget.b if budget else store.load("budget.json", {})) or {}
    jd, jc = b.get("jobs_day") or {}, b.get("jobs_cycle") or {}
    st = state.get("stats") or {}
    sc = scores(state)
    bt = store.load("backtest.json", {}) or {}
    pr = store.load("props_ran.json", {}) or {}
    legs = [l for x in state["book"]["bets"] if x["status"] in ("won", "lost", "push") for l in x["legs"]]
    closed = sum(1 for l in legs if l.get("close_p"))
    locked = b.get("locked")
    A = [
        {"name": "Scout", "kind": "program", "status": "off" if locked else "live", "when": "every 15 min (NBA every run 4h before tip, others hourly)",
         "detail": f"{jd.get('scan', 0)} credits today · {jc.get('scan', 0):,} this cycle"},
        {"name": "Prop Pricer", "kind": "program", "status": "off" if locked else "live", "when": "11am, 2pm, 5pm ET + ~1h before first tip",
         "detail": f"slots done today: {', '.join(f'{x:g}h' for x in pr.get('slots', [])) or 'none'} · {jd.get('props', 0)} credits today"},
        {"name": "Closer", "kind": "program", "status": "off" if locked else "live", "when": "every run, at tip",
         "detail": f"{closed} of {len(legs)} settled legs have a closing price · {jd.get('closer', 0)} credits today"},
        {"name": "Shopper", "kind": "program", "status": "live", "when": "every bet",
         "detail": (f"best of ten books averages {st['shop_avg'] * 100:+.1f}% over the typical book ({st['shop_n']} legs)"
                    if st.get("shop_avg") is not None else "measuring from the next bet on")},
        {"name": "Backtester", "kind": "program", "status": "off" if locked else "live", "when": "Sundays ~3:23am ET",
         "detail": (" ".join(bt.get("verdict", [])[:1]) or "not run yet") + f" · {sum((bt.get('credits') or {}).values()):,} credits used"},
    ]
    for role, (fname, name, when) in ROLES.items():
        doc = _load(role)
        a = _age_h((doc or {}).get("updated"))
        limit = 26 if role in ("newsroom", "skeptic", "strategist") else 8 * 24
        status = ("live" if role in LIVE else "shadow") if doc else "off"
        if doc and a is not None and a > limit:
            status = "stale"
        if role in ("newsroom", "skeptic"):
            s = sc[role]
            detail = (f"{s['calls']} graded calls · would have held {s['held']['n']} bets "
                      f"({s['held']['won']}-{s['held']['lost']}, {s['held']['profit']:+.2f}) · "
                      f"{'saved' if s['saved'] >= 0 else 'cost'} ${abs(s['saved']):.2f}")
        elif role == "strategist":
            props = (doc or {}).get("proposals") or []
            detail = (f"{len(props)} proposal(s) last night (shadow: recorded, not applied)" if doc else "first run tonight")
        else:
            detail = ((doc or {}).get("summary") or "first audit Sunday")[:220]
        A.append({"name": name, "kind": "claude", "status": status, "when": when,
                  "detail": (f"updated {a:.0f}h ago · " if a is not None else "") + detail})
    note = ("Claude agents start in shadow: their calls are recorded and graded, not acted on. An agent goes live after "
            f"about {PROMOTE_MIN} graded calls show it helps, and only with the owner's approval.")
    store.save("agents.json", {"updated": store.iso(store.now()), "agents": A, "shadow": sc, "note": note})


# ------------------------------------------------------------------ briefs for the Claude agents
def _slate():
    pj = store.load("players.json", {}) or {}
    games = [{"game": f"{g['away']} @ {g['home']}", "names": f"{g.get('away_name')} @ {g.get('home_name')}",
              "start": g["start"], "state": g["state"]} for g in pj.get("games", [])]
    return pj, games


def brief(role):
    pj, games = _slate()
    state = store.load("state.json", {}) or {}
    today = pj.get("date")
    if role == "newsroom":
        rot = [{"player": p["name"], "pid": p["pid"], "team": p["team"], "opp": p["opp"], "min": p.get("minutes"),
                "status": p.get("status") or "", "lineup": p.get("lineup")}
               for p in (pj.get("players") or {}).values() if (p.get("minutes") or 0) >= 18 and p.get("state") == "pre"]
        bets = [{"player": l.get("player"), "stat": l.get("stat"), "side": l.get("side"), "game": f"{l.get('away')} @ {l.get('home')}"}
                for b in bk.open_bets(state.get("book", {"bets": []})) for l in b["legs"]]
        best = store.load("best.json", {}) or {}
        picks = [{"game": x.get("game"), "player": x.get("player") or x.get("name"), "stat": x.get("stat")}
                 for x in ((best.get("days") or {}).get(today) or {}).get("props", [])]
        return {"date": today, "games": games, "rotation_players": sorted(rot, key=lambda x: -(x["min"] or 0)),
                "open_bets": bets, "best_bet_props": picks, "official_report": pj.get("injury_report"),
                "schema": {"items": [{"player": "name", "pid": "ESPN id from rotation_players", "team": "abbr",
                                      "status": "out|doubtful|questionable|probable|available|rest|minutes_limit|starting|benched|other",
                                      "detail": "one sentence", "source": "URL", "flag": "true if a bet on this player should be held"}]}}
    if role == "skeptic":
        best = store.load("best.json", {}) or {}
        day = (best.get("days") or {}).get(today) or {}
        bets = [{"bet_id": b["id"], "playbook": b["playbook"], "placed": b["placed"], "stake": b["stake"], "ev": b.get("ev"),
                 "legs": [{k: l.get(k) for k in ("player", "stat", "side", "point", "price", "book", "home", "away", "market", "outcome", "commence")}
                          for l in b["legs"]]} for b in bk.open_bets(state.get("book", {"bets": []}))]
        bt = store.load("backtest.json", {}) or {}
        return {"date": today, "games": games, "open_agent_bets": bets,
                "best_bets": [{k: x.get(k) for k in ("game", "kind", "player", "name", "stat", "side", "point", "am", "ev", "conv", "tier", "why")}
                              for x in day.get("props", []) + day.get("games", [])],
                "news": (_load("newsroom") or {}).get("items", []),
                "backtest": {"by_edge": (bt.get("props") or {}).get("by_edge"), "verdict": bt.get("verdict")},
                "schema": {"holds": [{"bet_id": "optional", "player": "optional", "pid": "optional", "stat": "optional",
                                      "game": "optional 'AWAY @ HOME' (abbr or full names), for a whole game",
                                      "kind": "stale|correlated|news|thin_edge|other", "reason": "one sentence"}],
                           "notes": "optional"}}
    if role == "strategist":
        bt = store.load("backtest.json", {}) or {}
        sb = store.load("scoreboard.json", {}) or {}
        jr = [j for j in state.get("journal", [])[-120:] if j.get("kind") in ("result", "race", "focus", "budget")]
        return {"params": state.get("params"), "bounds": config.BOUNDS, "stats": state.get("stats"),
                "arms": {k: {x: v.get(x) for x in ("bets", "staked", "profit", "clv_n", "clv_sum")} for k, v in (state.get("arms") or {}).items()},
                "paused": state.get("paused"), "backtest": {k: bt.get(k) for k in ("verdict", "bets", "props")},
                "scoreboard_focus": sb.get("focus"), "recent_journal": jr[-40:], "shadow": scores(state) if state else None,
                "last": _load("strategist"),
                "schema": {"proposals": [{"param": "a key of bounds", "value": "number inside its bounds", "why": "one sentence with the numbers"}],
                           "summary": "two or three sentences"}}
    if role == "auditor":
        return {"agents": store.load("agents.json", {}), "backtest": store.load("backtest.json", {}),
                "budget": {k: (store.load("budget.json", {}) or {}).get(k) for k in ("remaining", "plan", "jobs_cycle", "cycle_end", "backtest_used")},
                "stats": state.get("stats"), "strategist_last": _load("strategist"), "audit_last": _load("auditor"),
                "schema": {"summary": "three to five sentences", "recommendations": [
                    {"agent": "Scout|Prop Pricer|Closer|Shopper|Backtester|Newsroom|Skeptic|Strategist|playbook name",
                     "action": "promote|keep|bench|tune", "why": "one sentence with the numbers"}]}}
    raise SystemExit(f"unknown role {role}")


def save(role, path):
    """Check a Claude agent's file and put it in place. Strategist proposals outside the bounds are clipped."""
    with open(path) as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise SystemExit("expected a JSON object")
    doc["updated"] = store.iso(store.now())
    doc["by"] = role
    if role == "newsroom":
        doc["items"] = [i for i in doc.get("items", []) if isinstance(i, dict) and i.get("player")][:80]
        for i in doc["items"]:
            i["flag"] = bool(i.get("flag")) and str(i.get("status", "")).lower() in FLAG_STATUSES
    elif role == "skeptic":
        doc["holds"] = [h for h in doc.get("holds", []) if isinstance(h, dict) and h.get("reason")][:40]
    elif role == "strategist":
        ok = []
        for p in doc.get("proposals", [])[:10]:
            k = p.get("param")
            if k not in config.BOUNDS or not isinstance(p.get("value"), (int, float)):
                continue
            lo, hi = config.BOUNDS[k]
            p["value"] = min(hi, max(lo, p["value"]))
            ok.append(p)
        doc["proposals"] = ok
        hist = store.load(f"{DIR}/strategy_log.json", []) or []
        hist = (hist + [{"at": doc["updated"], "proposals": ok, "summary": doc.get("summary", "")}])[-120:]
        store.save(f"{DIR}/strategy_log.json", hist)
    elif role == "auditor":
        doc["recommendations"] = [r for r in doc.get("recommendations", []) if isinstance(r, dict) and r.get("agent")][:20]
    (config.DATA / DIR).mkdir(parents=True, exist_ok=True)
    store.save(f"{DIR}/{ROLES[role][0]}", doc)
    n = {"newsroom": len(doc.get("items", [])), "skeptic": len(doc.get("holds", [])),
         "strategist": len(doc.get("proposals", [])), "auditor": len(doc.get("recommendations", []))}[role]
    shift(role, done=True, summary={"newsroom": f"{n} news item(s), {sum(1 for i in doc.get('items', []) if i.get('flag'))} flagged",
                                    "skeptic": f"{n} hold(s)", "strategist": f"{n} proposal(s) (shadow)",
                                    "auditor": doc.get("summary", "")[:160] or f"{n} recommendation(s)"}[role])
    print(f"saved {DIR}/{ROLES[role][0]}: " + ", ".join(f"{k} {len(v)}" for k, v in doc.items() if isinstance(v, list)))


SHIFT_ROLES = {**{k: v[1] for k, v in ROLES.items()}, "briefing": "Briefing writer"}


def shift(role, what="", done=False, summary=""):
    """Claude agents' shift log (docs/data/agents/shift.json): who is on shift now and what they're doing. Only the
    Claude agents write it (the agent's own runs never touch it), so it can't collide with a run's commit."""
    if role not in SHIFT_ROLES:
        raise SystemExit(f"unknown role {role}")
    (config.DATA / DIR).mkdir(parents=True, exist_ok=True)
    sh = store.load(f"{DIR}/shift.json", {}) or {}
    now = store.iso(store.now())
    cur = sh.get(role) or {}
    if done:
        cur.update(finished=now, summary=str(summary)[:200])
    else:
        cur = {"name": SHIFT_ROLES[role], "started": now, "doing": str(what)[:160], "finished": None, "summary": ""}
    sh[role] = cur
    log = (sh.get("log") or []) + [{"role": role, "name": SHIFT_ROLES[role], "at": now, "ev": "finished" if done else "started",
                                    "text": str(summary if done else what)[:200]}]
    sh["log"] = log[-120:]
    sh["updated"] = now
    store.save(f"{DIR}/shift.json", sh)
    print(f"{SHIFT_ROLES[role]} {'finished' if done else 'on shift'}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "brief":
        print(json.dumps(brief(sys.argv[2]), indent=1, default=str))
    elif len(sys.argv) >= 4 and sys.argv[1] == "save":
        save(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 4 and sys.argv[1] == "shift":
        shift(sys.argv[2], " ".join(sys.argv[4:]) if sys.argv[3] == "start" else "", done=sys.argv[3] == "done",
              summary=" ".join(sys.argv[4:]) if sys.argv[3] == "done" else "")
    else:
        print(__doc__)
