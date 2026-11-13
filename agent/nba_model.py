"""NBA matchup model.

Everything is measured as a *residual*: what a player actually did divided by
what he normally does per minute, times the minutes he played. That way
"Utah gives up threes to point guards" means point guards beat their own norms
against Utah, not just that good shooters happened to play them.

Signals for player P, stat S, opponent O (all shrunk toward neutral by sample size):
  dvp  : residual of everyone at P's position against O
  sim  : residual of players with P's statistical profile against O
  h2h  : P's own residual against O
  ha   : P's home/away split
Projection = per-minute rate x projected minutes x exp(weighted sum of log signals).
"""
import math
from collections import defaultdict
from datetime import date

from . import nba_data as nd

STATS = {"pts": "points", "reb": "rebounds", "ast": "assists", "tpm": "threes", "pra": "pts+reb+ast",
         "ra": "reb+ast"}
ODDS_KEY = {"pts": "player_points", "reb": "player_rebounds", "ast": "player_assists",
            "tpm": "player_threes", "pra": "player_points_rebounds_assists", "ra": "player_rebounds_assists"}
COUNT_STATS = {"reb", "ast", "tpm", "ra"}
ROLE_NAME = {"PG": "point guards", "SG": "shooting guards", "SF": "small forwards", "PF": "power forwards", "C": "centers"}
PRIOR = {"dvp": 10.0, "sim": 8.0, "h2h": 5.0, "ha": 25.0}
# How hard each stat leans on the matchup, calibrated walk-forward on 2025-26 (slope of actual vs predicted).
STAT_CAL = {"pts": 0.70, "reb": 1.00, "ast": 0.70, "tpm": 0.55, "pra": 0.95, "ra": 1.00}   # ra: slope 1.06 walk-forward (Oct 2026)


def _val(p, s):
    if s == "pts":
        return p[nd.P_PTS]
    if s == "reb":
        return p[nd.P_REB]
    if s == "ast":
        return p[nd.P_AST]
    if s == "tpm":
        return p[nd.P_TPM]
    if s == "ra":
        return p[nd.P_REB] + p[nd.P_AST]
    return p[nd.P_PTS] + p[nd.P_REB] + p[nd.P_AST]


