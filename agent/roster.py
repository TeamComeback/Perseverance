"""Roster layer for the team model: how much is tonight's lineup worth compared with the team the ratings saw?

The ratings describe a team as it has played lately. Two things make tonight different:
  - missing: rotation players who are out tonight but played in recent games (the ratings still include them);
  - back:    players who play tonight but missed most recent games (the ratings barely include them).
Each player's value is his box-score production per minute (a Game Score built from the stats we store), recency
weighted and shrunk toward a replacement-level player. A player's weight is his usual minutes times how much of
the recent window he played (missing) or missed (back). One number, POINTS_PER_UNIT, converts the net into points
of margin; it is fitted on past games against what the ratings got wrong (tests/roster.py)."""
from collections import defaultdict, deque

from . import nba_data as nd

WINDOW = 10             # team games that define "lately"
K_MIN = 250.0           # minutes of replacement-level prior on a player's value
HALF = 25               # a player's games: recency half-life, in his own games
POINTS_PER_UNIT = 0.4   # fitted on 2025-26 (1,059 games): 0.41 against the ratings' misses; 0.06 against DraftKings' (already priced)
CAP = 8.0               # most points one team's lineup news can move the margin
FT = 0.78


def game_score(p):
    pts, fga, fta = p[nd.P_PTS], p[nd.P_FGA], p[nd.P_FTA]
    ftm = FT * fta
    fgm = max(0.0, (pts - p[nd.P_TPM] - ftm) / 2)
    return pts + 0.4 * fgm - 0.7 * fga - 0.4 * (fta - ftm) + 0.4 * p[nd.P_REB] + 0.7 * p[nd.P_AST] - p[nd.P_TOV]


class State:
    """Running picture of every team's rotation, updated one game at a time (so nothing sees the future)."""

    def __init__(self):
        self.hist = defaultdict(lambda: deque(maxlen=60))        # pid -> (min, gs)
        self.team_games = defaultdict(lambda: deque(maxlen=WINDOW))   # team -> {pid: min}
        self.name = {}
        self.season = {}                                         # team -> season of its window (a new season starts clean)
        self.repl_sum, self.repl_min = 60.0, 200.0               # running bench production per minute

    def value(self, pid):
        h = self.hist[pid]
        n = len(h)
        num = den = 0.0
        for i, (m, gs) in enumerate(h):
            w = 0.5 ** ((n - 1 - i) / HALF)
            num += w * gs; den += w * m
        repl = self.repl_sum / self.repl_min
        return (num + K_MIN * repl) / (den + K_MIN) - repl      # per minute, above replacement

    def rotation(self, team):
        """pid -> (usual minutes when he plays, share of the last WINDOW team games he played)."""
        tg = self.team_games[team]
        if len(tg) < 4:
            return {}
        tot, cnt = defaultdict(float), defaultdict(int)
        for g in tg:
            for pid, m in g.items():
                tot[pid] += m; cnt[pid] += 1
        return {pid: (tot[pid] / cnt[pid], cnt[pid] / len(tg)) for pid in tot if tot[pid] / cnt[pid] >= 10}

    def adjust(self, team, playing, known=None):
        """playing: set of pids expected to play. known: pids on the roster who have history but no recent team
        games (returning players). Returns (net units, missing list, back list)."""
        rot = self.rotation(team)
        miss, back = [], []
        for pid, (mins, share) in rot.items():
            v = self.value(pid)
            if pid not in playing and share >= 0.3:
                miss.append((self.name.get(pid, pid), round(mins * share * v, 2)))
            elif pid in playing and share < 0.7:
                back.append((self.name.get(pid, pid), round(mins * (1 - share) * v, 2)))
        for pid in (known or ()):                                 # back after missing the whole window
            if pid in playing and pid not in rot and len(self.hist[pid]) >= 10:
                mins = sum(m for m, _ in list(self.hist[pid])[-10:]) / 10
                if mins >= 14:
                    back.append((self.name.get(pid, pid), round(mins * self.value(pid), 2)))
        net = sum(x for _, x in back) - sum(x for _, x in miss)
        return net, sorted(miss, key=lambda x: -abs(x[1])), sorted(back, key=lambda x: -abs(x[1]))

    def update(self, g, season=None):
        per = defaultdict(dict)
        for p in g.get("players") or []:
            pid, t, m = str(p[nd.P_ID]), p[nd.P_TEAM], p[nd.P_MIN]
            if m <= 0:
                continue
            gs = game_score(p)
            self.hist[pid].append((m, gs)); self.name[pid] = p[nd.P_NAME]
            per[t][pid] = m
            if m < 16:
                self.repl_sum = 0.999 * self.repl_sum + gs; self.repl_min = 0.999 * self.repl_min + m
        for t, d in per.items():
            if season is not None and self.season.get(t) != season:      # offseason moves are not "missing players"
                self.team_games[t].clear(); self.season[t] = season
            self.team_games[t].append(d)


def history_pass(games):
    """games sorted by date. Returns ({gid: {team: net units}}, final State)."""
    S, out = State(), {}
    last_team = {}
    for s, gid, g in games:
        if len(g.get("teams") or {}) == 2 and g.get("players"):
            played = defaultdict(set)
            for p in g["players"]:
                if p[nd.P_MIN] > 0:
                    played[p[nd.P_TEAM]].add(str(p[nd.P_ID]))
            out[gid] = {}
            for t in g["teams"]:
                known = {pid for pid in played[t] if last_team.get(pid) == t}
                out[gid][t] = S.adjust(t, played[t], known)[0]
            for t, ps in played.items():
                for pid in ps:
                    last_team[pid] = t
        S.update(g, s)
    return out, S


def points(units):
    return max(-CAP, min(CAP, POINTS_PER_UNIT * units))


def tonight(S, team, roster_rows):
    """roster_rows: tonight's players for the team from players.json ({pid: {out, pplay, name}}).
    Returns {"pts": margin points, "out": [(name, pts)], "back": [(name, pts)]}."""
    playing = {pid for pid, r in roster_rows.items() if not r.get("out") and (r.get("pplay") is None or r["pplay"] >= 0.5)}
    net, miss, back = S.adjust(team, playing, set(roster_rows))
    f = lambda xs: [[n, round(POINTS_PER_UNIT * v, 1)] for n, v in xs if abs(POINTS_PER_UNIT * v) >= 0.3][:5]
    return {"pts": round(points(net), 1), "out": f(miss), "back": f(back)}
