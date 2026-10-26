"""The Odds API client with a hard credit budget so the free tier never runs dry."""
import calendar
import hashlib
import json
import math
import random
import urllib.parse
import urllib.request
from datetime import timedelta

from . import config, store


class OutOfBudget(Exception):
    pass


class Budget:
    """Tracks credits. Two rules: never drop below the reserve, and spread the
    rest evenly across the scans left this month."""

    def __init__(self):
        self.b = store.load("budget.json", {})
        month = store.now().strftime("%Y-%m")
        if self.b.get("month") != month:
            self.b = {"month": month, "remaining": None, "used": None,
                      "runs": 0, "runs_day": "", "runs_today": 0, "log": [],
                      "monthly": config.MONTHLY_CREDITS, "reserve": config.RESERVE_CREDITS}
        self.spent_this_run = 0
        self.allowance = 0

    def start_run(self):
        today = store.now().strftime("%Y-%m-%d")
        if self.b.get("runs_day") != today:
            self.b["runs_day"], self.b["runs_today"] = today, 0
        n = store.now()
        last = calendar.monthrange(n.year, n.month)[1]
        runs_left = max(1, (last - n.day + 1) * config.RUNS_PER_DAY - self.b["runs_today"])
        rem = self.b["remaining"] if self.b["remaining"] is not None else config.MONTHLY_CREDITS
        spendable = max(0, rem - config.RESERVE_CREDITS)
        self.allowance = spendable // runs_left
        self.b["runs"] += 1
        self.b["runs_today"] += 1
        self.b["runs_left"] = runs_left
        self.b["allowance"] = self.allowance

    def can(self, cost: int, essential=False) -> bool:
        rem = self.b["remaining"]
        if rem is not None and rem - cost < config.RESERVE_CREDITS:
            return False
        return essential or self.spent_this_run + cost <= self.allowance

    def record(self, endpoint, cost, remaining, used):
        self.spent_this_run += cost
        if remaining is not None:
            self.b["remaining"] = remaining
        if used is not None:
            self.b["used"] = used
        self.b["log"] = (self.b["log"] + [{"ts": store.iso(store.now()), "ep": endpoint,
                                           "cost": cost, "rem": self.b["remaining"]}])[-60:]

    def save(self):
        self.b["updated"] = store.iso(store.now())
        store.save("budget.json", self.b)


class OddsClient:
    def __init__(self, budget: Budget):
        self.budget = budget

    # -- transport --------------------------------------------------------
    def _get(self, path, params, cost_est, essential=False):
        if cost_est and not self.budget.can(cost_est, essential):
            raise OutOfBudget(path)
        if config.MOCK:
            data, cost = _mock(path, params), cost_est
            rem = (self.budget.b["remaining"] if self.budget.b["remaining"] is not None
                   else config.MONTHLY_CREDITS) - cost
            self.budget.record(path, cost, rem, None)
            return data
        if not config.API_KEY:
            raise RuntimeError("ODDS_API_KEY is not set")
        q = urllib.parse.urlencode({**params, "apiKey": config.API_KEY})
        req = urllib.request.Request(f"{config.API_BASE}{path}?{q}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                h = r.headers
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 429):
                self.budget.b["remaining"] = 0
                raise OutOfBudget(f"{path}: HTTP {e.code}")
            raise
        num = lambda k: int(float(h[k])) if h.get(k) not in (None, "") else None
        self.budget.record(path, num("x-requests-last") or 0, num("x-requests-remaining"),
                           num("x-requests-used"))
        return data

    # -- endpoints --------------------------------------------------------
    def sports(self):                       # free
        return self._get("/sports", {}, 0)

    def events(self, sport):                # free
        return self._get(f"/sports/{sport}/events", {}, 0)

    def odds(self, sport):
        markets = config.markets_for(sport)
        cost = len(markets.split(",")) * len(config.REGIONS.split(","))
        n = store.now()
        return self._get(f"/sports/{sport}/odds", {
            "regions": config.REGIONS, "markets": markets, "oddsFormat": "decimal",
            "commenceTimeFrom": store.iso(n),
            "commenceTimeTo": store.iso(n + timedelta(hours=config.WINDOW_MAX_H)),
        }, cost)

    def scores(self, sport, essential=True):
        return self._get(f"/sports/{sport}/scores", {"daysFrom": 3}, 2, essential)


# ---- Mock market for offline testing ----------------------------------------
MOCK_SPORTS = ["basketball_nba", "icehockey_nhl", "soccer_epl"]
BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "espnbet", "betrivers", "fanatics", "bovada"]


