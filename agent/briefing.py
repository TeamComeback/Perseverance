"""Daily Briefing (framework Nov 16, 2026; owner decisions and stage 0 study the same day).

One report per day in docs/data/briefing/YYYY-MM-DD.json, published as the morning edition at about 7am ET and kept
current after every run:
  - picks: tonight's Best Bets props as cards: the bet, a play-to line read off the simulation's own distribution,
    the numbers, the reasons for and against with each reason's record, what would kill it, players to watch, the
    market read, and a status (New, Holding, Upgraded, Downgraded, Past play-to, Removed, Started, Won, Lost, Push)
  - leans: tonight's game lines, shown first and labelled as leans with their record (they lose so far)
  - changes: a running log of what changed, newest last, with the effect on the picks
  - narratives: the written editions (7am, about 2:40pm, about 6:30pm), written by a scheduled Claude session and saved
    with `python -m agent.briefing narrate FILE`, which rejects any number that is not in the facts
Every pick is graded at the line it was published at (the morning record is the headline record), again at the line
it had at tip (final record); removed picks and Claude's overrides are graded too (docs/data/briefing_track.json).

Stage 0 findings that shaped this: props win whether the picker keeps or drops them, game lines lose either way;
dropping a play because its conviction dipped avoided no losses; opening and closing lines grade the same. So picks
are sticky: a conviction dip shows as Downgraded and only a kill condition removes a pick.

    python -m agent.briefing tick            publish the morning edition when due, else update; grade
    python -m agent.briefing brief           the facts as text, for the writer
    python -m agent.briefing narrate FILE    validate and save a written edition
"""
import json
import re
import sys
from datetime import date, timedelta

from . import store

DIR = "briefing"
TRACK = "briefing_track.json"
PUBLISH_FROM = 6.75                 # ET hour: the 6:57am schedule may publish; anything earlier waits
STD_DEC = 1.87                      # -115, the price a play-to line assumes
EDGE = 0.0                          # play-to = break-even: past it, the bet loses money on the model's own numbers
ALERT_CAP = 6                       # phone alerts per day, the morning one included
FIRST_DAY = "2026-11-17"           # the first morning edition (built the evening of Nov 16: no "morning" edition at 7pm)
LEANS_SINCE = "2026-11-11"          # game-line record shown on the leans strip starts when conviction tiers started
STATN = {"pts": "points", "reb": "rebounds", "ast": "assists", "tpm": "threes", "pra": "pts+reb+ast", "ra": "reb+ast",
         "pr": "pts+reb", "pa": "pts+ast", "stl": "steals", "blk": "blocks"}
EDITIONS = ("morning", "midday", "final")


def _path(d):
    from . import config
    (config.DATA / DIR).mkdir(parents=True, exist_ok=True)
    return f"{DIR}/{d}.json"


def et_hour():
    n = store.et_now()
    return n.hour + n.minute / 60


def today():
    return store.et_now().date().isoformat()


def am(dec):
    if not dec or dec <= 1:
        return None
    return round((dec - 1) * 100) if dec >= 2 else -round(100 / (dec - 1))


def am_s(dec):
    a = am(dec)
    return "" if a is None else (f"+{a}" if a > 0 else f"−{abs(a)}")


def r3(x):
    return None if x is None else round(x, 3)


# ------------------------------------------------------------------ the simulation's distribution
def _hist_p(h, line, over):
    """Chance the simulated value lands over (or under) the line, from a {value: count} histogram."""
    if not h:
        return None
    n = sum(h.values())
    if not n:
        return None
    o = sum(c for v, c in h.items() if float(v) > line)
    u = sum(c for v, c in h.items() if float(v) < line)
    return (o if over else u) / n


def _p_at(card, sims, line):
    g = (sims.get("games") or {}).get(card["espn_id"]) or {}
    if card["kind"] == "prop":
        h = (((g.get("players") or {}).get(card["pid"]) or {}).get("h") or {}).get(card["stat"])
        return _hist_p(h, line, card["side"] == "Over")
    if card["kind"] == "total":
        return _hist_p(g.get("th"), line, card["side"] == "Over")
    if card["kind"] == "spread":
        mh = g.get("mh")
        if not mh:
            return None
        # home covers when margin + line > 0; away (line is the away team's own) when margin < line
        return _hist_p(mh, -line, True) if card["side"] == "home" else _hist_p(mh, line, False)
    return None


