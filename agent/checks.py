"""Self-checks that fail loudly. Run after every research refresh, every auto run and nightly; the results go to
docs/data/checks.json (the dashboard's Model → Health panel) and any failure is also noted in the journal.

Each check returns (status, detail): ok, warn or fail. A check that cannot run is a warn, never silent."""
import json
from datetime import timedelta

from . import config, store

DATA = config.ROOT / "docs" / "data"


def _load(name, default=None):
    try:
        return json.loads((DATA / name).read_text())
    except Exception:
        return default


def _age_h(ts):
    try:
        return (store.now() - store.parse(ts.replace(".000Z", "Z") if ts else ts)).total_seconds() / 3600
    except Exception:
        return None


def run(log=print):
    from . import research
    out = []

    def add(name, status, detail):
        out.append({"name": name, "status": status, "detail": detail})
    today = research.et_today().isoformat()
    pj = _load("players.json", {}) or {}
    games = pj.get("games") or []
    pre = [g for g in games if g.get("state") == "pre"]

    # 1. the slate is today's and fresh enough
    a = _age_h(pj.get("updated"))
    if pj.get("date") != today:
        add("Slate date", "warn" if not pre else "fail", f"players.json is for {pj.get('date')}, today is {today}")
    elif a is None:
        add("Research freshness", "fail", "players.json has no update time")
    else:
        lim = 4.5 if pre else 30
        add("Research freshness", "ok" if a <= lim else "fail", f"last research refresh {a:.1f}h ago (limit {lim}h while games are still to tip)" if pre else f"last research refresh {a:.1f}h ago")

    # 2. minutes: every team's projected minutes add up to about 240
    tm = {}
    for p in (pj.get("players") or {}).values():
        if p.get("out") or not p.get("minutes") or p.get("state", "pre") != "pre":
            continue
        tm[p["team"]] = tm.get(p["team"], 0) + p["minutes"] * (p.get("pplay") if p.get("pplay") is not None else 1)
    # expected minutes (projected minutes × chance he plays) of the players who get projections. The minutes model
    # shares 240 among every eligible player and leaves a few for the end of the bench (players under 10 minutes aren't
    # projected), so a healthy team lands about 215-245 here. Well short means minutes went missing (Nov 12: a player
    # ruled out by RotoWire only kept his minutes in the fit, so Charlotte came up at 204).
    warn_ = {t: round(v) for t, v in tm.items() if not 215 <= v <= 250}
    fail_ = {t: round(v) for t, v in tm.items() if not 200 <= v <= 265}
    add("Team minutes ≈ 240", "fail" if fail_ else "warn" if warn_ or not tm else "ok",
        f"{len(tm)} teams checked" + (f"; outside 215-250: {', '.join(f'{t} {v}' for t, v in sorted(warn_.items(), key=lambda x: x[1]))}" if warn_ else "; all within 215-250"))

    # 3. sims: every game still to tip has today's sims, and the sim total is within a sane band of the line
    sims = _load("sims.json", {}) or {}
    sg = sims.get("games") or {}
    if pre:
        miss = [f"{g['away']}@{g['home']}" for g in pre if g["espn_id"] not in sg or sims.get("date") != today]
        add("Sims for every game", "ok" if not miss else "fail", "all games simulated" if not miss else "missing: " + ", ".join(miss))
        far = []
        for g in pre:
            x = sg.get(g["espn_id"]) or {}
            v = (x.get("vegas") or {}).get("total")
            if v and x.get("total") and abs(x["total"][0] - v) > 15:
                far.append(f"{g['away']}@{g['home']} sim {x['total'][0]:.0f} vs line {v:g}")
        add("Sim totals near the line", "ok" if not far else "warn", "within 15 points on every game" if not far else "; ".join(far))

    # 4. lines: today's DraftKings file exists and pregame lines aren't stale
    dk = _load(f"dk/{today}.json", None)
    if pre:
        if not dk:
            add("DraftKings lines", "fail", f"no dk/{today}.json")
        else:
            a = _age_h(dk.get("updated"))
            nol = [f"{g['away']}@{g['home']}" for g in pre if not ((dk.get("games") or {}).get(g["espn_id"]) or {}).get("close")]
            add("DraftKings lines", "ok" if (a is not None and a <= 4.5 and not nol) else "warn",
                (f"saved {a:.1f}h ago" if a is not None else "no save time") + (f"; no line yet: {', '.join(nol)}" if nol else ""))

    # 4b. Kalshi (second market): today's file is fresh and its lines roughly agree with DraftKings'
    kd = _load(f"kalshi/{today}.json", None)
    if pre:
        if not kd:
            add("Kalshi lines", "warn", f"no kalshi/{today}.json yet")
        else:
            a = _age_h(kd.get("updated"))
            dkg = (dk or {}).get("games") or {}
            far, have = [], 0
            for g in pre:
                k = ((kd.get("games") or {}).get(g["espn_id"]) or {}).get("pre") or {}
                c = (dkg.get(g["espn_id"]) or {}).get("close") or {}
                if k:
                    have += 1
                if k.get("sp") is not None and c.get("sp") is not None and abs(k["sp"] - c["sp"]) >= 2:
                    far.append(f"{g['away']}@{g['home']} spread DK {c['sp']:+g} vs Kalshi {k['sp']:+g}")
                if k.get("tot") and c.get("tot") and abs(k["tot"] - c["tot"]) >= 3:
                    far.append(f"{g['away']}@{g['home']} total DK {c['tot']:g} vs Kalshi {k['tot']:g}")
            add("Kalshi lines", "ok" if (a is not None and a <= 1 and have == len(pre) and not far) else "warn",
                (f"saved {a:.1f}h ago, {have} of {len(pre)} games" if a is not None else "no save time")
                + (f"; far from DraftKings: {'; '.join(far)}" if far else "; agrees with DraftKings"))

    # 4b. one side per prop: Best Bets and the ranked board must never show opposite sides of the same line
    rk = _load("ranked.json", {}) or {}
    if rk.get("date") == today:
        side = {r["k"]: (r["side"], r["point"]) for r in (rk.get("props") or []) + (rk.get("games") or [])}
        bd = (_load("best.json", {}) or {}).get("days", {}).get(today) or {}
        clash = []
        for x in (bd.get("props") or []) + (bd.get("games") or []):
            if x.get("res"):
                continue
            k = f"{x['pid']}|{x['stat']}" if x.get("kind") == "prop" else f"g|{x['espn_id']}|{x['kind']}"
            if k in side and side[k][1] == x.get("point") and side[k][0] != x.get("side"):
                clash.append(f"{x.get('name') or x.get('game')} {x.get('stat') or x['kind']}: pick {x.get('side')}, board {side[k][0]}")
        add("One side per prop", "ok" if not clash else "fail",
            f"Best Bets and the ranked board agree on all {len(side)} lines" if not clash else "; ".join(clash[:5]))

    # 5. best bets: today's picks exist, every prop pick has minutes and no injury tag
    best = _load("best.json", {}) or {}
    day = (best.get("days") or {}).get(today)
    if pre:
        if not day:
            add("Best bets", "warn", "no picks for today yet")
        else:
            P = pj.get("players") or {}
            badp = [x["name"] for x in day.get("props") or [] if x.get("res") is None
                    and ((P.get(x["pid"]) or {}).get("out") or ((P.get(x["pid"]) or {}).get("minutes") or 0) < 15)]
            add("Best bets", "ok" if not badp else "fail", f"{len(day.get('props') or [])} props, {len(day.get('games') or [])} game lines"
                + (f"; ruled out or low minutes since picked: {', '.join(badp)}" if badp else ""))

    # 6. run log: no test runs or bursts of runs minutes apart
    sd = _load("sims_day.json", {}) or {}
    runs = sorted(r["ts"] for r in sd.get("runs") or [] if sd.get("date") == today)
    burst = sum(1 for a_, b_ in zip(runs, runs[1:]) if (store.parse(b_.replace(".000Z", "Z")) - store.parse(a_.replace(".000Z", "Z"))).total_seconds() < 180)
    add("Sim run log", "ok" if not burst else "warn", f"{len(runs)} runs today" + (f"; {burst} within 3 minutes of another (test runs?)" if burst else ""))

    # 7. odds credits will last the month
    b = _load("budget.json", {}) or {}
    rem, dl = b.get("remaining"), b.get("days_left")
    if rem is not None and dl:
        per = (rem - config.RESERVE_CREDITS) / max(1, dl)
        add("Odds API credits", "ok" if per >= 5 else "warn" if per >= 2 else "fail", f"{rem} left, {dl} days to go: {per:.1f} a day after the reserve")

    # 8. nightly pieces ran in the last day and a half
    for name, f in (("Model vs market scoreboard", "scoreboard.json"), ("Team model", "team_model.json")):
        a = _age_h((_load(f, {}) or {}).get("updated"))
        add(name, "ok" if a is not None and a <= 36 else "warn", f"updated {a:.0f}h ago" if a is not None else "never updated")

    # 9. Daily Briefing: the morning edition is out by 8am on a game day, its open picks match Best Bets' side,
    #    and a written edition followed (the scheduled Claude writer)
    try:
        from . import briefing
        h = briefing.et_hour()
        bf = _load(f"briefing/{today}.json", None)
        if pj.get("date") == today and games and h >= 8 and today >= briefing.FIRST_DAY:
            if not bf or not any(e["id"] == "morning" for e in bf.get("editions") or []):
                add("Daily Briefing", "warn", "no morning edition yet")
            else:
                side = {f"p|{x['pid']}|{x['stat']}": x["side"] for x in (day or {}).get("props") or []}
                clash = [c["label"] for c in bf["picks"].values() if not c.get("removed") and not c.get("started")
                         and c["key"].rsplit("|", 1)[0] in side and side[c["key"].rsplit("|", 1)[0]] != c["side"]]
                wrote = [n["edition"] for n in bf.get("narratives") or []]
                late = h >= 9 and "morning" not in wrote
                add("Daily Briefing", "fail" if clash else "warn" if late else "ok",
                    (f"sides disagree with Best Bets: {', '.join(clash)}" if clash else
                     f"{len(bf['picks'])} props, {len(bf['leans'])} leans, {len(bf['changes'])} changes; written: {', '.join(wrote) or 'none yet'}"
                     + ("; the 7:22am written edition is missing" if late else "")))
    except Exception as e:
        add("Daily Briefing", "warn", f"check could not run: {e}")

    worst = "fail" if any(c["status"] == "fail" for c in out) else "warn" if any(c["status"] == "warn" for c in out) else "ok"
    res = {"updated": store.iso(store.now()), "status": worst, "checks": out}
    (DATA / "checks.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    for c in out:
        if c["status"] != "ok":
            log(f"[checks] {c['status'].upper()} {c['name']}: {c['detail']}")
    return res