class Model:
    def __init__(self, cur_season, asof=None, prev_weight=0.45):
        self.cur = cur_season
        self.asof = asof          # ISO date: only learn from games before this (for backtests)
        rows, team_games = [], defaultdict(list)
        games = [x for x in nd.all_games([cur_season - 1, cur_season])
                 if x[2].get("st", 2) in (2, 3) and not (asof and x[2]["d"] >= asof)]
        cnt = defaultdict(int)
        for _, _, g in games:
            for t in g["teams"]:
                cnt[t] += 1
        real = {t for t, n in cnt.items() if n >= 10}      # drops All-Star / exhibition teams
        for season, gid, g in games:
            if not set(g["teams"]) <= real:
                continue
            sw = 1.0 if season == cur_season else prev_weight
            ab = list(g["teams"])
            if len(ab) != 2:
                continue
            poss = {}
            for t in ab:
                ts = g["tstats"].get(t)
                if ts:
                    poss[t] = ts["fga"] + 0.44 * ts["fta"] - ts["oreb"] + ts["tov"]
            gp = sum(poss.values()) / len(poss) if poss else None
            for t in ab:
                o = ab[1] if t == ab[0] else ab[0]
                if gp:
                    team_games[t].append((sw, gp, g["teams"][t]["score"], g["teams"][o]["score"], poss.get(t, gp), poss.get(o, gp)))
            for p in g["players"]:
                if p[nd.P_MIN] < 1:
                    continue
                t = p[nd.P_TEAM]
                o = ab[1] if t == ab[0] else ab[0]
                rows.append({"pid": p[nd.P_ID], "name": p[nd.P_NAME], "team": t, "opp": o, "d": g["d"],
                             "season": season, "sw": sw, "home": g["teams"][t]["home"], "gpos": p[nd.P_GPOS],
                             "min": p[nd.P_MIN], "start": p[nd.P_START], "tpa": p[nd.P_TPA], "fga": p[nd.P_FGA],
                             "fta": p[nd.P_FTA], "tov": p[nd.P_TOV],
                             "mar": g["teams"][t]["score"] - g["teams"][o]["score"], **{s: _val(p, s) for s in STATS}})
        self.rows = rows
        self._players()
        self._teams(team_games)
        self._residuals()
        self.env = None                        # built on first use (environment() is defined below)

    # ---------------- players ----------------
    def _players(self):
        by = defaultdict(list)
        for r in self.rows:
            by[r["pid"]].append(r)
        self.by_player = by
        P = {}
        for pid, rs in by.items():
            rs.sort(key=lambda r: r["d"])
            last = rs[-1]
            wmin = sum(r["min"] * r["sw"] for r in rs)
            if wmin <= 0:
                continue
            per36 = {k: 36 * sum(r[k] * r["sw"] for r in rs) / wmin for k in ("pts", "reb", "ast", "tpa", "fta", "fga", "tpm")}
            g = last["gpos"] or ("C" if per36["reb"] >= 10 else "F" if per36["reb"] >= 7 else "G")
            if g == "G":
                role = "PG" if per36["ast"] >= 5.5 else "SG"
            elif g == "C":
                role = "C"
            else:
                role = "PF" if (per36["reb"] >= 8.0 and per36["tpa"] < 5) or per36["reb"] >= 9.5 else "SF"
            # season-specific per-minute rates (expected values for residuals)
            srate = {}
            for season in {r["season"] for r in rs}:
                sr = [r for r in rs if r["season"] == season]
                m = sum(r["min"] for r in sr)
                if m >= 120:
                    srate[season] = {s: sum(r[s] for r in sr) / m for s in STATS}
            allm = sum(r["min"] for r in rs)
            P[pid] = {"pid": pid, "name": last["name"], "team": last["team"], "role": role, "per36": per36,
                      "srate": srate, "rate_all": {s: sum(r[s] for r in rs) / allm for s in STATS},
                      "games": len(rs), "cur_games": sum(1 for r in rs if r["season"] == self.cur)}
        # profile z-scores for similarity
        keys = ("pts", "reb", "ast", "tpa", "fta", "fga")
        pool = [p for pid, p in P.items() if sum(r["min"] for r in by[pid]) >= 200]
        mu = {k: sum(p["per36"][k] for p in pool) / len(pool) for k in keys} if pool else {k: 0 for k in keys}
        sd = {k: (sum((p["per36"][k] - mu[k]) ** 2 for p in pool) / len(pool)) ** 0.5 or 1 for k in keys} if pool else {k: 1 for k in keys}
        for p in P.values():
            v = [(p["per36"][k] - mu[k]) / sd[k] for k in keys]
            n = math.sqrt(sum(x * x for x in v)) or 1
            p["vec"] = [x / n for x in v]
        self.players = P

    def rate(self, pid, s, season=None):
        p = self.players[pid]
        if season and season in p["srate"]:
            return p["srate"][season][s]
        return p["rate_all"][s]

    def sim(self, a, b):
        va, vb = self.players[a]["vec"], self.players[b]["vec"]
        c = sum(x * y for x, y in zip(va, vb))
        return max(0.0, c) ** 3

    # ---------------- teams ----------------
    def _teams(self, team_games):
        T = {}
        for t, gs in team_games.items():
            w = sum(x[0] for x in gs)
            pace = sum(x[0] * x[1] for x in gs) / w
            ortg = 100 * sum(x[0] * x[2] for x in gs) / sum(x[0] * x[4] for x in gs)
            drtg = 100 * sum(x[0] * x[3] for x in gs) / sum(x[0] * x[5] for x in gs)
            T[t] = {"pace": pace, "ortg": ortg, "drtg": drtg}
        for k, rev in (("pace", True), ("ortg", True), ("drtg", False)):
            order = sorted(T, key=lambda t: T[t][k], reverse=rev)
            for i, t in enumerate(order):
                T[t][k + "_rank"] = i + 1      # pace #1 = fastest, ortg #1 = best offense, drtg #1 = best defense
        self.teams = T
        self.n_teams = len(T)

    # ---------------- residual tables ----------------
    def _residuals(self):
        self.res_rows = []
        for r in self.rows:
            if r["pid"] not in self.players or r["min"] < 6:
                continue
            rr = {}
            for s in STATS:
                e = self.rate(r["pid"], s, r["season"]) * r["min"]
                rr[s] = max(0.25, min(3.0, (r[s] + 0.5) / (e + 0.5)))
            w = r["sw"] * min(r["min"], 40) / 36
            self.res_rows.append((r, rr, w))
        # Center: averaging log ratios of noisy counts drifts below zero, which would make every
        # matchup look like an under. Rescale so the league-average matchup is exactly neutral.
        for st in STATS:
            tw = sum(w for _, _, w in self.res_rows)
            c = math.exp(sum(w * math.log(rr[st]) for _, rr, w in self.res_rows) / tw) if tw else 1.0
            for _, rr, _ in self.res_rows:
                rr[st] = rr[st] / c
        self.by_opp = defaultdict(list)
        self.res_by_player = defaultdict(list)
        for x in self.res_rows:
            self.by_opp[x[0]["opp"]].append(x)
            self.res_by_player[x[0]["pid"]].append(x)
        # defense-vs-position factors for every team/role/stat, then ranks
        self.dvp_tab = {}
        for o in self.teams:
            for role in ROLE_NAME:
                for s in STATS:
                    xs = [(rr[s], w) for r, rr, w in self.by_opp[o] if self.players[r["pid"]]["role"] == role]
                    self.dvp_tab[(o, role, s)] = self._shrunk(xs, PRIOR["dvp"])
        self.dvp_rank = {}
        for role in ROLE_NAME:
            for s in STATS:
                order = sorted(self.teams, key=lambda o: self.dvp_tab[(o, role, s)][0], reverse=True)
                for i, o in enumerate(order):
                    self.dvp_rank[(o, role, s)] = i + 1    # 1 = gives up the most

    @staticmethod
    def _shrunk(xs, prior):
        n = sum(w for _, w in xs)
        if n <= 0:
            return 1.0, 0.0
        lm = sum(w * math.log(v) for v, w in xs) / (n + prior)
        return math.exp(lm), n

    # ---------------- projection ----------------
    def minutes(self, pid):
        rs = self.by_player[pid]
        cur = [r for r in rs if r["season"] == self.cur][-8:]
        prev = [r for r in rs if r["season"] != self.cur][-15:]
        if len(cur) >= 4:
            ws = [0.75 ** i for i in range(len(cur))][::-1]
            return sum(w * r["min"] for w, r in zip(ws, cur)) / sum(ws)
        pm = sum(r["min"] for r in prev) / len(prev) if prev else 0
        cm = sum(r["min"] for r in cur) / len(cur) if cur else pm
        k = len(cur) / 4
        return k * cm + (1 - k) * pm

    def baseline_rate(self, pid, s):
        """Recency-weighted per-minute rate (last season counts less)."""
        rs = self.by_player[pid][-40:]
        ws = [r["sw"] * 0.96 ** i for i, r in enumerate(reversed(rs))][::-1]
        m = sum(w * r["min"] for w, r in zip(ws, rs))
        return sum(w * r[s] for w, r in zip(ws, rs)) / m if m else 0

    # ---------------- schedule strength (display + tracking only until tests/backtest_schedule.py passes) ----------------
    SOS_CAP = 0.08

    def schedule(self, pid, s, opp, weights=None, n=40):
        """How tough his recent opponents were for his position in this stat (1.00 = league average, below 1 = tougher),
        weighted like his baseline (last 40 games, recent ones and minutes count more); tonight's opponent on the same
        scale; and `adj`, the factor the projection would divide by if his baseline were first put on a neutral
        schedule, using the same strength the model gives a matchup and capped at +-8%. Nothing here feeds project()."""
        p = self.players[pid]
        rs = self.by_player[pid][-n:]
        ws = [r["sw"] * 0.96 ** i for i, r in enumerate(reversed(rs))][::-1]
        tw = lw = 0.0
        for w, r in zip(ws, rs):
            f = self.dvp_tab.get((r["opp"], p["role"], s), (1.0, 0))[0]
            tw += w * r["min"]
            lw += w * r["min"] * math.log(f)
        sched = math.exp(lw / tw) if tw else 1.0
        tonight = self.dvp_tab.get((opp, p["role"], s), (1.0, 0))[0]
        W = weights or {}
        k = W.get("w_dvp", 0.45) * STAT_CAL[s] * W.get("matchup_strength", 1.0)
        adj = max(1 - self.SOS_CAP, min(1 + self.SOS_CAP, sched ** k))
        return {"sched": sched, "tonight": tonight, "k": k, "adj": adj, "n": len(rs), "rank": self.sched_rank(pid, s, sched)}

    def sched_rank(self, pid, s, sched):
        """Where his schedule sits among players at his position with 5+ games this season: (rank, of), 1 = toughest."""
        role = self.players[pid]["role"]
        key = (role, s)
        if not hasattr(self, "_sched_tab"):
            self._sched_tab = {}
        if key not in self._sched_tab:
            vals = []
            for q, pl in self.players.items():
                if pl["role"] != role or pl.get("cur_games", 0) < 5:
                    continue
                rs = self.by_player[q][-40:]
                ws = [r["sw"] * 0.96 ** i for i, r in enumerate(reversed(rs))][::-1]
                tw = sum(w * r["min"] for w, r in zip(ws, rs))
                if tw:
                    vals.append(math.exp(sum(w * r["min"] * math.log(self.dvp_tab.get((r["opp"], role, s), (1.0, 0))[0])
                                             for w, r in zip(ws, rs)) / tw))
            self._sched_tab[key] = sorted(vals)
        vals = self._sched_tab[key]
        return (min(len(vals), sum(1 for v in vals if v < sched - 1e-9) + 1), len(vals)) if vals else (0, 0)

    def project(self, pid, s, opp, home, weights, mins=None):
        p = self.players[pid]
        mins = self.minutes(pid) if mins is None else mins
        base = self.baseline_rate(pid, s) * mins
        dvp, n_dvp = self.dvp_tab.get((opp, p["role"], s), (1.0, 0))
        # similar players vs this opponent (not including him)
        xs, n_close = [], 0
        for r, rr, w in self.by_opp.get(opp, []):
            if r["pid"] == pid or r["pid"] not in self.players:
                continue
            sm = self.sim(pid, r["pid"])
            if sm > 0.05:
                xs.append((rr[s], w * sm))
                if sm > 0.35:
                    n_close += 1
        sim, n_sim = self._shrunk(xs, PRIOR["sim"])
        mine = [(rr[s], w) for r, rr, w in self.by_opp.get(opp, []) if r["pid"] == pid]
        h2h, n_h2h = self._shrunk(mine, PRIOR["h2h"])
        h2h_games = [r for r in self.by_player[pid] if r["opp"] == opp]
        side = [(rr[s], w) for r, rr, w in self.res_by_player.get(pid, []) if r["home"] == home]
        ha, _ = self._shrunk(side, PRIOR["ha"])
        logm = (weights["w_dvp"] * math.log(dvp) + weights["w_sim"] * math.log(sim)
                + weights["w_h2h"] * math.log(h2h) + 0.5 * math.log(ha))
        mult = math.exp(logm * STAT_CAL[s] * weights.get("matchup_strength", 1.0))
        # chances: how many rebounds (missed shots, extra possessions) this opponent gives up compared with the league.
        # A separate, team-level signal the position tables miss (tests/environment.py: +0.4 to +1.0 elasticity, every stat).
        if self.env is None:
            self.env = environment(self) or {}
        ef = env_factors(self.env, p["team"], opp) if self.env else None
        chances = max(0.9, min(1.1, ef["reb"])) if ef else 1.0
        mult *= chances ** ENV_W[s]
        proj = base * mult
        # spread of outcomes, scaled to projected minutes
        rs = [r for r in self.by_player[pid] if r["min"] >= max(12, mins * 0.6)][-40:]
        ys = [r[s] * mins / r["min"] for r in rs] or [base]
        mu_y = sum(ys) / len(ys)
        var_y = sum((y - mu_y) ** 2 for y in ys) / max(1, len(ys) - 1)
        T, To = self.teams.get(p["team"], {}), self.teams.get(opp, {})
        rk = self.dvp_rank.get((opp, p["role"], s), 0)
        sig = [
            {"k": "dvp", "v": round(dvp, 3), "n": round(n_dvp, 1), "rank": rk,
             "txt": f"{opp} gives {ROLE_NAME[p['role']]} {_pct(dvp)} {STATS[s]} vs their norm "
                    + (f"(#{rk} softest of {self.n_teams})" if rk <= self.n_teams / 2 else f"(#{self.n_teams - rk + 1} toughest of {self.n_teams})")},
            {"k": "sim", "v": round(sim, 3), "n": n_close,
             "txt": f"Players with his profile: {_pct(sim)} {STATS[s]} vs {opp} ({n_close} close comps)"},
            {"k": "h2h", "v": round(h2h, 3), "n": len(h2h_games),
             "txt": (f"His history vs {opp}: {_avg(h2h_games, s)} {STATS[s]}/g in {len(h2h_games)} games ({_pct(h2h)})"
                     if h2h_games else f"No history vs {opp} yet")},
            {"k": "chances", "v": round(chances, 3),
             "txt": f"{opp} games have {_pct(chances)} rebounds up for grabs vs the league (missed shots and extra possessions)"},
            {"k": "env", "txt": f"{opp}: defense #{To.get('drtg_rank', '-')}, pace #{To.get('pace_rank', '-')} · "
                                f"{p['team']}: offense #{T.get('ortg_rank', '-')}, pace #{T.get('pace_rank', '-')}"},
            {"k": "ha", "v": round(ha, 3), "txt": f"{'Home' if home else 'Away'} split {_pct(ha)}"},
        ]
        return {"pid": pid, "name": p["name"], "team": p["team"], "opp": opp, "home": home, "role": p["role"],
                "stat": s, "minutes": round(mins, 1), "base": round(base, 2), "proj": round(proj, 2),
                "mult": round(mult, 3), "var": var_y, "mean_y": mu_y, "signals": sig, "games": p["games"],
                "cur_games": p["cur_games"]}