def play_to(card, sims):
    """The worst line (at a standard -115) and the worst price at tonight's line where the bet is still a bet.
    The model's chance at another line = its chance here, shifted by how much the simulation's chance moves."""
    p, L, dec = card.get("p"), card.get("point"), card.get("dec")
    if p is None or L is None:
        return None
    base = _p_at(card, sims, L)
    floor_dec = 1 / max(0.05, p - EDGE)
    out = {"floor_dec": round(floor_dec, 3), "floor_am": am(floor_dec), "line": L, "next": None}
    if base is None:
        return out
    worse = 1 if (card["kind"] in ("prop", "total") and card["side"] == "Over") else -1
    if card["kind"] == "spread":
        worse = -1                                         # a team's own spread gets worse as it falls (−4.5 → −5.5)
    need = 1 / STD_DEC + EDGE
    worst = L if p - 0 >= need else None
    for step in (1, 2, 3):
        L2 = L + worse * step
        q = _p_at(card, sims, L2)
        if q is None:
            break
        if p + (q - base) >= need:
            worst = L2
        else:
            out["next"] = L2
            break
    out["line"] = worst
    return out


def past_play_to(card, pub):
    """True when tonight's line or price has moved past the play-to set when the pick was published."""
    pt = pub.get("play_to") or {}
    L, dec = card.get("point"), card.get("dec")
    if L is None:
        return False
    worse = 1 if (card["kind"] in ("prop", "total") and card["side"] == "Over") else -1
    if card["kind"] == "spread":
        worse = -1
    if pt.get("line") is not None and (L - pt["line"]) * worse > 1e-9:
        return True
    if L == pub.get("point") and pt.get("floor_dec") and dec and dec < pt["floor_dec"] - 1e-9:
        return True
    return False


# ------------------------------------------------------------------ cards
def _rec(pat, tag, kind):
    t = ((pat or {}).get("tags") or {}).get(tag)
    if not t:
        return None
    w, l = t.get(kind, [0, 0])
    return [w, l] if w + l else None


def _reasons(case, pat):
    def rows(lst, kind):
        out = [{"tag": r["tag"], "t": r["t"], "rec": _rec(pat, r["tag"], kind)} for r in lst or []]
        # strongest track record first (reasons with fewer than 30 graded cases go last, in their original order)
        key = lambda r: -(r["rec"][0] / sum(r["rec"])) if r["rec"] and sum(r["rec"]) >= 30 else 1
        return sorted(out, key=key)
    return rows((case or {}).get("for"), "f"), rows((case or {}).get("against"), "a")


def _watch(x, pj):
    """Players whose status could change this bet: him, and rotation teammates and opponents carrying a tag."""
    P = pj.get("players") or {}
    me = P.get(x.get("pid") or "") or {}
    out = []
    if me and me.get("status"):
        out.append({"pid": me.get("pid"), "name": me.get("name"), "team": me.get("team"), "status": me.get("status"), "self": True, "min": me.get("minutes")})
    teams = {x.get("team"), x.get("opp")} if x["kind"] == "prop" else {x.get("home"), x.get("away")}
    for p in P.values():
        if p.get("pid") == x.get("pid") or p.get("team") not in teams or not p.get("status"):
            continue
        if (p.get("minutes") or 0) < (20 if p.get("team") == x.get("team") or x["kind"] != "prop" else 28):
            continue
        out.append({"pid": p.get("pid"), "name": p.get("name"), "team": p.get("team"), "status": p.get("status"), "self": False, "min": p.get("minutes")})
    return sorted(out, key=lambda w: (not w["self"], -(w["min"] or 0)))[:5]


def _kills(card, pt, watch):
    k = []
    if pt:
        if pt.get("next") is not None:
            k.append(f"The line reaching {pt['next']:g}" + (f" (still a bet at {pt['line']:g} near −115)" if pt.get("line") is not None and pt["line"] != card["point"] else ""))
        if pt.get("floor_am") is not None:
            k.append(f"The price at {card['point']:g} going past {('+' if pt['floor_am'] > 0 else '−') + str(abs(pt['floor_am']))}")
    for w in watch:
        if w["self"] and w["status"] not in ("Out",):
            k.append(f"{w['name']} is listed {w['status'].lower()}: if he is ruled out the bet is off")
    others = [w for w in watch if not w["self"] and w["status"] not in ("Out",)]
    if others:
        k.append("A change in " + ", ".join(f"{w['name']} ({w['status'].lower()})" for w in others[:3]) + ": the model re-prices on the next run")
    if card["kind"] == "prop":
        k.append("His minutes projection falling 15% or more")
    return k


