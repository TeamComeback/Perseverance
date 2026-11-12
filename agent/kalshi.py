"""Kalshi: a second market for NBA lines, free (public market data, no key, no Odds API credits).

Kalshi is a regulated exchange where people trade yes/no contracts against each other, so its prices come from a
different crowd than DraftKings' traders. Every game has:
  KXNBAGAME    one contract per team: "Philadelphia wins"                      -> win chance
  KXNBASPREAD  a ladder per team: "Philadelphia wins by over 7.5 points"     -> Kalshi's own spread
  KXNBATOTAL   a ladder: "Over 223.5 points"                                  -> Kalshi's own total
  KXNBAPTS/REB/AST/3PT  player ladders: "Anfernee Simons: 20+"               -> chance of any prop line
Prices are dollars per $1 contract = the crowd's probability. We use the middle of bid and ask, and only when the
two are within 10 cents of each other (a wide gap means nobody is really trading it).

Kept per day in docs/data/kalshi/YYYY-MM-DD.json:
  games  {espn_id: {home, away, ev, open, pre, live[], final}}   open = first look, pre = latest before tip,
         live = snapshots during the game. Each look: win (home win chance), sp (home spread, - = home favored),
         tot (total), p_cover/p_over (Kalshi's chance at DraftKings' line), vol (contracts traded)
  props  {"pid|stat": {name, ladder [[line, chance of over]], fair, line (DraftKings'), p (chance of over at it),
         exact (DraftKings' line is one of Kalshi's own), vol}}

The browser can't call Kalshi directly (it doesn't allow other websites), so the agent saves a look every run
(every 15 minutes through the outside timer). Nightly, kalshi_track.json grades which market was closer:
DraftKings' no-vig price or Kalshi's, on game lines and props."""
import json
import math
import re
import unicodedata
import urllib.request
from datetime import timedelta

from . import config, store

API = "https://api.elections.kalshi.com/trade-api/v2/markets"
SERIES = {"win": "KXNBAGAME", "spread": "KXNBASPREAD", "total": "KXNBATOTAL",
          "pts": "KXNBAPTS", "reb": "KXNBAREB", "ast": "KXNBAAST", "tpm": "KXNBA3PT"}
CODE = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}   # ESPN -> Kalshi
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
DIR = "kalshi"
KEEP_DAYS = 45
MAX_GAP = 0.10          # bid-ask wider than this: not a real price
LIVE_KEEP = 60


def path(day):
    return f"{DIR}/{day}.json"


def load(day):
    return store.load(path(day), {"date": day, "games": {}, "props": {}})


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "perseverance/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch(series):
    """Every open market in one series (a few pages at most)."""
    out, cur = [], ""
    for _ in range(10):
        d = _get(f"{API}?series_ticker={series}&status=open&limit=1000" + (f"&cursor={cur}" if cur else ""))
        out += d.get("markets") or []
        cur = d.get("cursor")
        if not cur or not d.get("markets"):
            break
    return out


def datecode(day):
    """2026-11-12 -> 26NOV12 (Kalshi names games by their US date)."""
    return f"{day.year % 100:02d}{MONTHS[day.month - 1]}{day.day:02d}"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def mid(m):
    """The market's probability: middle of bid and ask, None when the gap is too wide to trust."""
    b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if a <= 0 or a < b or a - b > MAX_GAP:
        return None
    if b <= 0:
        return None if a > 0.05 else a / 2
    return (a + b) / 2


def _logit(p):
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1 - p))


def interp(ladder, x):
    """Chance of finishing over x from a ladder [[line, chance over], ...] (sorted by line). Straight-line on the
    log-odds scale between the two nearest lines; None outside the ladder."""
    pts = sorted(ladder)
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return p0
            t = (x - x0) / (x1 - x0)
            z = _logit(p0) + t * (_logit(p1) - _logit(p0))
            return 1 / (1 + math.exp(-z))
    for xx, pp in pts:
        if abs(xx - x) < 1e-9:
            return pp
    return None


