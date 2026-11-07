"""DraftKings lines from ESPN's free odds feed (no Odds API credits): game lines with their opening,
closing and current numbers, and DraftKings' player props with opening and current lines."""
import json
import urllib.request

CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
DK = "100"                                   # ESPN's provider id for DraftKings (pregame: open, close)
DK_LIVE = "200"                              # "DraftKings - Live Odds": the in-game line (provider 100's "current" stays frozen at the close)
PROP_TYPES = {"Total Points": "pts", "Total Rebounds": "reb", "Total Assists": "ast", "Total 3-Point Field Goals": "tpm",
              "Total Points, Rebounds, and Assists": "pra", "Total Assists and Rebounds": "ra"}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _f(x, k="decimal"):
    try:
        v = x.get(k) if isinstance(x, dict) else None
        if v is None and isinstance(x, dict):
            v = x.get("american") if k == "point" else x.get("value")
        return float(str(v).replace("+", "")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _snap(item, state):
    """One state ('open', 'close' or 'current') of DraftKings' spread, total and moneyline."""
    t, h, a = item.get(state) or {}, (item.get("homeTeamOdds") or {}).get(state) or {}, (item.get("awayTeamOdds") or {}).get(state) or {}
    pt = lambda d: _f((d or {}).get("pointSpread"), "point") if (d or {}).get("pointSpread") else None
    s = {"sp": pt(h), "sp_h": _f(h.get("spread")), "sp_a": _f(a.get("spread")),
         "tot": _f(t.get("total"), "point") if t.get("total") else None, "ov": _f(t.get("over")), "un": _f(t.get("under")),
         "ml_h": _f(h.get("moneyLine")), "ml_a": _f(a.get("moneyLine"))}
    return s if any(v is not None for v in s.values()) else None


def game_odds(eid, live=False):
    """open / close / current DraftKings game lines. live=True takes "current" from the in-game provider."""
    d = _get(f"{CORE}/events/{eid}/competitions/{eid}/odds")
    items = {str((i.get("provider") or {}).get("id")): i for i in d.get("items", [])}
    item, lv = items.get(DK), items.get(DK_LIVE)
    if live:
        try:                                    # the provider's own endpoint is often fresher than the list
            one = _get(f"{CORE}/events/{eid}/competitions/{eid}/odds/{DK_LIVE}")
            if one.get("current"):
                lv = one
        except Exception:
            pass
    if not item and not (lv and live):
        return None
    out = {k: _snap(item, k) for k in ("open", "close", "current")} if item else {"open": None, "close": None, "current": None}
    cur = _snap(lv, "current") if lv and live else None
    if cur:                                   # during a game the live provider has the real in-game number
        out["current"] = cur
    return out


def props(eid):
    """{(athlete_id, stat): {"open": [line, over, under], "cur": [line, over, under]}}. ESPN lists each prop as
    two entries without labels: the over first, then the under (checked against the model: when the line sits
    above a player's norm the first price is the longer one)."""
    d = _get(f"{CORE}/events/{eid}/competitions/{eid}/odds/{DK}/propBets?lang=en&region=us&limit=1000")
    pairs = {}
    for i in d.get("items", []):
        st = PROP_TYPES.get((i.get("type") or {}).get("name"))
        if not st:
            continue
        aid = i["athlete"]["$ref"].split("/athletes/")[1].split("?")[0]
        cur = (i.get("current") or {}).get("target", {}).get("value")
        opn = (i.get("open") or {}).get("target", {}).get("value")
        od = (i.get("odds") or {}).get("decimal") or {}
        pairs.setdefault((aid, st), []).append((cur, opn, _f(od, "value"), _f(od, "open")))
    out = {}
    for k, v in pairs.items():
        if len(v) < 2:
            continue
        (c1, o1, p1, q1), (c2, o2, p2, q2) = v[0], v[1]
        out[k] = {"cur": [c1, p1, p2], "open": [o1 if o1 is not None else c1, q1 or p1, q2 or p2]}
    return out


def live_games(day_str):
    """Games on an ET date with their state, score, period and clock."""
    d = _get(f"{SITE}/scoreboard?dates={day_str}&limit=50")
    out = []
    for e in d.get("events", []):
        c = e["competitions"][0]
        t = {x["homeAway"]: x for x in c["competitors"]}
        st = e["status"]
        out.append({"espn_id": e["id"], "start": e["date"], "state": st["type"].get("state"), "period": st.get("period", 0),
                    "clock": st.get("displayClock", ""), "detail": st["type"].get("shortDetail", ""),
                    "home": t["home"]["team"]["abbreviation"], "away": t["away"]["team"]["abbreviation"],
                    "home_score": int(t["home"].get("score") or 0), "away_score": int(t["away"].get("score") or 0)})
    return out