def card_of(x, pj, sims, dk, pat):
    prop = x.get("kind") == "prop"
    if prop:
        key = f"p|{x['pid']}|{x['stat']}|{x['side']}"
        label = f"{x['name']} {x['side']} {x['point']:g} {STATN.get(x['stat'], x['stat'])}"
    else:
        key = f"g|{x['espn_id']}|{x['kind']}|{x['side']}"
        if x["kind"] == "total":
            label = f"{x['game']} {x['side']} {x['point']:g}"
        else:
            team = x.get("home") if x["side"] == "home" else x.get("away")
            label = f"{team} {'+' if x['point'] > 0 else ''}{x['point']:g} ({x['game']})"
    c = {"key": key, "kind": x["kind"], "label": label, "espn_id": x["espn_id"], "game": x.get("game"), "start": x.get("start"),
         "side": x["side"], "point": x["point"], "dec": x.get("dec"), "am": x.get("am") if x.get("am") is not None else am(x.get("dec")),
         "conv": x.get("conv"), "tier": x.get("tier"), "p": r3(x.get("p")), "p_sim": r3(x.get("p_sim")), "p_mkt": r3(x.get("p_mkt")),
         "p_kalshi": r3(x.get("p_kalshi")), "ev": r3(x.get("ev")), "verdict": x.get("verdict"), "home": x.get("home"), "away": x.get("away")}
    if prop:
        me = (pj.get("players") or {}).get(x["pid"]) or {}
        c.update({"pid": x["pid"], "name": x["name"], "team": x.get("team"), "opp": x.get("opp"), "stat": x["stat"], "proj": x.get("proj"),
                  "sim": x.get("sim"), "ob_ind": x.get("ob_ind"), "avg10": x.get("avg10"), "minutes": me.get("minutes")})
    else:
        c["why"] = x.get("why")
    c["for"], c["against"] = _reasons(x.get("case"), pat)
    c["play_to"] = play_to(c, sims)
    c["watch"] = _watch({**x, "team": c.get("team"), "opp": c.get("opp")}, pj)
    c["kill"] = _kills(c, c["play_to"], c["watch"])
    r = ((dk.get("props") or {}).get(f"{x['pid']}|{x['stat']}") if prop else None) or {}
    c["market"] = {"open": x.get("open") if x.get("open") is not None else (r.get("o") or [None])[0], "now": x["point"], "kalshi": r3(x.get("p_kalshi"))}
    return c


# ------------------------------------------------------------------ grading
def _grade(card, dk, line, dec):
    if line is None or not dec:
        return None
    if card["kind"] == "prop":
        r = (dk.get("props") or {}).get(f"{card['pid']}|{card['stat']}") or {}
        if r.get("res") == "void":
            return {"res": "void", "units": 0}
        a = r.get("actual")
        if a is None:
            return None
        d = (a - line) if card["side"] == "Over" else (line - a)
    else:
        g = (dk.get("games") or {}).get(card["espn_id"]) or {}
        if not g.get("final"):
            return None
        h, a = g["final"]
        if card["kind"] == "total":
            d = (h + a - line) if card["side"] == "Over" else (line - h - a)
        else:
            d = (h + line - a) if card["side"] == "home" else (a + line - h)
        a = h + a if card["kind"] == "total" else h - a
    res = "win" if d > 0 else "loss" if d < 0 else "push"
    return {"res": res, "units": round(dec - 1, 2) if res == "win" else -1 if res == "loss" else 0, "actual": a}


def _clv(card, dk, pub):
    """Did the line move our way after we published? Toward = the market moved to our side (we got the better number)."""
    if card["kind"] != "prop":
        return None
    r = (dk.get("props") or {}).get(f"{card['pid']}|{card['stat']}") or {}
    c = r.get("c")
    if not c:
        return None
    over = card["side"] == "Over"
    if c[0] != pub["point"]:
        return "toward" if (c[0] > pub["point"]) == over else "against"
    cd = c[1] if over else c[2]
    if not cd or not pub.get("dec"):
        return None
    return "toward" if cd < pub["dec"] - 0.005 else "against" if cd > pub["dec"] + 0.005 else "same"


# ------------------------------------------------------------------ the day file
def _load(d):
    return store.load(_path(d), None)


def has_morning(d=None):
    f = _load(d or today())
    return bool(f and any(e["id"] == "morning" for e in f.get("editions", [])))


def _now():
    return store.iso(store.now())


def _started(card, games):
    g = games.get(card["espn_id"])
    if g:
        return g.get("state") != "pre"
    try:
        return store.parse(card["start"]) <= store.now()
    except Exception:
        return False


