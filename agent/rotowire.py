"""Projected and confirmed starting lineups from RotoWire's public NBA lineups page.

One page fetch per research run (a few a day). What the free page shows, and what this reads:
  - each team's five starters with position and RotoWire's "likely to play" percentage,
  - whether the lineup is Confirmed or Expected,
  - the MAY NOT PLAY list with injury status (Out, GTD, Doubtful, Questionable...),
  - a link to each player's RotoWire profile.
RotoWire's projected minutes and its stat projections are for subscribers only, so they are not
read here; the dashboard links to each player's RotoWire page instead.
"""
import html
import re
import urllib.request

URL = "https://www.rotowire.com/basketball/nba-lineups.php"
BASE = "https://www.rotowire.com"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Perseverance research bot)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _players(block):
    out = []
    for m in re.finditer(r'<li class="lineup__player([^"]*)"([^>]*)>(.*?)</li>', block, re.S):
        cls, attrs, inner = m.group(1), m.group(2), m.group(3)
        tt = re.search(r'title="([^"]*)"', attrs)
        title = tt.group(1) if tt else ""
        pos = re.search(r'lineup__pos"[^>]*>([^<]*)<', inner)
        a = re.search(r'<a title="([^"]+)" href="([^"]+)"', inner)
        inj = re.search(r'lineup__inj">([^<]*)<', inner)
        pct = re.search(r"is-pct-play-(\d+)", cls)
        if not a:
            continue
        out.append({"name": html.unescape(a.group(1)), "url": BASE + a.group(2),
                    "pos": (pos.group(1).strip() if pos else ""), "inj": (inj.group(1).strip() if inj else ""),
                    "pct": int(pct.group(1)) if pct else None, "note": html.unescape(title)})
    return out


def _team_list(ul):
    status = re.search(r'lineup__status[^"]*"[^>]*>.*?</div>\s*([^<]+)<', ul, re.S)
    nick = re.search(r'data-nickname="([^"]+)"', ul)
    abbr = re.search(r'data-team="([^"]+)"', ul)
    parts = re.split(r'lineup__title is-middle">', ul, maxsplit=1)
    starters = _players(parts[0])
    out_list = _players(parts[1]) if len(parts) > 1 else []
    return {"status": (status.group(1).strip() if status else ""), "nick": nick.group(1) if nick else "",
            "abbr": abbr.group(1) if abbr else "", "starters": starters, "may_not_play": out_list}


def fetch():
    """[{time, visit:{...}, home:{...}}] for every game on the page (today's slate)."""
    page = _get(URL)
    games = []
    chunks = re.split(r'<div class="lineup is-nba[^"]*"', page)[1:]
    for c in chunks:
        if "lineup__list" not in c:
            continue
        t = re.search(r'lineup__time">([^<]+)<', c)
        v = re.search(r'<ul class="lineup__list is-visit">(.*?)</ul>', c, re.S)
        h = re.search(r'<ul class="lineup__list is-home">(.*?)</ul>', c, re.S)
        if not v or not h:
            continue
        visit, home = _team_list(v.group(1)), _team_list(h.group(1))
        # nicknames also appear in the matchup header if the buttons are missing
        names = re.findall(r'lineup__mteam is-(visit|home)[^>]*>\s*([A-Za-z0-9 .]+?)\s*<span', c)
        for side, nm in names:
            (visit if side == "visit" else home)["nick"] = visit["nick"] if side == "visit" and visit["nick"] else \
                home["nick"] if side == "home" and home["nick"] else nm.strip()
        games.append({"time": t.group(1).strip() if t else "", "visit": visit, "home": home})
    return games


def by_player(games, norm):
    """{normalized player name: {team_nick, starter, pos, status, inj, pct, url}}"""
    out = {}
    for g in games:
        for side in ("visit", "home"):
            t = g[side]
            for p in t["starters"]:
                out[norm(p["name"])] = {"nick": t["nick"], "starter": True, "pos": p["pos"], "status": t["status"],
                                        "inj": p["inj"], "pct": p["pct"], "url": p["url"], "note": p["note"]}
            for p in t["may_not_play"]:
                out.setdefault(norm(p["name"]), {"nick": t["nick"], "starter": False, "pos": p["pos"],
                                                 "status": t["status"], "inj": p["inj"] or "Questionable",
                                                 "pct": p["pct"], "url": p["url"], "note": p["note"]})
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(fetch()[:2], indent=1))
