"""Market-anchored prop probabilities (model audit, Nov 22 2026, fixes 1 and 2).

The audit found the matchup model's probabilities about five times too confident (log loss 0.725 vs 0.693 for a coin
flip and 0.687 for DraftKings' close) and no measurable information beyond the closing price. So the price comes first:

  anchored P(over) = sigmoid(a + b * logit(market) + c * gap)        gap = logit(model) - logit(market), clipped to +-GAP_CLIP

fitted per stat on every graded DraftKings prop (docs/data/dk/*.json: the model's probability at the line, the line's
prices, what happened). `c` is how far the model is allowed to move the market. It is shrunk toward the pooled fit, and
it stays 0 for a stat until a walk-forward test (fit on earlier days, score the next day, never the same day) shows the
model's gap lowering log loss against the market alone. `b` corrects the market's own calibration (DraftKings' props
have run a little toward unders).

Also fitted per stat: an honest version of the model's own probability, sigmoid(a2 + b2 * logit(model)), for display.

Extreme disagreements are not edges: the ten biggest gaps a day won 47.6%. A gap past GAP_CAP is a flag for the
Newsroom/Skeptic to check for news, never a bet (props.py writes them to agents/gaps.json).

Writes docs/data/anchor.json nightly (run.py nightly -> build()). Pure Python: the runner has no numpy."""
import glob
import math
import os

from . import config, store

FILE = "anchor.json"
STATS = ("pts", "reb", "ast", "tpm", "pra", "ra")
GAP_CLIP = 1.0          # the fit never sees a gap bigger than this (a 50% market vs a 73% model)
GAP_CAP = 1.0           # bigger than this at bet time: a news flag, not a bet
WINDOW_DAYS = 45        # graded days used for the fit
MIN_ROWS = 400          # rows per stat before it gets its own fit
TEST_DAYS = 10          # walk-forward: the last N graded days, each scored by a fit on the days before it
RIDGE_POOL = 300.0      # how hard a stat's fit is pulled toward the pooled fit (in rows)
MIN_GAIN = 0.0003       # walk-forward log-loss gain per prop the model's gap must show before c is used


def _logit(p):
    p = min(max(p, 0.02), 0.98)
    return math.log(p / (1 - p))


def _sig(x):
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, x))))


def _novig(o, u):
    io, iu = 1 / o, 1 / u
    return io / (io + iu)


def _ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


# ------------------------------------------------------------------ data
def rows(data_dir=None, days=WINDOW_DAYS):
    """Graded DraftKings props: (date, stat, logit market, gap, logit model, y)."""
    d = data_dir or os.path.join(str(config.DATA), "dk")
    files = sorted(glob.glob(os.path.join(d, "*.json")))[-days:]
    out = []
    for f in files:
        db = store.load_path(f) if hasattr(store, "load_path") else __import__("json").load(open(f))
        if not db or not db.get("graded"):
            continue
        for r in (db.get("props") or {}).values():
            c = r.get("c")
            if r.get("res") not in ("over", "under") or r.get("pm") is None or not c or not c[1] or not c[2]:
                continue
            lm, lp = _logit(_novig(c[1], c[2])), _logit(r["pm"])
            out.append((db["date"], r["stat"], lm, max(-GAP_CLIP, min(GAP_CLIP, lp - lm)), lp, 1 if r["res"] == "over" else 0))
    return out


# ------------------------------------------------------------------ fitting (Newton's method, small ridge)
def _fit(X, Y, prior=None, lam=None, iters=25):
    """Logistic regression. prior/lam: ridge toward a prior vector (lam = list of per-coefficient strengths in rows)."""
    k = len(X[0])
    w = list(prior) if prior else [0.0] * k
    pr = prior or [0.0] * k
    lam = lam or [1e-6] * k
    for _ in range(iters):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for x, y in zip(X, Y):
            p = _sig(sum(a * b for a, b in zip(w, x)))
            e, v = y - p, p * (1 - p)
            for i in range(k):
                g[i] += e * x[i]
                for j in range(i, k):
                    H[i][j] += v * x[i] * x[j]
        for i in range(k):
            g[i] -= lam[i] * (w[i] - pr[i]) * 0.25          # ridge in "rows" units (a row's curvature is at most 1/4)
            H[i][i] += lam[i] * 0.25
            for j in range(i):
                H[i][j] = H[j][i]
        step = _solve(H, g)
        if step is None:
            break
        w = [a + b for a, b in zip(w, step)]
        if max(abs(s) for s in step) < 1e-7:
            break
    return w


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for j in range(c, n + 1):
                    M[r][j] -= f * M[c][j]
    return [M[i][n] / M[i][i] for i in range(n)]


def _fit_set(R):
    """Pooled + per-stat fits on rows R. Returns {"_pool": [a,b,c], stat: [a,b,c]} and honest-model fits."""
    X = [[1.0, r[2], r[3]] for r in R]
    Y = [r[5] for r in R]
    pool = _fit(X, Y, prior=[0.0, 1.0, 0.0], lam=[1.0, 50.0, 50.0])
    out = {"_pool": pool}
    for s in STATS:
        Rs = [r for r in R if r[1] == s]
        if len(Rs) < MIN_ROWS:
            out[s] = pool
            continue
        out[s] = _fit([[1.0, r[2], r[3]] for r in Rs], [r[5] for r in Rs], prior=pool,
                      lam=[RIDGE_POOL, RIDGE_POOL, RIDGE_POOL])
    return out