def _status(c, pub, old):
    if c.get("result_final") or c.get("result"):
        r = (c.get("result_final") or c.get("result"))["res"]
        return {"win": "Won", "loss": "Lost", "push": "Push", "void": "Void"}.get(r, r)
    if c.get("removed"):
        return "Removed"
    if c.get("started"):
        return "Started"
    if past_play_to(c, pub):
        return "Past play-to"
    tiers = ["Pass", "Consider", "Confident"]
    if pub.get("conv") is not None and c.get("conv") is not None:
        dt = (tiers.index(c["tier"]) if c.get("tier") in tiers else 0) - (tiers.index(pub["tier"]) if pub.get("tier") in tiers else 0)
        if dt > 0 or c["conv"] - pub["conv"] >= 5:
            return "Upgraded"
        if dt < 0 or pub["conv"] - c["conv"] >= 5:
            return "Downgraded"
    if pub.get("edition") != "morning" and old is None:
        return "New"
    return "Holding" if pub.get("edition") == "morning" else "New"


def _slate(pj, cards):
    games = [g for g in pj.get("games") or []]
    pre = [g for g in games if g.get("state") == "pre"]
    starts = sorted(g["start"] for g in games if g.get("start"))
    q = [p for p in (pj.get("players") or {}).values() if p.get("status") and p.get("status") != "Out" and (p.get("minutes") or 0) >= 24]
    return {"games": len(games), "to_tip": len(pre), "first": starts[0] if starts else None,
            "questionable": [{"name": p["name"], "team": p["team"], "status": p["status"]} for p in sorted(q, key=lambda p: -(p.get("minutes") or 0))[:8]],
            "picks": len(cards)}


def _leans_record(best):
    w = l = 0
    u = 0.0
    for d, x in (best.get("days") or {}).items():
        if d < LEANS_SINCE:
            continue
        for b in x.get("games") or []:
            if b.get("res") == "win":
                w += 1
            elif b.get("res") == "loss":
                l += 1
            u += b.get("units") or 0 if b.get("res") in ("win", "loss", "push") else 0
    return {"w": w, "l": l, "u": round(u, 2), "since": LEANS_SINCE}


def _change(f, key, kind, text, alert=False):
    f["changes"].append({"at": _now(), "key": key, "kind": kind, "text": text, "alert": alert})


def _fmt_line(c):
    return f"{c['point']:g} at {am_s(c.get('dec'))}"


