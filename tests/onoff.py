"""Out-of-sample check of the lineup on/off adjustment. On/off data through CUT (pbpstats date filter) predicts what
teammates did in the games a 26+ minute player MISSED after CUT, compared with the games he played after CUT.
Run: python tests/onoff.py [CUT=2026-02-01]   (about 80 polite requests the first time, then cached)"""
import math, sys
from collections import defaultdict
sys.path.insert(0, ".")
from agent import nba_data as nd, nba_model as nm, pbp

CUT = sys.argv[1] if len(sys.argv) > 1 else "2026-02-01"
SEASON = 2026
TODAY = __import__("datetime").date.today().isoformat()
to_date = (__import__("datetime").date.fromisoformat(CUT) - __import__("datetime").timedelta(days=1)).isoformat()
P = pbp.players(SEASON, TODAY)
games = [(gid, g) for s, gid, g in nd.all_games([SEASON]) if g.get("st", 2) == 2]
m = nm.Model(SEASON, asof=CUT)
ids = pbp.id_map(P, m.players)
tgames = defaultdict(list)
for gid, g in games:
    for t in g["teams"]:
        tgames[t].append((g["d"], {p[nd.P_ID]: p for p in g["players"] if p[nd.P_TEAM] == t}))
VAL = {"pts": nd.P_PTS, "reb": nd.P_REB, "ast": nd.P_AST, "tpm": nd.P_TPM}
pts = defaultdict(list)
n_o = 0
for team, gl in sorted(tgames.items()):
    pre, post = [x for x in gl if x[0] < CUT], [x for x in gl if x[0] >= CUT]
    tid = P["teams"].get(team)
    if not tid or len(pre) < 25 or len(post) < 15:
        continue
    cands = []
    for pid, pl in m.players.items():
        if pl["team"] != team or pid not in ids or m.minutes(pid) < 26:
            continue
        g_pre = sum(1 for d, b in pre if pid in b)
        missed = [x for x in post if pid not in x[1]]
        played = [x for x in post if pid in x[1]]
        if g_pre >= 20 and len(missed) >= 4 and len(played) >= 5:
            cands.append((pid, missed, played, g_pre / len(pre)))
    if not cands:
        continue
    allr = pbp.team_totals(tid, SEASON, TODAY, to_date=to_date)
    for pid, missed, played, g_frac in cands[:3]:
        offr = pbp.wowy_off(tid, ids[pid], SEASON, TODAY, to_date=to_date)
        if not allr or not offr:
            continue
        n_o += 1
        for mate, pl in m.players.items():
            if pl["team"] != team or mate == pid or mate not in ids or m.minutes(mate) < 18:
                continue
            eff = pbp.off_effect(allr, offr, ids[mate])
            if not eff:
                continue
            gm = [b[mate] for d, b in missed if mate in b]; gp = [b[mate] for d, b in played if mate in b]
            if len(gm) < 3 or len(gp) < 4:
                continue
            p_on = min(0.92, eff["share_on"] / g_frac)                 # the comparison games all had him playing
            for st, col in VAL.items():
                r = eff["r"].get(st)
                rm = sum(x[col] for x in gm) / sum(x[nd.P_MIN] for x in gm)
                rp = sum(x[col] for x in gp) / sum(x[nd.P_MIN] for x in gp)
                if not r or rp <= 0 or rm <= 0:
                    continue
                pts[st].append((math.log(pbp.tonight_mult(r, p_on)), math.log(rm / rp), sum(x[nd.P_MIN] for x in gm), eff["poss"]))
print(f"{n_o} absent players, on/off data through {to_date}, shrinkage prior {pbp.K_POSS:.0f} possessions")
for st, xs in pts.items():
    w = [x[2] for x in xs]; sw = sum(w)
    mx = sum(a * c for a, _, c, _ in xs) / sw; my = sum(b * c for _, b, c, _ in xs) / sw
    cov = sum(c * (a - mx) * (b - my) for a, b, c, _ in xs); vx = sum(c * (a - mx) ** 2 for a, _, c, _ in xs); vy = sum(c * (b - my) ** 2 for _, b, c, _ in xs)
    slope = cov / vx if vx else 0
    print(f"{st:4} n={len(xs):3d}  slope {slope:+.2f}  corr {cov / math.sqrt(vx * vy) if vx and vy else 0:+.3f}  avg predicted {math.exp(mx) - 1:+.1%}  avg actual {math.exp(my) - 1:+.1%}  pred sd {math.sqrt(vx / sw):.3f}")
