"""Walk-forward check: does the matchup multiplier predict how players beat or miss their norms?
Train on games before CUT, test on games after. No odds needed: we compare actual output to the
player's own per-minute rate x actual minutes, so minutes noise is removed and only matchup signal remains."""
import math, random, sys, time
from collections import defaultdict
from agent import nba_model as nm, nba_data as nd

CUT = sys.argv[1] if len(sys.argv) > 1 else "2026-02-01"
W = {"w_dvp": 0.45, "w_sim": 0.40, "w_h2h": 0.25}
t0 = time.time()
m = nm.Model(2026, asof=CUT)
print(f"trained on {len(m.rows)} player-games before {CUT} in {time.time()-t0:.0f}s")
test = []
for season, gid, g in nd.all_games([2026]):
    if g["d"] < CUT or g.get("st") != 2:
        continue
    ab = list(g["teams"])
    for p in g["players"]:
        if p[nd.P_MIN] >= 20 and p[nd.P_ID] in m.players and m.players[p[nd.P_ID]]["games"] >= 15:
            t = p[nd.P_TEAM]; o = ab[1] if t == ab[0] else ab[0]
            test.append((p, o, g["teams"][t]["home"]))
random.seed(7); random.shuffle(test); test = test[:2500]
res = defaultdict(list)
for p, o, home in test:
    for s in nm.STATS:
        pr = m.project(p[nd.P_ID], s, o, home, W)
        exp_ = m.baseline_rate(p[nd.P_ID], s) * p[nd.P_MIN]
        if exp_ < 0.5:
            continue
        act = nm._val(p, s)
        res[s].append((math.log(pr["mult"]), math.log((act + 0.5) / (exp_ + 0.5)), act, exp_, exp_ * pr["mult"]))
print(f"{len(test)} test player-games, {time.time()-t0:.0f}s\n")
print("stat   n     corr   | actual vs norm by matchup bucket: softest 20% / middle / toughest 20% | MAE norm -> model")
for s, xs in res.items():
    n = len(xs); a = [x[0] for x in xs]; b = [x[1] for x in xs]
    ma, mb = sum(a)/n, sum(b)/n
    cov = sum((x-ma)*(y-mb) for x, y in zip(a, b)); va = sum((x-ma)**2 for x in a); vb = sum((y-mb)**2 for y in b)
    corr = cov / math.sqrt(va*vb) if va and vb else 0
    slope = cov / va if va else 0
    xs.sort(key=lambda x: x[0]); q = n // 5
    def avg_ratio(seg): return sum(x[2] for x in seg) / sum(x[3] for x in seg)
    top, mid, bot = xs[-q:], xs[q:-q], xs[:q]
    mae0 = sum(abs(x[2]-x[3]) for x in xs)/n; mae1 = sum(abs(x[2]-x[4]) for x in xs)/n
    print(f"{s:5} {n:5}  {corr:+.3f}  |  {avg_ratio(top)-1:+6.1%} / {avg_ratio(mid)-1:+6.1%} / {avg_ratio(bot)-1:+6.1%}"
          f"   | {mae0:.3f} -> {mae1:.3f} | slope {slope:.2f} | spread {math.sqrt(va/n):.3f}")