def build(d, f, edition=None, log=print):
    """Bring the day file up to date with best.json and the slate. edition='morning' publishes the morning edition."""
    best = store.load("best.json", {}) or {}
    day = (best.get("days") or {}).get(d)
    pj = store.load("players.json", {}) or {}
    sims = store.load("sims.json", {}) or {}
    dk = store.load(f"dk/{d}.json", {}) or {}
    if pj.get("date") != d:
        pj = {"games": [], "players": {}}
    if sims.get("date") != d:
        sims = {}
    pat = best.get("patterns") or {}
    games = {g["espn_id"]: g for g in pj.get("games") or []}
    now = _now()
    first = f is None
    if first:
        f = {"date": d, "version": 1, "created": now, "editions": [], "picks": {}, "leans": {}, "changes": [], "narratives": [],
             "overrides": [], "alerts": 0}
    cur_p = [card_of(x, pj, sims, dk, pat) for x in (day or {}).get("props") or []]
    cur_g = [card_of(x, pj, sims, dk, pat) for x in (day or {}).get("games") or []]
    dropped = {}
    for x in (day or {}).get("dropped") or []:
        k = (f"p|{x['pid']}|{x['stat']}|{x['side']}" if x.get("kind") == "prop" else f"g|{x['espn_id']}|{x['kind']}|{x['side']}")
        dropped[k] = x.get("drop_why")
    ed = edition or "update"
    alerts = []
    for store_key, cards in (("picks", cur_p), ("leans", cur_g)):
        book = f[store_key]
        seen = set()
        for c in cards:
            seen.add(c["key"])
            old = book.get(c["key"])
            if old and (old.get("started") or old.get("result_final")):
                continue                                                     # frozen at tip
            c["started"] = _started(c, games)
            if old is None:
                c["published"] = {"at": now, "edition": ed, "point": c["point"], "dec": c["dec"], "am": c["am"], "conv": c.get("conv"),
                                  "tier": c.get("tier"), "p": c.get("p"), "play_to": c.get("play_to")}
                c["first_seen"] = now
                if ed != "morning" and not first:
                    _change(f, c["key"], "added", f"Added: {c['label']} at {am_s(c['dec'])}" + (f", conviction {c['conv']}" if c.get('conv') is not None else ""), alert=store_key == "picks")
                    if store_key == "picks":
                        alerts.append((f"brief-add-{d}-{c['key']}", f"New pick: {c['label']} ({am_s(c['dec'])})"))
            else:
                c["published"], c["first_seen"] = old["published"], old["first_seen"]
                if old.get("removed"):
                    _change(f, c["key"], "back", f"Back on the board: {c['label']} at {am_s(c['dec'])}")
                if (old["point"], old.get("dec")) != (c["point"], c.get("dec")):
                    was_past = past_play_to(old, old["published"])
                    now_past = past_play_to(c, c["published"])
                    _change(f, c["key"], "line", f"{c['label'].split(' ' + str(c['side']))[0] if c['kind'] == 'prop' else c['label']}: line {_fmt_line(old)} → {_fmt_line(c)}"
                            + (" — past the play-to line; don't add at this number" if now_past and not was_past else ""), alert=now_past and not was_past and store_key == "picks")
                    if now_past and not was_past and store_key == "picks":
                        alerts.append((f"brief-past-{d}-{c['key']}", f"Past play-to: {c['label']} now {_fmt_line(c)}"))
                if old.get("tier") != c.get("tier") or abs((old.get("conv") or 0) - (c.get("conv") or 0)) >= 5:
                    _change(f, c["key"], "conviction", f"{c['label']}: conviction {old.get('conv')} {old.get('tier') or ''} → {c.get('conv')} {c.get('tier') or ''}".replace("  ", " "))
                ow = {w["name"]: w["status"] for w in old.get("watch") or []}
                for w in c["watch"]:
                    if w["name"] in ow and ow[w["name"]] != w["status"]:
                        _change(f, c["key"], "injury", f"{w['name']} {ow[w['name']].lower()} → {w['status'].lower()} ({c['label']} re-priced: {_fmt_line(c)}, conviction {c.get('conv')})")
                    elif w["name"] not in ow and w["status"] == "Out" and old.get("watch") is not None:
                        _change(f, c["key"], "injury", f"{w['name']} ruled out ({c['label']} re-priced: {_fmt_line(c)}, conviction {c.get('conv')})")
                if old.get("minutes") and c.get("minutes") and c["minutes"] < old["minutes"] * 0.85:
                    _change(f, c["key"], "minutes", f"{c['label']}: minutes projection {old['minutes']:.1f} → {c['minutes']:.1f}")
            c["removed"] = None
            book[c["key"]] = c
        # picks no longer on the board
        for k, old in list(book.items()):
            if k in seen or old.get("removed") or old.get("started") or old.get("result_final"):
                continue
            if _started(old, games):
                old["started"] = True
                continue
            why = dropped.get(k) or "no longer on Best Bets"
            old["removed"] = {"at": now, "why": why}
            _change(f, k, "removed", f"Removed: {old['label']} — {why}", alert=store_key == "picks")
            if store_key == "picks":
                alerts.append((f"brief-rm-{d}-{k}", f"Removed: {old['label']} ({why})"))
        for c in book.values():
            c["status"] = _status(c, c["published"], c)
    # grading: published line (morning / added / removed records) and the line at tip (final record)
    for book in (f["picks"], f["leans"]):
        for c in book.values():
            if c.get("result_final") and c.get("result"):
                continue
            g = _grade(c, dk, c["published"]["point"], c["published"]["dec"])
            if g:
                c["result"] = g
                if not c.get("removed"):
                    c["result_final"] = _grade(c, dk, c["point"], c["dec"])
                c["clv"] = _clv(c, dk, c["published"])
                c["status"] = _status(c, c["published"], c)
    if edition == "morning":
        f["editions"].append({"id": "morning", "at": now, "picks": list(f["picks"].keys()), "leans": list(f["leans"].keys())})
        top = sorted(f["picks"].values(), key=lambda c: -(c.get("conv") or 0))
        txt = (f"Morning edition: {len(f['picks'])} props, {len(f['leans'])} leans. " +
               "; ".join(f"{c['label']} {am_s(c['dec'])}" + (f" (good to {am_s(c['play_to']['floor_dec'])})" if c.get("play_to") else "") for c in top[:3]))
        if not f["picks"] and not f["leans"]:
            txt = "Morning edition: no plays clear the bar this morning." if pj.get("games") else "No NBA games today."
        alerts.insert(0, (f"brief-morning-{d}", txt))
    f["slate"] = _slate(pj, f["picks"]) if pj.get("games") or first else f.get("slate")
    f["leans_record"] = _leans_record(best)
    f["patterns_n"] = pat.get("n")
    f["updated"] = now
    if alerts and f.get("editions"):
        room = ALERT_CAP - f.get("alerts", 0)
        if room > 0:
            try:
                from . import alerts as al
                sent = al.send(alerts[:room], title="Perseverance briefing")
                f["alerts"] = f.get("alerts", 0) + sent
            except Exception as e:
                log(f"[briefing] alert failed: {e}")
    return f


