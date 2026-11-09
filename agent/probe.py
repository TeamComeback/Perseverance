"""Reachability check for outside data sources from wherever the agent runs (GitHub's servers differ from a laptop)."""
import time
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
NBA_HEADERS = {"User-Agent": UA, "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com", "Accept": "application/json, text/plain, */*",
               "x-nba-stats-origin": "stats", "x-nba-stats-token": "true", "Accept-Language": "en-US,en;q=0.9"}
URLS = {
    "nba_passing": ("https://stats.nba.com/stats/leaguedashptstats?LastNGames=0&LeagueID=00&Month=0&OpponentTeamID=0&PORound=0&PerMode=PerGame"
                    "&PlayerOrTeam=Player&PtMeasureType=Passing&Season=2026-27&SeasonType=Regular%20Season&TeamID=0", NBA_HEADERS),
    "nba_rebounding": ("https://stats.nba.com/stats/leaguedashptstats?LastNGames=0&LeagueID=00&Month=0&OpponentTeamID=0&PORound=0&PerMode=PerGame"
                       "&PlayerOrTeam=Player&PtMeasureType=Rebounding&Season=2026-27&SeasonType=Regular%20Season&TeamID=0", NBA_HEADERS),
    "nba_cdn": ("https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json", {"User-Agent": UA}),
    "pbpstats": ("https://api.pbpstats.com/get-totals/nba?Season=2026-27&SeasonType=Regular%2BSeason&Type=Team", {"User-Agent": UA}),
    "refs": ("https://official.nba.com/wp-json/api/v1/get-game-officials?&date=2026-11-08", {"User-Agent": UA}),
}


def run():
    out = {}
    for name, (url, headers) in URLS.items():
        t = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as r:
                body = r.read()
                out[name] = (r.status, len(body), round(time.time() - t, 1))
        except Exception as e:
            out[name] = ("ERR", str(e)[:80], round(time.time() - t, 1))
        print(f"[probe] {name}: {out[name]}")
    return out