def _pct(m):
    return f"{(m - 1) * 100:+.0f}%"


def _avg(rs, s):
    return f"{sum(r[s] for r in rs) / len(rs):.1f}" if rs else "-"


# ---------------- probabilities ----------------
def _nb_cdf(c, mu, k):
    """P(X <= c) for a negative binomial with mean mu and size k."""
    if c < 0:
        return 0.0
    p = k / (k + mu)
    pmf = p ** k
    tot = pmf
    for x in range(1, int(c) + 1):
        pmf *= (x + k - 1) / x * (1 - p)
        tot += pmf
    return min(1.0, tot)


# Walk-forward calibration on 2025-26 (tests/calibration.py): raw over-probabilities ran ~3 points high,
# because real stat lines are right-skewed. actual ~= 0.03 + 0.88 x raw.
CAL_A, CAL_B = 0.03, 0.88


def p_over(proj, line):
    p = _p_over_raw(proj, line)
    return min(0.97, max(0.03, CAL_A + CAL_B * p))


def _p_over_raw(proj, line):
    """Model probability of going over `line` (half-point lines; integer lines treat equal as push)."""
    s, mu = proj["stat"], max(proj["proj"], 0.05)
    var_y, mean_y = proj["var"], max(proj["mean_y"], 0.05)
    if s in COUNT_STATS:
        var = var_y * (mu / mean_y) if mean_y else mu
        k = mu * mu / (var - mu) if var > mu * 1.05 else 60.0
        k = max(1.5, min(60.0, k))
        over = 1 - _nb_cdf(math.floor(line), mu, k)
        if float(line).is_integer():
            push = _nb_cdf(line, mu, k) - _nb_cdf(line - 1, mu, k)
            return over / max(1e-6, 1 - push)
        return over
    cv = (var_y ** 0.5) / mean_y if mean_y else 0.35
    sd = max(0.18, min(0.6, cv)) * mu
    z = (line - mu) / sd
    return 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))