# ------------------------------------------------------------------ the record
def records(days=60):
    import os
    from . import config
    d0 = config.DATA / DIR
    files = sorted(p.name[:-5] for p in d0.glob("*.json")) if d0.exists() else []
    z = lambda: {"w": 0, "l": 0, "p": 0, "u": 0.0}
    tot = {"morning": z(), "final": z(), "added": z(), "removed": z(), "leans": z(), "clv": {"toward": 0, "against": 0, "same": 0},
           "overrides": {"right": 0, "wrong": 0}}
    out = {"updated": _now(), "days": {}}

    def add(r, g):
        if not g or g["res"] not in ("win", "loss", "push"):
            return
        r[{"win": "w", "loss": "l", "push": "p"}[g["res"]]] += 1
        r["u"] = round(r["u"] + g["units"], 2)
    for d in files[-days:]:
        f = _load(d) or {}
        day = {"morning": z(), "final": z(), "added": z(), "removed": z(), "leans": z(), "clv": {"toward": 0, "against": 0, "same": 0},
               "overrides": {"right": 0, "wrong": 0}, "narratives": len(f.get("narratives") or [])}
        for c in (f.get("picks") or {}).values():
            pub = c.get("published") or {}
            if c.get("removed"):
                add(day["removed"], c.get("result"))
            elif pub.get("edition") == "morning":
                add(day["morning"], c.get("result"))
            else:
                add(day["added"], c.get("result"))
            if pub.get("edition") == "morning" and c.get("removed"):
                add(day["morning"], c.get("result"))               # morning record keeps its removed picks: as published
            if not c.get("removed"):
                add(day["final"], c.get("result_final"))
            if pub.get("edition") == "morning" and c.get("clv"):
                day["clv"][c["clv"]] += 1
        for c in (f.get("leans") or {}).values():
            add(day["leans"], c.get("result"))
        for o in f.get("overrides") or []:
            c = (f.get("picks") or {}).get(o["key"]) or (f.get("leans") or {}).get(o["key"])
            g = c and c.get("result")
            if g and g["res"] in ("win", "loss"):
                day["overrides"]["right" if g["res"] == "loss" else "wrong"] += 1
        out["days"][d] = day
        for k in ("morning", "final", "added", "removed", "leans"):
            for m in ("w", "l", "p"):
                tot[k][m] += day[k][m]
            tot[k]["u"] = round(tot[k]["u"] + day[k]["u"], 2)
        for m in ("toward", "against", "same"):
            tot["clv"][m] += day["clv"][m]
        for m in ("right", "wrong"):
            tot["overrides"][m] += day["overrides"][m]
    out["total"] = tot
    return out


def tick(edition=None, log=print):
    """Called after every agent run. Publishes the morning edition once it is due, otherwise keeps today's file
    current; grades yesterday's file once its games are final."""
    d = today()
    f = _load(d)
    pj = store.load("players.json", {}) or {}
    best = store.load("best.json", {}) or {}
    if f is None and d < FIRST_DAY:
        log(f"[briefing] first edition is {FIRST_DAY}")
    elif f is None:
        if et_hour() < PUBLISH_FROM:
            log("[briefing] before 6:45am ET: nothing to publish yet")
        elif pj.get("date") != d or d not in (best.get("days") or {}):
            if pj.get("date") == d and not pj.get("games"):
                f = build(d, None, "morning", log)
                store.save(_path(d), f)
                log("[briefing] no games today: morning edition says so")
            else:
                log("[briefing] today's research has not run yet: the morning edition waits")
        else:
            f = build(d, None, "morning", log)
            store.save(_path(d), f)
            log(f"[briefing] morning edition published: {len(f['picks'])} props, {len(f['leans'])} leans")
    else:
        n0 = len(f["changes"])
        f = build(d, f, edition, log)
        store.save(_path(d), f)
        log(f"[briefing] updated: {len(f['changes']) - n0} change(s)")
    y = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
    fy = _load(y)
    if fy and any(not (c.get("result") or c.get("removed") and c.get("result")) for c in list(fy["picks"].values()) + list(fy["leans"].values())):
        fy = build(y, fy, None, log)
        store.save(_path(y), fy)
    store.save(TRACK, records())