def median(ladder):
    """The line where over and under are 50/50."""
    pts = sorted(ladder)
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if (p0 - 0.5) * (p1 - 0.5) <= 0 and p0 != p1:
            z0, z1 = _logit(p0), _logit(p1)
            return x0 + (0 - z0) / (z1 - z0) * (x1 - x0)
    return None


def _monotone(ladder):
    """Chances of 'over' must fall as the line rises; pool small crossings into their average
    (pool-adjacent-violators), one value per original line."""
    pts = sorted(ladder)
    blocks = []                                   # [sum of p, count]
    for _, p in pts:
        blocks.append([p, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += n
    vals = [b[0] / b[1] for b in blocks for _ in range(b[1])]
    return [[x, round(v, 4)] for (x, _), v in zip(pts, vals)]


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def _no_vig(a, b):
    if not a or not b:
        return None
    pa, pb = 1 / a, 1 / b
    return pa / (pa + pb)


def look(games, players, dkdb, day, log=print):
    """One look at Kalshi for the given games (ESPN slate entries with espn_id/home/away/state).
    Returns (games, props) dicts keyed like the day file."""
    dc = datecode(day)
    raw = {}
    for k, s in SERIES.items():
        try:
            raw[k] = [m for m in fetch(s) if f"-{dc}" in m.get("event_ticker", "")]
        except Exception as e:
            log(f"[kalshi] {s} failed: {e}")
            raw[k] = []
    return build(raw, games, players, dkdb, day)


def build(raw, games, players, dkdb, day):
    """Lines from Kalshi markets ({series key: [market]}) for the given games. Also used by the backfill with
    historical prices."""
    dc = datecode(day)
    by_ev = {}
    for k, ms in raw.items():
        for m in ms:
            by_ev.setdefault(m["event_ticker"].split("-", 1)[1], {}).setdefault(k, []).append(m)
    G, P = {}, {}
    pnames = {}
    for pid, p in (players or {}).items():
        pnames.setdefault((p.get("team"), _norm(p.get("name"))), pid)
    dkg = (dkdb or {}).get("games") or {}
    dkp = (dkdb or {}).get("props") or {}
    for g in games:
        h, a = CODE.get(g["home"], g["home"]), CODE.get(g["away"], g["away"])
        ev = by_ev.get(f"{dc}{a}{h}") or by_ev.get(f"{dc}{h}{a}")
        if not ev:
            continue
        eid = g["espn_id"]
        rec = {"vol": 0}
        # win chance
        wm = {m["ticker"].rsplit("-", 1)[1]: m for m in ev.get("win", [])}
        ph, pa_ = (mid(wm[h]) if h in wm else None), (mid(wm[a]) if a in wm else None)
        if ph is not None and pa_ is not None and ph + pa_ > 0:
            rec["win"] = round(ph / (ph + pa_), 4)
        elif ph is not None:
            rec["win"] = round(ph, 4)
        rec["vol"] += int(sum(_f(m.get("volume_fp")) for m in ev.get("win", [])))
        # spread ladder on the home margin: P(home margin > s)
        lad = []
        for m in ev.get("spread", []):
            p = mid(m)
            team = re.sub(r"\d+$", "", m["ticker"].rsplit("-", 1)[1])
            s = _f(m.get("floor_strike"))
            if p is None:
                continue
            if team == h:
                lad.append([s, p])
            elif team == a:
                lad.append([-s, 1 - p])
        if rec.get("win") is not None:
            lad.append([0.0, rec["win"]])
        rec["vol"] += int(sum(_f(m.get("volume_fp")) for m in ev.get("spread", [])))
        if len(lad) >= 3:
            lad = _monotone(lad)
            md = median(lad)
            if md is not None:
                rec["sp"] = round(-md, 1)
            rec["sp_ladder"] = lad
        # total ladder
        tl = [[_f(m.get("floor_strike")), mid(m)] for m in ev.get("total", []) if mid(m) is not None]
        rec["vol"] += int(sum(_f(m.get("volume_fp")) for m in ev.get("total", [])))
        if len(tl) >= 2:
            tl = _monotone(tl)
            md = median(tl)
            if md is not None:
                rec["tot"] = round(md, 1)
            rec["tot_ladder"] = tl
        # Kalshi's chance at DraftKings' line
        dl = (dkg.get(eid) or {}).get("close") or {}
        if dl.get("sp") is not None and rec.get("sp_ladder"):
            q = interp(rec["sp_ladder"], -dl["sp"])
            rec["p_cover"] = None if q is None else round(q, 4)
        if dl.get("tot") is not None and rec.get("tot_ladder"):
            q = interp(rec["tot_ladder"], dl["tot"])
            rec["p_over"] = None if q is None else round(q, 4)
        G[eid] = rec
        # player props
        for st in ("pts", "reb", "ast", "tpm"):
            lads = {}
            for m in ev.get(st, []):
                nm_ = (m.get("yes_sub_title") or "").rsplit(":", 1)[0].strip()
                tcode = re.match(r"[A-Z]+", m["ticker"].split("-")[2])
                p = mid(m)
                if not nm_ or p is None:
                    continue
                L = lads.setdefault(nm_, {"lad": [], "vol": 0, "team": tcode.group(0) if tcode else ""})
                L["lad"].append([_f(m.get("floor_strike")), p])
                L["vol"] += int(_f(m.get("volume_fp")))
            for nm_, L in lads.items():
                team = next((t for t in (g["home"], g["away"]) if L["team"].startswith(CODE.get(t, t))), None)
                pid = pnames.get((team, _norm(nm_))) or pnames.get((g["home"], _norm(nm_))) or pnames.get((g["away"], _norm(nm_)))
                if not pid:
                    continue
                lad = _monotone(L["lad"])
                r = {"name": nm_, "espn_id": eid, "ladder": lad, "vol": L["vol"]}
                md = median(lad)
                if md is not None:
                    r["fair"] = round(md, 1)
                d = dkp.get(f"{pid}|{st}")
                if d and d.get("c") and d["c"][0] is not None:
                    line = d["c"][0]
                    q = interp(lad, line)
                    r.update(line=line, p=None if q is None else round(q, 4), exact=any(abs(x - line) < 1e-9 for x, _ in lad))
                P[f"{pid}|{st}"] = r
    return G, P


def _strip(rec):
    return {k: v for k, v in rec.items() if not k.endswith("_ladder")}


def refresh(games=None, day=None, log=print):
    """Save a look for one day: pregame values update 'pre' (first look kept as 'open'), games in progress add a
    live snapshot. Free; about a dozen requests."""
    from . import research, dk
    day = day or research.et_today()
    ds = day.isoformat()
    if games is None:
        games = research.slate_all(day)
    games = [g for g in games if g.get("state") in ("pre", "in")]
    if not games:
        return None
    pj = store.load("players.json", None) or {}
    players = pj.get("players") or {} if pj.get("date") == ds else {}
    dkdb = dk.load(ds)
    G, P = look(games, players, dkdb, day, log=log)
    db = load(ds)
    now = store.iso(store.now())
    state = {g["espn_id"]: g for g in games}
    for eid, rec in G.items():
        g = state[eid]
        E = db["games"].setdefault(eid, {"home": g["home"], "away": g["away"], "live": []})
        rec["at"] = now
        if g.get("state") == "pre":
            E.setdefault("open", _strip(rec))
            E["pre"] = rec
        else:
            E["live"] = (E.get("live") or [])[-(LIVE_KEEP - 1):] + [_strip(rec)]
    for k, r in P.items():
        g = state.get(r["espn_id"]) or {}
        if g.get("state") == "pre" or k not in db["props"]:
            r["at"] = now
            db["props"][k] = r
    (config.DATA / DIR).mkdir(parents=True, exist_ok=True)
    db["updated"] = now
    store.save(path(ds), db, compact=True)
    log(f"[kalshi] {ds}: {len(G)} games, {len(P)} props")
    return db


def _ll(p, won):
    p = min(max(p, 0.01), 0.99)
    return -math.log(p if won else 1 - p)


def grade(days=KEEP_DAYS, log=print):
    """Who was closer, DraftKings (no-vig) or Kalshi? Log loss on every bet both priced (lower is better), plus
    the times they disagreed by 4+ points of probability: which side won."""
    from . import research, dk
    today = research.et_today()
    rows = {"games": [], "props": []}
    for back in range(1, days + 1):
        ds = (today - timedelta(days=back)).isoformat()
        kd = store.load(path(ds), None)
        if not kd:
            continue
        dd = dk.load(ds)
        for eid, E in kd.get("games", {}).items():
            D = dd["games"].get(eid) or {}
            fin, c, pre = D.get("final"), D.get("close") or {}, E.get("pre") or {}
            if not fin or fin[0] is None:
                continue
            margin, total = fin[1] - fin[0], fin[1] + fin[0]
            if pre.get("p_cover") is not None and c.get("sp") is not None and margin + c["sp"] != 0:
                pd_ = _no_vig(c.get("sp_h"), c.get("sp_a"))
                if pd_:
                    rows["games"].append((ds, "spread", pd_, pre["p_cover"], margin + c["sp"] > 0))
            if pre.get("p_over") is not None and c.get("tot") is not None and total != c["tot"]:
                pd_ = _no_vig(c.get("ov"), c.get("un"))
                if pd_:
                    rows["games"].append((ds, "total", pd_, pre["p_over"], total > c["tot"]))
            if pre.get("win") is not None and c.get("ml_h") and c.get("ml_a"):
                rows["games"].append((ds, "win", _no_vig(c["ml_h"], c["ml_a"]), pre["win"], margin > 0))
        for k, r in kd.get("props", {}).items():
            D = dd["props"].get(k) or {}
            if r.get("p") is None or D.get("res") not in ("over", "under") or not D.get("c"):
                continue
            if r.get("line") != D["c"][0]:
                continue
            pd_ = _no_vig(D["c"][1], D["c"][2])
            if pd_:
                rows["props"].append((ds, D["stat"], pd_, r["p"], D["res"] == "over"))
    out = {"updated": store.iso(store.now())}
    for kind, rs in rows.items():
        groups = {"all": rs}
        for r in rs:
            groups.setdefault(r[1], []).append(r)
        o = {}
        for name, g in groups.items():
            if not g:
                continue
            n = len(g)
            dis = [r for r in g if abs(r[3] - r[2]) >= 0.04]
            k_right = sum(1 for r in dis if (r[3] > r[2]) == r[4])      # outcome went Kalshi's way
            o[name] = {"n": n, "days": len({r[0] for r in g}),
                       "ll_dk": round(sum(_ll(r[2], r[4]) for r in g) / n, 4),
                       "ll_kalshi": round(sum(_ll(r[3], r[4]) for r in g) / n, 4),
                       "ll_blend": round(sum(_ll((r[2] + r[3]) / 2, r[4]) for r in g) / n, 4),
                       "gap": round(sum(abs(r[3] - r[2]) for r in g) / n, 4),
                       "disagree": {"n": len(dis), "kalshi_right": k_right}}
        out[kind] = o
    store.save("kalshi_track.json", out)
    a = (out.get("props") or {}).get("all") or {}
    log(f"[kalshi] graded: {a.get('n', 0)} props" + (f", log loss DraftKings {a['ll_dk']} vs Kalshi {a['ll_kalshi']}" if a else ""))
    prune()
    return out


def prune():
    root = config.DATA / DIR
    if not root.exists():
        return
    from . import research
    cut = (research.et_today() - timedelta(days=KEEP_DAYS)).isoformat()
    for f in root.glob("*.json"):
        if f.stem < cut:
            f.unlink()