def season_now(d=None):
    return nd.season_of(d or date.today())


# ---------------- game environment ----------------
# Elasticities from tests/environment.py (three walk-forward cuts on 2025-26), cut roughly in half to be safe.
ENV_W = {"pts": 0.3, "reb": 0.3, "ast": 0.5, "tpm": 0.25, "pra": 0.3, "ra": 0.35}


def environment(model):
    """Team tables for tonight's game environment: pace, scoring, and rebounds for and allowed per game
    (recency-weighted like everything else in the model)."""
    reb = defaultdict(lambda: defaultdict(float))            # (team, date) -> team rebounds
    opp_of = {}
    for r in model.rows:
        reb[(r["team"], r["d"])]["v"] += r["reb"]
        reb[(r["team"], r["d"])]["w"] = r["sw"]
        opp_of[(r["team"], r["d"])] = r["opp"]
    acc = defaultdict(lambda: [0.0, 0.0, 0.0])                 # team -> w, rebounds for, rebounds allowed
    for (t, d), x in reb.items():
        o = reb.get((opp_of[(t, d)], d))
        if not o:
            continue
        a = acc[t]; a[0] += x["w"]; a[1] += x["w"] * x["v"]; a[2] += x["w"] * o["v"]
    T = {t: {"reb_for": a[1] / a[0], "reb_alw": a[2] / a[0]} for t, a in acc.items() if a[0] > 0 and t in model.teams}
    if not T:
        return None
    n = len(T)
    lg = {"reb": sum(x["reb_for"] for x in T.values()) / n, "pace": sum(model.teams[t]["pace"] for t in T) / n,
          "ortg": sum(model.teams[t]["ortg"] for t in T) / n}
    return {"T": T, "lg": lg, "teams": model.teams}


def env_factors(E, team, opp):
    """Tonight vs this team's usual game: {pace, off, reb} ratios (1.0 = a normal night for them).
    pace: possessions; off: points per possession against this defense; reb: team rebounds against this opponent."""
    if not E or team not in E["T"] or opp not in E["T"]:
        return None
    Tm, lg, T = E["teams"], E["lg"], E["T"]
    pace = (Tm[team]["pace"] + Tm[opp]["pace"] - lg["pace"]) / Tm[team]["pace"]
    off = (Tm[team]["ortg"] + Tm[opp]["drtg"] - lg["ortg"]) / Tm[team]["ortg"]
    reb = (T[team]["reb_for"] * T[opp]["reb_alw"] / lg["reb"]) / T[team]["reb_for"]
    return {"pace": pace, "off": off, "reb": reb}
