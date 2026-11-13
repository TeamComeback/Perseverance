"""Walk-forward check for the schedule adjustment (stage gate for the schedule framework).

Train on games before CUT, test on games after. For each test player-game we build the projection the model makes
today (baseline per-minute rate x actual minutes x matchup) and the same projection with the baseline first put on a
neutral schedule (divided by Model.schedule()'s `adj`), plus a full-strength version (exponent 1, uncapped) to see
whether the model's matchup strength is the right scale. Lower average miss wins. Actual minutes are used so minutes
noise doesn't drown the signal.

    python -m tests.backtest_schedule [CUT] [N]
The adjustment can move into project() only if `sos` beats `now` on most stats here AND in sos_track.json (live).
"""
import math, random, sys, time
from collections import defaultdict
from agent import nba_model as nm, nba_data as nd

CUT = sys.argv[1] if len(sys.argv) > 1 else "2026-02-01"
NMAX = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
W = {"w_dvp": 0.45, "w_sim": 0.40, "w_h2h": 0.25, "matchup_strength": 1.0}
SEASON = int(CUT[:4]) + (1 if CUT[5:7] >= "08" else 0)
t0 = time.time()
m = nm.Model(SEASON, asof=CUT)
print(f"trained on {len(m.rows)} player-games before {CUT} in {time.time() - t0:.0f}s")
test = []
for season, gid, g in nd.all_games([SEASON]):
    if g["d"] < CUT or g.get("st") != 2:
        continue
    ab = list(g["teams"])
    for p in g["players"]:
        if p[nd.P_MIN] >= 20 and p[nd.P_ID] in m.players and m.players[p[nd.P_ID]]["games"] >= 10:
            t = p[nd.P_TEAM]; o = ab[1] if t == ab[0] else ab[0]
            test.append((p, o, g["teams"][t]["home"]))
random.seed(11); random.shuffle(test); test = test[:NMAX]
res = defaultdict(lambda: {"n": 0, "now": 0.0, "sos": 0.0, "full": 0.0, "big_n": 0, "big_now": 0.0, "big_sos": 0.0})
for p, o, home in test:
    pid = p[nd.P_ID]
    for s in nm.STATS:
        pr = m.project(pid, s, o, home, W, mins=p[nd.P_MIN])
        if pr["base"] < 0.5:
            continue
        x = m.schedule(pid, s, o, W)
        act = nm._val(p, s)
        now, sos, full = pr["proj"], pr["proj"] / x["adj"], pr["proj"] / x["sched"]
        r = res[s]; r["n"] += 1
        r["now"] += abs(act - now); r["sos"] += abs(act - sos); r["full"] += abs(act - full)
        if abs(x["sched"] - 1) >= 0.04:                       # the players the adjustment is meant for
            r["big_n"] += 1; r["big_now"] += abs(act - now); r["big_sos"] += abs(act - sos)
print(f"{len(test)} test player-games, {time.time() - t0:.0f}s\n")
print("stat      n   MAE now   MAE sos   MAE full |  schedule 4%+ off:  n   now     sos")
wins = 0
for s, r in res.items():
    n = r["n"]; bn = max(1, r["big_n"])
    better = r["sos"] < r["now"]; wins += better
    print(f"{s:5} {n:6}   {r['now']/n:.4f}   {r['sos']/n:.4f}   {r['full']/n:.4f}  |  {r['big_n']:6}  {r['big_now']/bn:.4f}  {r['big_sos']/bn:.4f}  {'sos better' if better else ''}")
print(f"\nschedule-adjusted beats current on {wins} of {len(res)} stats")
