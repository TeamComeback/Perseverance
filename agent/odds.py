"""The Odds API client with a hard credit budget: the paid plan is spread over its billing cycle and never runs dry."""
import calendar
import hashlib
import json
import math
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from . import config, store


class OutOfBudget(Exception):
    pass


def _et():
    return store.et_now()


def cycle_bounds(now=None):
    """The billing cycle: the paid plan renews on config.RESET_DAY at 00:00 UTC each month (Nov 17 2026 purchase)."""
    from datetime import datetime, timezone
    n = now or store.now()

    def at(y, m):
        return datetime(y, m, min(config.RESET_DAY, calendar.monthrange(y, m)[1]), tzinfo=timezone.utc)
    start = at(n.year, n.month)
    if n < start:
        start = at(n.year - 1, 12) if n.month == 1 else at(n.year, n.month - 1)
    end = at(start.year + 1, 1) if start.month == 12 else at(start.year, start.month + 1)
    return start, end


class Budget:
    """Tracks credits. Rules: never drop below the reserve, never touch the old free key, spread the rest evenly.

    Paid plan (Nov 17 2026): 20,000 credits a billing cycle, renewing on the billing date (config.RESET_DAY), not the
    1st. What's left above the reserve and the Backtester's untouched allotment is divided by the days left in the
    cycle to give today's budget. Within the day a scan may be at most a little ahead of an even pace from 9am to 1am
    ET (config.PACE_HEADROOM), so the morning can't eat the evening; prop and closing-line runs are only held to the
    day's budget. Every run is capped at config.RUN_CAP credits as a runaway guard.

    Old free key: the API reports used + remaining on every call, which is the key's plan size. If that is under
    config.PAID_MIN_PLAN, the secret still holds the old free key, whose balance now belongs to the 2H-Comeback bot
    (owner, Nov 17), so nothing is spent until the paid key is in place.

    Shared key (Nov 17 2026): every balance the API reports is still compared with the last one minus what this agent
    spent; any difference is another app's use (`external`) and is set aside, as before."""

    def __init__(self):
        self.b = store.load("budget.json", {})
        c0, c1 = cycle_bounds()
        cyc = c0.strftime("%Y-%m-%d")
        if self.b.get("month") != cyc:
            carry = {k: self.b[k] for k in ("ext_days", "plan") if k in self.b}
            self.b = {"month": cyc, "remaining": None, "used": None, "runs": 0, "log": [], "paid": [], "ext": [],
                      "own_month": 0, "external_month": 0, "monthly": config.MONTHLY_CREDITS,
                      "reserve": config.RESERVE_CREDITS, "jobs_cycle": {}, "backtest_used": 0, **carry}
        self.b.update(cycle_start=store.iso(c0), cycle_end=store.iso(c1), monthly=config.MONTHLY_CREDITS,
                      reserve=config.RESERVE_CREDITS, backtest_cap=config.BACKTEST_MONTHLY)
        for k, v in (("paid", []), ("ext", []), ("own_month", 0), ("external_month", 0), ("ext_days", {}),
                     ("jobs_cycle", {}), ("jobs_day", {}), ("backtest_used", 0)):
            self.b.setdefault(k, v)
        self.spent_this_run = 0
        self.allowance = 0
        self.job = "scan"
        self.month_end = False
        self.unconfirmed = False                                    # the last balance check failed: keep the last known number

    def locked(self):
        """True while the secret still holds the old free key (its balance is the 2H bot's now)."""
        plan = self.b.get("plan")
        return bool(plan) and plan < config.PAID_MIN_PLAN and not config.MOCK

    def ext_rate(self):
        """Credits a day the other apps on this key are using lately: the average over the last 7 ET days, or 0 if
        they have used nothing in the last 48 hours (they moved to their own key or stopped)."""
        days = self.b.get("ext_days") or {}
        last = (self.b.get("ext") or [{}])[-1].get("ts")
        if not last or (store.now() - store.parse(last)).total_seconds() > 48 * 3600:
            return 0.0
        today = _et().date()
        win = [(today - timedelta(days=i)).isoformat() for i in range(7)]
        return sum(days.get(d, 0) for d in win) / 7

    def backtest_left(self):
        return max(0, config.BACKTEST_MONTHLY - self.b.get("backtest_used", 0))

    def _day(self):
        n = _et()
        today = n.strftime("%Y-%m-%d")
        if self.b.get("day") != today:
            rem = self.b["remaining"] if self.b.get("remaining") is not None else config.MONTHLY_CREDITS
            end = store.parse(self.b["cycle_end"])
            days_left = max(1, math.ceil((end - store.now()).total_seconds() / 86400))
            rate = self.ext_rate()
            # what the other apps are likely to take before the reset, but they stop at their own floor
            others = min(round(rate * days_left), max(0, rem - config.OTHER_APPS_FLOOR)) if rate else 0
            spendable = max(0, rem - config.RESERVE_CREDITS - others - self.backtest_left())
            self.b.update(day=today, days_left=days_left, day_budget=spendable // days_left,
                          spent_today=0, units_today=0, set_aside=others, ext_rate=round(rate, 1), jobs_day={})
        return self.b

    def pace(self):
        """Share of the betting day (9am-1am ET) that has gone by."""
        n = _et()
        h = n.hour + n.minute / 60
        if h < 3:
            h += 24
        return min(1.0, max(0.0, (h - config.DAY_START_ET) / (config.DAY_END_ET - config.DAY_START_ET)))

    def start_run(self, units=1, job=None):
        """Set this run's allowance. job: scan (paced through the day), props, closer or backtest."""
        b = self._day()
        self.job = job or ("props" if units > 1 else "scan")
        left_today = max(0, b["day_budget"] - b["spent_today"])
        if self.locked():
            self.allowance = 0
        elif self.job == "backtest":
            self.allowance = min(self.backtest_left(), config.RUN_CAP * 10)
        elif self.job == "scan":
            ahead = b["day_budget"] * min(1.0, self.pace() + config.PACE_HEADROOM) - b["spent_today"]
            self.allowance = int(max(0, min(config.RUN_CAP, left_today, ahead)))
        else:
            self.allowance = int(min(config.RUN_CAP, left_today))
        rem = b["remaining"] if b.get("remaining") is not None else config.MONTHLY_CREDITS
        self.allowance = min(self.allowance, max(0, rem - config.RESERVE_CREDITS))
        b["runs"] = b.get("runs", 0) + 1
        b["units_today"] += units
        b["allowance"] = self.allowance
        b["locked"] = self.locked()
        b.pop("month_end_mode", None)

    def can(self, cost: int, essential=False) -> bool:
        if self.locked():
            return False
        rem = self.b["remaining"]
        if rem is not None and rem - cost < config.RESERVE_CREDITS:
            return False
        if self.job != "backtest" and self.spent_this_run + cost > config.RUN_CAP:
            return False
        if self.job == "backtest" and self.spent_this_run + cost > self.backtest_left():
            return False
        return essential or self.spent_this_run + cost <= self.allowance

    def record(self, endpoint, cost, remaining, used):
        b = self.b
        self.spent_this_run += cost
        if self.job == "backtest":
            b["backtest_used"] = b.get("backtest_used", 0) + cost
        else:
            self._day()["spent_today"] += cost
        if cost:
            b["jobs_day"][self.job] = b["jobs_day"].get(self.job, 0) + cost
            b["jobs_cycle"][self.job] = b["jobs_cycle"].get(self.job, 0) + cost
        b["own_month"] = b.get("own_month", 0) + cost
        now = store.iso(store.now())
        if remaining is not None and used is not None:
            b["plan"] = remaining + used                            # the key's plan size: 500 = the old free key
        if remaining is not None:
            prev = b.get("remaining")
            if prev is not None and remaining > prev + 50:
                # the API reset the cycle under us (or the paid key arrived): start the day over from the fresh balance
                b["remaining"] = remaining; b.pop("day", None); b["ext"] = []; self._day()
            elif prev is not None and not b.get("unconfirmed"):
                gap = prev - cost - remaining                       # credits that went somewhere other than this agent
                if gap > 0:
                    b["external_month"] = b.get("external_month", 0) + gap
                    d = _et().strftime("%Y-%m-%d"); b["ext_days"][d] = b["ext_days"].get(d, 0) + gap
                    b["ext_days"] = dict(sorted(b["ext_days"].items())[-21:])
                    b["ext"] = (b["ext"] + [{"ts": now, "credits": gap, "rem": remaining}])[-200:]
            b["remaining"] = remaining
            b.pop("unconfirmed", None); self.unconfirmed = False
        if used is not None:
            b["used"] = used
        entry = {"ts": now, "ep": endpoint, "cost": cost, "rem": b["remaining"], "job": self.job}
        b["log"] = (b["log"] + [entry])[-60:]
        if cost:                                                    # paid calls are kept for the whole cycle
            b["paid"] = (b["paid"] + [entry])[-1500:]

    def save(self):
        self.b["updated"] = store.iso(store.now())
        store.save("budget.json", self.b)