def _rng(*parts):
    return random.Random(int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


def _mock_events():
    n = store.now()
    day0 = n.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for d in range(-3, 2):
        day = day0 + timedelta(days=d)
        for sport in MOCK_SPORTS:
            for g in range(4):
                eid = f"mock-{sport}-{day:%Y%m%d}-{g}"
                start = day + timedelta(hours=23, minutes=30 * g)
                out.append({"id": eid, "sport_key": sport, "commence_time": store.iso(start),
                            "home_team": f"{sport.split('_')[-1].upper()} Home{g}",
                            "away_team": f"{sport.split('_')[-1].upper()} Away{g}"})
    return out


def _truth(ev):
    r = _rng(ev["id"])
    if ev["sport_key"].startswith("soccer"):
        lh, la = r.uniform(0.8, 2.0), r.uniform(0.6, 1.7)
        def pois(lam):
            u, k, p = r.random(), 0, math.exp(-lam)
            c = p
            while u > c and k < 15:
                k += 1; p *= lam / k; c += p
            return k
        return {"lh": lh, "la": la, "hs": pois(lh), "as": pois(la), "soccer": True}
    scale = 1.0 if ev["sport_key"].startswith("basketball") else 0.12
    d, t = r.gauss(0, 6) * scale, (220 if scale == 1 else 6) + r.gauss(0, 8) * scale
    sd = 9 * scale
    hs = round((t + d) / 2 + r.gauss(0, sd)); as_ = round((t - d) / 2 + r.gauss(0, sd))
    if hs == as_:
        hs += 1
    return {"d": d, "t": t, "sd": sd, "hs": hs, "as": as_, "soccer": False}


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _mock_market(ev, tr):
    """True probabilities for each market, keyed like the real API."""
    H, A = ev["home_team"], ev["away_team"]
    mk = {}
    if tr["soccer"]:
        r = _rng("sim", ev["id"])
        sims = [(sum(r.random() < tr["lh"] / 10 for _ in range(10)),
                 sum(r.random() < tr["la"] / 10 for _ in range(10))) for _ in range(600)]
        ph = sum(h > a for h, a in sims) / 600; pa = sum(a > h for h, a in sims) / 600
        mk["h2h"] = [(H, None, ph), (A, None, pa), ("Draw", None, 1 - ph - pa)]
        po = sum(h + a > 2.5 for h, a in sims) / 600
        mk["totals"] = [("Over", 2.5, po), ("Under", 2.5, 1 - po)]
    else:
        s = tr["sd"] * math.sqrt(2)
        ph = _phi(tr["d"] / s)
        mk["h2h"] = [(H, None, ph), (A, None, 1 - ph)]
        line = round(tr["d"] * 2) / 2 or 0.5
        pc = _phi((tr["d"] - line) / s)
        mk["spreads"] = [(H, -line, pc), (A, line, 1 - pc)]
        tl = round(tr["t"] * 2) / 2 + 0.5
        po = 1 - _phi((tl - tr["t"]) / s)
        mk["totals"] = [("Over", tl, po), ("Under", tl, 1 - po)]
    return mk


def _mock(path, params):
    evs = _mock_events()
    n = store.now()
    if path == "/sports":
        return [{"key": s, "group": s.split("_")[0], "title": s, "active": True,
                 "has_outrights": False} for s in MOCK_SPORTS]
    sport = path.split("/")[2]
    evs = [e for e in evs if e["sport_key"] == sport]
    if path.endswith("/events"):
        return [e for e in evs if store.parse(e["commence_time"]) > n]
    if path.endswith("/scores"):
        out = []
        for e in evs:
            st = store.parse(e["commence_time"])
            if st > n or st < n - timedelta(days=3):
                continue
            done = n > st + timedelta(hours=3)
            tr = _truth(e)
            out.append({**e, "completed": done, "scores": [
                {"name": e["home_team"], "score": str(tr["hs"])},
                {"name": e["away_team"], "score": str(tr["as"])}] if done else None})
        return out
    # odds
    hi = store.parse(params["commenceTimeTo"])
    wanted = params["markets"].split(",")
    out = []
    for e in evs:
        st = store.parse(e["commence_time"])
        if not (n < st <= hi):
            continue
        tr = _truth(e)
        mk = _mock_market(e, tr)
        books = []
        for b in BOOKS:
            r = _rng("book", e["id"], b, n.strftime("%Y%m%d%H"))
            markets = []
            for key in wanted:
                if key not in mk:
                    continue
                outs = []
                for name, point, p in mk[key]:
                    implied = p * (1 + r.uniform(0.035, 0.06)) + r.gauss(0, 0.012)
                    price = 1 / min(max(implied, 0.02), 0.97)
                    if r.random() < 0.06:          # a book that's off the market
                        price *= r.uniform(1.04, 1.12)
                    o = {"name": name, "price": round(price, 3)}
                    if point is not None:
                        o["point"] = point
                    outs.append(o)
                markets.append({"key": key, "outcomes": outs})
            books.append({"key": b, "title": b.title(), "markets": markets})
        out.append({**e, "bookmakers": books})
    return out
