"""How much schedule spots are worth, from this project's own box scores.
For every regular-season game: each team's margin compared with what the two teams' season scoring margins and
home court predict, regressed on back-to-backs, travel, time zones, altitude, road trips and long rest.
Run: python tests/situations.py   (needs numpy; the agent itself does not)."""
import sys
from collections import defaultdict
sys.path.insert(0, ".")
import numpy as np
from agent import nba_data as nd, travel


def rows():
    out = []
    for season in (2025, 2026, 2027):
        gs = sorted(((g["d"], gid, g) for s, gid, g in nd.all_games([season]) if g.get("st", 2) == 2 and len(g["teams"]) == 2), key=lambda x: x[0])
        net, n = defaultdict(float), defaultdict(int)
        for d, gid, g in gs:
            ab = list(g["teams"])
            for t, o in ((ab[0], ab[1]), (ab[1], ab[0])):
                net[t] += g["teams"][t]["score"] - g["teams"][o]["score"]; n[t] += 1
        hist = defaultdict(list)
        for d, gid, g in gs:
            ab = list(g["teams"])
            home = next(t for t in ab if g["teams"][t]["home"]); away = next(t for t in ab if t != home)
            if home not in travel.ARENAS or away not in travel.ARENAS or n[home] < 10 or n[away] < 10:
                continue
            exp = net[home] / n[home] - net[away] / n[away]
            mar = g["teams"][home]["score"] - g["teams"][away]["score"]
            fh, fa = travel.features(home, home, d, hist[home]), travel.features(away, home, d, hist[away])
            out.append((season, exp, mar, g["teams"][home]["score"] + g["teams"][away]["score"], fh, fa))
            hist[home].append((d, home)); hist[away].append((d, home))
    return out


def vec(f):
    return [1.0 if f["b2b"] else 0.0, (f["miles"] / 1000 if f["b2b"] else 0.0), 1.0 if f["three_in_four"] else 0.0,
            float(max(0, f["tz"])) if (f["rest"] is not None and f["rest"] <= 1) else 0.0, 1.0 if f["alt"] else 0.0,
            1.0 if (f["alt"] and f["b2b"]) else 0.0, 1.0 if f["road_trip"] >= 4 else 0.0, 1.0 if (f["rest"] or 0) >= 3 else 0.0]


NAMES = ["b2b", "b2b_miles_per_1000", "three_in_four", "east_per_hour", "alt_visitor", "alt_visitor_b2b", "road_trip_4plus", "rest_3plus"]

if __name__ == "__main__":
    R = rows()
    # home margin residual = home court + home team's spot effects - away team's spot effects
    X = np.array([[1.0] + [a - b for a, b in zip(vec(fh), vec(fa))] for _, _, _, _, fh, fa in R])
    y = np.array([mar - exp for _, exp, mar, _, _, _ in R])
    lam = 30.0
    P = np.eye(X.shape[1]) * lam; P[0, 0] = 0
    beta = np.linalg.solve(X.T @ X + P, X.T @ y)
    res = y - X @ beta
    cov = np.linalg.inv(X.T @ X + P) * res.var()
    print(f"{len(R)} games, home court {beta[0]:+.2f}, residual sd {res.std():.1f}")
    cnt = np.abs(np.array([vec(fh) for *_, fh, _ in R])).astype(bool).sum(0) + np.abs(np.array([vec(fa) for *_, fa in R])).astype(bool).sum(0)
    for nme, b, se, c in zip(NAMES, beta[1:], np.sqrt(np.diag(cov))[1:], cnt):
        print(f"  {nme:22s} {b:+.2f}  (se {se:.2f}, {int(c)} team-games)")