# ------------------------------------------------------------------ the writer (a scheduled Claude session)
def _numbers_in(obj, acc):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        x = float(obj)
        for v in (x, round(x, 1), round(x), abs(x)):
            acc.add(_norm(v))
        if 0 < abs(x) < 1:
            acc.update({_norm(round(x * 100)), _norm(round(x * 100, 1)), _norm(round(abs(x) * 100))})
        if 1 < x < 30 and x != int(x):                              # a decimal price: its American odds too
            a = am(x)
            if a is not None:
                acc.update({_norm(a), _norm(abs(a))})
        return
    if isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj.replace(",", "")):
            acc.add(_norm(float(m)))
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _numbers_in(v, acc)


def _norm(v):
    v = abs(float(v))
    return str(int(v)) if v == int(v) else f"{v:g}"


def unverified(text, allowed):
    t = re.sub(r"\b\d{1,2}:\d{2}\s*(am|pm|a\.m\.|p\.m\.)?", " ", text, flags=re.I)        # clock times
    t = re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}\b", " ", t)   # dates
    t = re.sub(r"\b20\d\d\b", " ", t)
    bad = []
    for m in re.findall(r"\d+(?:\.\d+)?", t.replace(",", "")):
        v = float(m)
        if v == int(v) and v <= 10:
            continue
        if _norm(v) not in allowed:
            bad.append(m)
    return sorted(set(bad))


def brief(d=None):
    """The facts as plain text, for the writer."""
    d = d or today()
    f = _load(d)
    if not f:
        return f"No briefing file for {d} yet (the morning edition publishes after 6:45am ET once today's research has run)."
    tr = store.load(TRACK, {}) or {}
    L = [f"# Facts for the {d} Daily Briefing (updated {f['updated']})", ""]
    s = f.get("slate") or {}
    L.append(f"Slate: {s.get('games', 0)} games, {s.get('to_tip', 0)} still to tip, first tip {s.get('first')}. "
             f"Rotation players with a status tag: " + (", ".join(f"{q['name']} ({q['team']}, {q['status']})" for q in s.get("questionable") or []) or "none") + ".")
    t = (tr.get("total") or {})
    if t:
        m, fi, lr = t["morning"], t["final"], f.get("leans_record") or {}
        L.append(f"Report record: morning {m['w']}-{m['l']}-{m['p']} ({m['u']:+.2f}u), final {fi['w']}-{fi['l']}-{fi['p']} ({fi['u']:+.2f}u), "
                 f"closing-line moves on morning picks {t['clv']['toward']} toward / {t['clv']['against']} against. "
                 f"Game lines since {lr.get('since')}: {lr.get('w')}-{lr.get('l')} ({lr.get('u', 0):+.2f}u).")
    L += ["", "## Leans (game lines; they have lost so far, so they are leans, never best bets)"]
    for c in f["leans"].values():
        L.append(f"- [{c['key']}] {c['label']} {am_s(c['dec'])} · {c['status']} · sim {round((c.get('p_sim') or 0) * 100)}% vs DraftKings {round((c.get('p_mkt') or 0) * 100)}% · {c.get('why') or ''}")
    L += ["", "## Props (the report's picks)"]
    for c in sorted(f["picks"].values(), key=lambda c: -(c.get("conv") or 0)):
        pub, pt = c["published"], c.get("play_to") or {}
        L.append(f"### [{c['key']}] {c['label']} · {am_s(c['dec'])} · {c['status']}")
        L.append(f"Published {pub['edition']} at {pub['point']:g} {am_s(pub['dec'])}. Conviction {c.get('conv')} ({c.get('tier')}); model {round((c.get('p') or 0) * 100)}%, "
                 f"sim {round((c.get('p_sim') or 0) * 100)}%, DraftKings {round((c.get('p_mkt') or 0) * 100)}%"
                 + (f", Kalshi {round(c['p_kalshi'] * 100)}%" if c.get("p_kalshi") else "") + (f", our sealed line {c['ob_ind']:g}" if c.get("ob_ind") is not None else "")
                 + (f", projection {c['proj']}" if c.get("proj") is not None else "") + (f", minutes {c['minutes']:.1f}" if c.get("minutes") else "") + ".")
        if pt:
            L.append(f"Play-to: good at {c['point']:g} down to {am_s(pt['floor_dec'])}" + (f"; still a bet at {pt['line']:g} near −115" if pt.get("line") not in (None, c["point"]) else "")
                     + (f"; not at {pt['next']:g}" if pt.get("next") is not None else "") + ".")
        for r in c["for"]:
            L.append(f"- FOR ({r['tag']}{', record ' + str(r['rec'][0]) + '-' + str(r['rec'][1]) if r['rec'] else ''}): {r['t']}")
        for r in c["against"]:
            L.append(f"- AGAINST ({r['tag']}{', record when it argued against: ' + str(r['rec'][0]) + '-' + str(r['rec'][1]) if r['rec'] else ''}): {r['t']}")
        L.append("Kill: " + "; ".join(c["kill"]))
        if c.get("watch"):
            L.append("Watch: " + ", ".join(f"{w['name']} ({w['team']}, {w['status']})" for w in c["watch"]))
        if c.get("removed"):
            L.append(f"Removed {c['removed']['at']}: {c['removed']['why']}")
    if f["changes"]:
        L += ["", "## Changes today (oldest first)"] + [f"- {x['at']} {x['text']}" for x in f["changes"]]
    y = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
    yd = (tr.get("days") or {}).get(y)
    if yd:
        L += ["", f"## Yesterday ({y}): morning {yd['morning']['w']}-{yd['morning']['l']}-{yd['morning']['p']} ({yd['morning']['u']:+.2f}u), "
                  f"final {yd['final']['w']}-{yd['final']['l']} ({yd['final']['u']:+.2f}u), leans {yd['leans']['w']}-{yd['leans']['l']}."]
    return "\n".join(L)


