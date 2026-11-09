"""Walk-forward check of game-environment signals the matchup multiplier may be missing: tonight's pace, the
opponent's defensive strength, and how many rebounds are up for grabs. Trained before CUT, tested after.
Residual = actual vs the current model's projection at the minutes actually played.
Run: python tests/environment.py [CUT]"""
import math, random, sys
from collections import defaultdict
sys.path.insert(0, ".")
from agent import nba_model as nm, nba_data as nd

CUT = sys.argv[1] if len(sys.argv) > 1 else "2026-02-01"
W = {"w_dvp": 0.45, "w_sim": 0.40, "w_h2h": 0.25}
m = nm.Model(2026, asof=CUT)
E = nm.environment(m)
test = []
for season, gid, g in nd.all_games([2026]):
    if g["d"] < CUT or g.get("st") != 2:
        continue
    ab = list(g["teams"])
    for p in g["players"]:
        if p[nd.P_MIN] >= 20 and p[nd.P_ID] in m.players and m.players[p[nd.P_ID]]["games"] >= 15:
            t = p[nd.P_TEAM]; o = ab[1] if t == ab[0] else ab[0]
            test.append((p, t, o, g["teams"][t]["home"]))
random.seed(7); random.shuffle(test); test = test[:4000]
rows = defaultdict(list)
for p, t, o, home in test:
    f = nm.env_factors(E, t, o)
    if not f:
        continue
    for s in nm.STATS:
        pr = m.project(p[nd.P_ID], s, o, home, W)
        exp_ = m.baseline_rate(p[nd.P_ID], s) * p[nd.P_MIN] * pr["mult"]
        if exp_ < 0.5:
            continue
        rows[s].append((math.log((nm._val(p, s) + 0.5) / (exp_ + 0.5)), {k: math.log(v) for k, v in f.items()}))
print(f"{len(test)} test player-games after {CUT}")
for s, xs in rows.items():
    y = [a for a, _ in xs]; my = sum(y) / len(y)
    out = []
    for k in ("pace", "off", "reb"):
        x = [b[k] for _, b in xs]; mx = sum(x) / len(x)
        cov = sum((a - mx) * (b - my) for a, b in zip(x, y)); vx = sum((a - mx) ** 2 for a in x); vy = sum((b - my) ** 2 for b in y)
        slope = cov / vx if vx else 0; corr = cov / math.sqrt(vx * vy) if vx and vy else 0
        se = math.sqrt(max(1e-12, (vy - slope * cov) / (len(x) - 2) / vx)) if vx else 0
        out.append(f"{k}: slope {slope:+.2f} (se {se:.2f}, r {corr:+.3f}, sd {math.sqrt(vx / len(x)):.3f})")
    print(f"{s:4} n={len(xs):5}  " + " | ".join(out))

# joint fit (the three signals overlap), ridge-shrunk; needs numpy
try:
    import numpy as np
    print("\njoint ridge fit (elasticities):")
    for s, xs in rows.items():
        X = np.array([[b["pace"], b["off"], b["reb"]] for _, b in xs]); y = np.array([a for a, _ in xs])
        X = X - X.mean(0); y = y - y.mean()
        lam = 0.5
        beta = np.linalg.solve(X.T @ X + lam * np.eye(3), X.T @ y)
        r = y - X @ beta
        se = np.sqrt(np.diag(np.linalg.inv(X.T @ X + lam * np.eye(3)) * r.var()))
        print(f"  {s:4} pace {beta[0]:+.2f} ({se[0]:.2f})  off {beta[1]:+.2f} ({se[1]:.2f})  reb {beta[2]:+.2f} ({se[2]:.2f})   var explained {1 - r.var() / y.var():.4f}")
except ImportError:
    pass