def _fit_market_only(R):
    out = {}
    pool = _fit([[1.0, r[2]] for r in R], [r[5] for r in R], prior=[0.0, 1.0], lam=[1.0, 50.0])
    for s in STATS:
        Rs = [r for r in R if r[1] == s]
        out[s] = pool if len(Rs) < MIN_ROWS else _fit([[1.0, r[2]] for r in Rs], [r[5] for r in Rs], prior=pool,
                                                      lam=[RIDGE_POOL, RIDGE_POOL])
    return out


def _honest(R):
    out = {}
    for s in STATS:
        Rs = [r for r in R if r[1] == s]
        if len(Rs) < 200:
            continue
        a, b = _fit([[1.0, r[4]] for r in Rs], [r[5] for r in Rs], prior=[0.0, 1.0], lam=[1.0, 20.0])
        out[s] = [round(a, 4), round(b, 4)]
    return out


def walk_forward(R, test_days=TEST_DAYS):
    """For each of the last test_days graded days: fit on the days before it, score that day. Per stat:
    log loss of the raw market, the market recalibrated, and the full anchor (market + model gap)."""
    days = sorted({r[0] for r in R})
    res = {s: {"n": 0, "market": 0.0, "mkt_cal": 0.0, "anchor": 0.0} for s in STATS}
    for d in days[-test_days:]:
        train = [r for r in R if r[0] < d]
        test = [r for r in R if r[0] == d]
        if len(train) < 1500 or not test:
            continue
        full, mk = _fit_set(train), _fit_market_only(train)
        for r in test:
            s = r[1]
            a, b, c = full.get(s, full["_pool"])
            am, bm = mk[s]
            x = res[s]
            x["n"] += 1
            x["market"] += _ll(_sig(r[2]), r[5])
            x["mkt_cal"] += _ll(_sig(am + bm * r[2]), r[5])
            x["anchor"] += _ll(_sig(a + b * r[2] + c * r[3]), r[5])
    for x in res.values():
        n = x["n"] or 1
        for k in ("market", "mkt_cal", "anchor"):
            x[k] = round(x[k] / n, 5)
        x["gain"] = round(x["mkt_cal"] - x["anchor"], 5)             # what the model's gap adds, per prop
        x["cal_gain"] = round(x["market"] - x["mkt_cal"], 5)         # what recalibrating the market adds
    return res


def build(data_dir=None, log=print):
    R = rows(data_dir)
    if len(R) < 2000:
        log(f"[anchor] only {len(R)} graded props: not enough to fit")
        return None
    wf = walk_forward(R)
    full = _fit_set(R)
    mk = _fit_market_only(R)
    coef, use = {}, {}
    for s in STATS:
        a, b, c = full.get(s, full["_pool"])
        t = wf[s]
        ok = t["n"] >= 300 and t["gain"] >= MIN_GAIN
        if ok:
            coef[s] = [round(a, 4), round(b, 4), round(c, 4)]
        else:
            am, bm = mk[s]
            if t["n"] >= 300 and t["cal_gain"] < 0:                   # recalibrating the market didn't help either
                am, bm = 0.0, 1.0
            coef[s] = [round(am, 4), round(bm, 4), 0.0]
        use[s] = ok
    out = {"updated": store.iso(store.now()), "n": len(R), "days": len({r[0] for r in R}),
           "from": min(r[0] for r in R), "to": max(r[0] for r in R), "gap_cap": GAP_CAP, "gap_clip": GAP_CLIP,
           "coef": coef, "model_counts": use, "honest": _honest(R), "walk_forward": wf,
           "fitted": {s: [round(v, 4) for v in full.get(s, full["_pool"])] for s in STATS}}
    store.save(FILE, out)
    log("[anchor] " + ", ".join(f"{s}: c={coef[s][2]:+.2f} ({'model counts' if use[s] else 'market only'}, "
                                 f"wf gain {wf[s]['gain']:+.4f} on {wf[s]['n']})" for s in STATS))
    return out


# ------------------------------------------------------------------ use
_CACHE = {}


def load():
    if "a" not in _CACHE:
        _CACHE["a"] = store.load(FILE, None)
    return _CACHE["a"]


def ready(A=None):
    A = A if A is not None else load()
    return bool(A and A.get("coef"))


def gap(p_model, p_mkt):
    """Model vs market disagreement on the logit scale (positive: the model likes the over)."""
    return _logit(p_model) - _logit(p_mkt)


def prob(stat, p_mkt, p_model, A=None):
    """Anchored P(over). Falls back to the market alone when there's no fit."""
    A = A if A is not None else load()
    if not ready(A):
        return p_mkt
    a, b, c = A["coef"].get(stat) or [0.0, 1.0, 0.0]
    lm = _logit(p_mkt)
    g = max(-GAP_CLIP, min(GAP_CLIP, _logit(p_model) - lm))
    return _sig(a + b * lm + c * g)


def honest(stat, p_model, A=None):
    """The model's own over probability, recalibrated to what has actually happened (for display)."""
    A = A if A is not None else load()
    h = (A or {}).get("honest", {}).get(stat)
    if not h:
        return p_model
    return _sig(h[0] + h[1] * _logit(p_model))


if __name__ == "__main__":
    build()