def narrate(path, log=print):
    """Validate a written edition and save it into today's file. Returns (ok, message)."""
    try:
        n = json.loads(open(path).read())
    except Exception as e:
        return False, f"not valid JSON: {e}"
    d = n.get("date") or today()
    f = _load(d)
    if not f:
        return False, f"no briefing file for {d}"
    ed = n.get("edition")
    if ed not in EDITIONS:
        return False, f"edition must be one of {EDITIONS}"
    for k in ("headline", "briefing"):
        if not isinstance(n.get(k), str) or not n[k].strip():
            return False, f"missing {k}"
    if len(n["headline"]) > 140 or len(n["briefing"]) > 1400:
        return False, "headline max 140 characters, briefing max 1,400"
    cards = n.get("cards") or {}
    keys = set(f["picks"]) | set(f["leans"])
    bad_keys = [k for k in cards if k not in keys]
    if bad_keys:
        return False, f"unknown card keys: {bad_keys}"
    if any(len(v) > 600 for v in cards.values()):
        return False, "each card note max 600 characters"
    flags = n.get("flags") or []
    for fl in flags:
        if fl.get("key") not in keys or fl.get("action") not in ("flag", "demote") or not fl.get("reason"):
            return False, f"bad flag {fl}: needs a known key, action flag or demote, and a reason"
    allowed = set()
    _numbers_in({k: v for k, v in f.items() if k not in ("narratives",)}, allowed)
    _numbers_in(store.load(TRACK, {}) or {}, allowed)
    _numbers_in([fl.get("reason", "") for fl in flags if fl.get("source")], allowed)    # numbers quoted from a cited source
    texts = [n["headline"], n["briefing"], *cards.values(), n.get("leans_note") or ""]
    bad = sorted({x for t in texts for x in unverified(t, allowed)})
    if bad:
        return False, "these numbers are not in the facts: " + ", ".join(bad) + ". Use only numbers from `python -m agent.briefing brief`."
    srcs = [s for s in n.get("sources") or [] if isinstance(s, dict) and s.get("url", "").startswith("http")]
    if flags and any(not fl.get("source") for fl in flags):
        return False, "every flag needs a source (a URL for the news it rests on)"
    entry = {"edition": ed, "at": _now(), "author": "claude", "headline": n["headline"].strip(), "briefing": n["briefing"].strip(),
             "cards": cards, "leans_note": n.get("leans_note"), "sources": srcs, "changes_at_write": len(f["changes"])}
    f["narratives"].append(entry)
    for fl in flags:
        f["overrides"].append({"at": entry["at"], "edition": ed, **{k: fl.get(k) for k in ("key", "action", "reason", "source")}})
        _change(f, fl["key"], "override", f"Writer {fl['action']}s {((f['picks'].get(fl['key']) or f['leans'].get(fl['key'])) or {}).get('label')}: {fl['reason']}")
    f["updated"] = entry["at"]
    store.save(_path(d), f)
    store.save(TRACK, records())
    return True, f"{ed} edition saved ({len(cards)} card notes, {len(flags)} flags)"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "tick":
        tick()
    elif cmd == "brief":
        print(brief(sys.argv[2] if len(sys.argv) > 2 else None))
    elif cmd == "narrate":
        ok, msg = narrate(sys.argv[2])
        print(("OK: " if ok else "REJECTED: ") + msg)
        sys.exit(0 if ok else 1)
