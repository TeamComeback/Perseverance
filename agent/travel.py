"""Schedule strain for each team on the slate: miles flown since the last game, time zones crossed, altitude,
back-to-backs and long road trips. Arena coordinates are fixed facts; the point effects are estimated from
this project's own box scores (see tests/situations.py) and shrunk, because each effect is small."""
import math
from datetime import date

# team: (lat, lon, UTC offset in winter, feet above sea level)
ARENAS = {
    "ATL": (33.757, -84.396, -5, 1050), "BOS": (42.366, -71.062, -5, 20), "BKN": (40.683, -73.975, -5, 30),
    "CHA": (35.225, -80.839, -5, 750), "CHI": (41.881, -87.674, -6, 595), "CLE": (41.497, -81.688, -5, 650),
    "DAL": (32.790, -96.810, -6, 430), "DEN": (39.749, -105.008, -7, 5280), "DET": (42.341, -83.055, -5, 600),
    "GS": (37.768, -122.388, -8, 10), "HOU": (29.751, -95.362, -6, 50), "IND": (39.764, -86.156, -5, 715),
    "LAC": (33.945, -118.341, -8, 100), "LAL": (34.043, -118.267, -8, 270), "MEM": (35.138, -90.051, -6, 260),
    "MIA": (25.781, -80.188, -5, 10), "MIL": (43.045, -87.917, -6, 615), "MIN": (44.979, -93.276, -6, 830),
    "NO": (29.949, -90.082, -6, 5), "NY": (40.751, -73.994, -5, 35), "OKC": (35.463, -97.515, -6, 1200),
    "ORL": (28.539, -81.384, -5, 100), "PHI": (39.901, -75.172, -5, 20), "PHX": (33.446, -112.071, -7, 1090),
    "POR": (45.532, -122.667, -8, 50), "SAC": (38.580, -121.500, -8, 30), "SA": (29.427, -98.437, -6, 650),
    "TOR": (43.643, -79.379, -5, 250), "UTAH": (40.768, -111.901, -7, 4230), "WSH": (38.898, -77.021, -5, 50),
}
HIGH = 4000            # feet: Denver and Utah


def miles(a, b):
    if a not in ARENAS or b not in ARENAS or a == b:
        return 0.0
    (la1, lo1, _, _), (la2, lo2, _, _) = ARENAS[a], ARENAS[b]
    p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3959 * 2 * math.asin(math.sqrt(h))


def features(team, site, today, history):
    """history: [(iso_date, site_team)] of the team's earlier games, any order. site = the home team tonight.
    Returns the schedule facts for tonight's game."""
    d0 = today if isinstance(today, date) else date.fromisoformat(today)
    prev = sorted((date.fromisoformat(d), s) for d, s in history if date.fromisoformat(d) < d0)
    f = {"home": team == site, "rest": None, "b2b": False, "three_in_four": False, "miles": 0, "miles7": 0, "tz": 0,
         "alt": ARENAS.get(site, (0, 0, 0, 0))[3] >= HIGH and team != site, "road_trip": 0, "games7": 0}
    if not prev:
        return f
    ld, ls = prev[-1]
    gap = (d0 - ld).days
    f["rest"] = gap - 1
    f["b2b"] = gap == 1
    f["three_in_four"] = sum(1 for d, _ in prev if 1 <= (d0 - d).days <= 3) >= 2 and not f["b2b"]
    f["games7"] = sum(1 for d, _ in prev if 1 <= (d0 - d).days <= 6)
    if gap <= 3:                                   # with 3+ days off a team has been home (or settled) for a while
        f["miles"] = round(miles(ls, site))
        f["tz"] = ARENAS.get(site, (0, 0, 0))[2] - ARENAS.get(ls, (0, 0, 0))[2]      # + = flew east (lost hours)
    # miles over the last week, and how many straight road games this is
    chain = [(d, s) for d, s in prev if (d0 - d).days <= 6] + [(d0, site)]
    f["miles7"] = round(sum(miles(a[1], b[1]) for a, b in zip(chain, chain[1:])))
    n = 0
    if team != site:
        n = 1
        for d, s in reversed(prev):
            if s == team or (d0 - d).days > 12:
                break
            n += 1
    f["road_trip"] = n
    return f


# Point effects on a team's own score margin (tests/situations.py, 2024-25 through Nov 2026, ~2,800 games,
# ridge-shrunk; standard errors are about 0.4-0.6 points, so treat every one as a nudge).
EFFECT = {"b2b": -1.2, "b2b_miles_per_1000": -0.4, "three_in_four": -0.4, "east_per_hour": -0.25, "alt_visitor": -0.3,
          "alt_visitor_b2b": -0.8, "road_trip_4plus": -0.5, "rest_3plus": 0.3}


def margin_points(f, E=None):
    """Expected points of margin this team gains (+) or loses (-) from its schedule spot, with the reasons."""
    E = E or EFFECT
    pts, why = 0.0, []
    def add(v, txt):
        nonlocal pts
        if abs(v) >= 0.05:
            pts += v; why.append((round(v, 1), txt))
    if f["b2b"]:
        add(E["b2b"], "second night of a back-to-back")
        if f["miles"] >= 300:
            add(E["b2b_miles_per_1000"] * f["miles"] / 1000, f"flew {f['miles']:,} miles overnight")
    elif f["three_in_four"]:
        add(E["three_in_four"], "third game in four nights")
    if f["tz"] > 0 and f["rest"] is not None and f["rest"] <= 1:
        add(E["east_per_hour"] * f["tz"], f"crossed {f['tz']} time zone{'s' if f['tz'] > 1 else ''} east")
    if f["alt"]:
        add(E["alt_visitor"], "visiting at altitude")
        if f["b2b"]:
            add(E["alt_visitor_b2b"], "altitude on no rest")
    if f["road_trip"] >= 4:
        add(E["road_trip_4plus"], f"game {f['road_trip']} of a road trip")
    if f["rest"] is not None and f["rest"] >= 3:
        add(E["rest_3plus"], f"{f['rest']} days of rest")
    return round(pts, 2), why