def only_allowed(data):
    """Drop every bookmaker that is not on the owner's list, from a list of events or one event."""
    def one(ev):
        if isinstance(ev, dict) and "bookmakers" in ev:
            ev = {**ev, "bookmakers": [b for b in ev["bookmakers"] if config.book_ok(b.get("key"))]}
        return ev
    return [one(e) for e in data] if isinstance(data, list) else one(data)


class OddsClient:
    def __init__(self, budget: Budget):
        self.budget = budget

    # -- transport --------------------------------------------------------
    def _get(self, path, params, cost_est, essential=False, retry=False):
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
            # 429 is "too many requests at once", not "out of credits" (it zeroed the balance on Nov 15 and 17):
            # wait and try once more, and if it still says no, stop this run's spending but keep the last known balance.
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            if e.code == 429 and not retry:
                import time
                time.sleep(2.5)
                return self._get(path, params, 0, essential, retry=True)
            if e.code == 401 and re.search(r"quota|usage|limit", body, re.I):
                self.budget.b["remaining"] = 0                     # the API says the month's credits are gone
                raise OutOfBudget(f"{path}: HTTP 401 ({body[:80]})")
            if e.code in (401, 429):
                self.budget.b["unconfirmed"] = True; self.budget.unconfirmed = True
                raise OutOfBudget(f"{path}: HTTP {e.code} ({body[:80] or 'no detail'}); balance kept at its last reading")
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

    def _by_books(self, path, params, n_markets):
        """Ask only for the allowed books (owner rule). Up to 10 books bill as one region.
        If the API rejects the book list (HTTP 422), fall back to the US region and filter the
        reply ourselves; the filter below keeps every other book out either way."""
        cost = n_markets * max(1, -(-len(config.ALLOWED_BOOKS) // 10))
        try:
            data = self._get(path, {**params, "bookmakers": ",".join(config.ALLOWED_BOOKS)}, cost)
        except urllib.error.HTTPError as e:
            if e.code != 422:
                raise
            data = self._get(path, {**params, "regions": config.REGIONS}, n_markets)
        return only_allowed(data)

    def odds(self, sport):
        markets = config.markets_for(sport)
        n = store.now()
        return self._by_books(f"/sports/{sport}/odds", {
            "markets": markets, "oddsFormat": "decimal",
            "commenceTimeFrom": store.iso(n),
            "commenceTimeTo": store.iso(n + timedelta(hours=config.WINDOW_MAX_H)),
        }, len(markets.split(",")))

    def event_odds(self, sport, event_id, markets):
        """Player props for one game. Costs one credit per market."""
        return self._by_books(f"/sports/{sport}/events/{event_id}/odds",
                              {"markets": ",".join(markets), "oddsFormat": "decimal"}, len(markets))

    def scores(self, sport, essential=True):
        return self._get(f"/sports/{sport}/scores", {"daysFrom": 3}, 2, essential)


# ---- Mock market for offline testing ----------------------------------------
MOCK_SPORTS = ["basketball_nba", "icehockey_nhl", "soccer_epl"]
BOOKS = ["draftkings", "fanduel", "betmgm", "betrivers", "kalshi", "bovada", "pinnacle"]   # mock: extras prove the filter works


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
