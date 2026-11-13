"""Our Book: schedule the freezes, grade them nightly, run the gates, and flag inconsistent book prices.

Freezes (tools/ourbook.js) land in docs/data/ourbook/<date>.json at 9am, noon, 3pm and 6pm ET, plus a "tip" freeze
for each game in the last run before it starts. Nothing in a freeze is ever edited.

Nightly grade() -> docs/data/ourbook_track.json:
  moves      Closing-line test. For every frozen line where DraftKings had a line at the freeze and later moved it:
             did it move toward our number? (50% = no information, 55%+ = real signal.) Also closing-line value in
             points. Lines where someone on either team changed injury status after the freeze are kept apart ("news").
  preopen    Props frozen before DraftKings posted them: was our number closer to the close than DraftKings' own opener?
  accuracy   At tip: log loss of our chance vs DraftKings' no-vig vs Kalshi, at DraftKings' closing line; calibration;
             blend test (does adding our number to the market's lower the log loss? leave-one-day-out).
  ind_bias   The independent book's own corrections, learned only from its graded results (never from prices).
  cuts       Gates per cut (book x market x early/late): collecting -> watch / signal / no edge, with a kill switch.
  stage      0 label, 1 tracked, 2 conviction reason (signal + blend helps), 3 eligible for paper bets (owner's OK).
"""
import json
import math
from datetime import timedelta

from . import config, store

DIR = "ourbook"
SLOTS = [(9, "9am"), (12, "noon"), (15, "3pm"), (18, "6pm")]
SLOT_WINDOW_H = 2.5            # a slot missed by more than this is skipped (better no freeze than a mislabeled one)
TIP_WINDOW_MIN = 25            # the tip freeze: last run with the game starting within this many minutes
ORDER = ["9am", "noon", "3pm", "6pm", "tip"]
EARLY = {"9am", "noon"}
GATE_N = 300                   # moved lines before a cut counts
SIGNAL, WATCH, KILL = 0.55, 0.52, 0.50
BLEND_N = 1000                 # graded props (150 for game lines) before a blend weight can be used
BLEND_N_GAME = 150
STAGE3_DAYS = 28
KEEP_DAYS = 45


def path(day):
    return f"{DIR}/{day}.json"


def load(day):
    return store.load(path(day), None)


# ------------------------------------------------------------------ scheduling (called from auto_run)
def due(games, now_et, today):
    """Which freezes are due now: [(kind, [espn_id...])]."""
    db = load(today) or {}
    have = {f["id"] for f in db.get("freezes", [])}
    pre = [g for g in games if g.get("state") == "pre"]
    if not pre:
        return []
    out = []
    h = now_et.hour + now_et.minute / 60
    for slot, kind in SLOTS:
        if kind not in have and slot <= h < slot + SLOT_WINDOW_H:
            out.append((kind, []))
    tipped = {e for e, G in (db.get("games") or {}).items() if "tip" in (G.get("f") or {})}
    soon = []
    for g in pre:
        mins = (store.parse(g["start"]) - store.now()).total_seconds() / 60
        if 0 < mins <= TIP_WINDOW_MIN and g["espn_id"] not in tipped:
            soon.append(g["espn_id"])
    if soon:
        out.append(("tip", soon))
    return out


def freeze(kind, games=None, log=print):
    import os, shutil, subprocess
    if not shutil.which("node"):
        log("[ourbook] node missing")
        return
    env = {**os.environ, "PERSEVERANCE_DATA": str(config.DATA), "FREEZE": kind}
    if games:
        env["GAMES"] = ",".join(games)
    r = subprocess.run(["node", str(config.ROOT / "tools" / "ourbook.js")], capture_output=True, text=True, timeout=900, env=env)
    log((r.stdout + r.stderr).strip()[-600:])


# ------------------------------------------------------------------ helpers
def _nv(a, b):
    if not a or not b:
        return None
    return (1 / a) / (1 / a + 1 / b)


def _ll(p, won):
    p = min(max(p, 0.01), 0.99)
    return -math.log(p if won else 1 - p)


def _sgn(x):
    return (x > 0) - (x < 0)


def _ladder_p(lad, line):
    """Chance of over `line` from [[line, p‰], ...] (sorted), straight-line between neighbours; None outside."""
    pts = sorted(lad)
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if x0 <= line <= x1:
            t = 0 if x1 == x0 else (line - x0) / (x1 - x0)
            return (p0 + t * (p1 - p0)) / 1000
    return None


def _prop_p(b, line):
    """Our chance of over DraftKings' line from a frozen prop [line, p‰, ladder|0, mean]."""
    if not b:
        return None
    if abs(b[0] - line) < 1e-9:
        return b[1] / 1000
    if b[2]:
        lad = [[b[0] + j, v] for j, v in zip(range(-2, 3), b[2])]
        return _ladder_p(lad, line)
    return None


def _news(G, fid):
    """Did anyone on either team change status (out / questionable) between this freeze and the last one?"""
    f = G.get("f") or {}
    last = next((f[k] for k in reversed(ORDER) if k in f), None)
    a = (f.get(fid) or {}).get("inp") or {}
    b = (last or {}).get("inp") or {}
    return set(a.get("out") or []) != set(b.get("out") or []) or set(a.get("q") or []) != set(b.get("q") or [])


# ------------------------------------------------------------------ nightly grading
def grade(days=KEEP_DAYS, log=print):
    from . import research, dk
    today = research.et_today()
    old = store.load("ourbook_track.json", {}) or {}
    moves, preopen, acc, ind_err, daily = [], [], [], {}, {}
    for back in range(1, days + 1):
        ds = (today - timedelta(days=back)).isoformat()
        ob = load(ds)
        if not ob:
            continue
        dd = dk.load(ds)
        kd = store.load(f"kalshi/{ds}.json", {"games": {}, "props": {}}) or {}
        dy = daily.setdefault(ds, {"ind_mar": [], "dk_mar": [], "ind_tot": [], "dk_tot": [], "ind_prop": [], "inf_prop": []})
        # ---- games
        for eid, G in (ob.get("games") or {}).items():
            D = dd["games"].get(eid) or {}
            fin, c = D.get("final"), D.get("close") or {}
            for fid, rec in (G.get("f") or {}).items():
                dkf = rec.get("dk") or {}
                news = _news(G, fid)
                for book in ("ind", "inf"):
                    b = rec.get(book)
                    if not b:
                        continue
                    for mk, ours, at_f, close in (("spread", b.get("sp"), dkf.get("sp"), c.get("sp")), ("total", b.get("tot"), dkf.get("tot"), c.get("tot"))):
                        if ours is None or at_f is None or close is None:
                            continue
                        gap = ours - at_f
                        if gap == 0 or close == at_f:
                            continue
                        toward = _sgn(close - at_f) == _sgn(gap)
                        moves.append({"d": ds, "book": book, "mk": mk, "fid": fid, "toward": toward, "clv": round((close - at_f) * _sgn(gap), 2),
                                      "gap": abs(gap), "news": news})
            if not fin or fin[0] is None:
                continue
            margin, total = fin[1] - fin[0], fin[1] + fin[0]
            tip = (G.get("f") or {}).get("tip") or next(((G["f"][k]) for k in reversed(ORDER) if k in (G.get("f") or {})), None)
            if not tip:
                continue
            kp = ((kd.get("games") or {}).get(eid) or {}).get("pre") or {}
            ib = tip.get("ind") or {}
            if ib.get("mar") is not None:
                dy["ind_mar"].append(abs(ib["mar"] - margin))
            if c.get("sp") is not None:
                dy["dk_mar"].append(abs(-c["sp"] - margin))
            if ib.get("mt") is not None:
                dy["ind_tot"].append(abs(ib["mt"] - total))
                ind_err.setdefault("total", []).append(ib["mt"] - total)
            if c.get("tot") is not None:
                dy["dk_tot"].append(abs(c["tot"] - total))
            for book in ("ind", "inf"):
                b = tip.get(book) or {}
                if c.get("sp") is not None and b.get("spl") and margin + c["sp"] != 0:
                    p = _ladder_p(b["spl"], c["sp"])
                    pdk = _nv(c.get("sp_h"), c.get("sp_a"))
                    if p is not None and pdk:
                        acc.append({"d": ds, "book": book, "mk": "spread", "p": p, "dk": pdk, "k": kp.get("p_cover"), "won": margin + c["sp"] > 0})
                if c.get("tot") is not None and b.get("totl") and total != c["tot"]:
                    p = _ladder_p(b["totl"], c["tot"])
                    pdk = _nv(c.get("ov"), c.get("un"))
                    if p is not None and pdk:
                        acc.append({"d": ds, "book": book, "mk": "total", "p": p, "dk": pdk, "k": kp.get("p_over"), "won": total > c["tot"]})
        # ---- props
        for key, pr in (ob.get("props") or {}).items():
            D = dd["props"].get(key) or {}
            G = (ob.get("games") or {}).get(pr.get("eid")) or {}
            if not D.get("c") or D["c"][0] is None:
                continue
            close, opn = D["c"][0], (D.get("o") or [None])[0]
            for fid, fr in (pr.get("f") or {}).items():
                news = _news(G, fid) if G else False
                for book in ("ind", "inf"):
                    b = fr.get(book)
                    if not b:
                        continue
                    if fr.get("dk"):
                        at_f = fr["dk"][0]
                        gap = b[0] - at_f
                        if gap != 0 and close != at_f:
                            moves.append({"d": ds, "book": book, "mk": D["stat"], "fid": fid, "toward": _sgn(close - at_f) == _sgn(gap),
                                          "clv": round((close - at_f) * _sgn(gap), 2), "gap": abs(gap), "news": news})
                    elif opn is not None and opn != close:
                        preopen.append({"d": ds, "book": book, "mk": D["stat"], "fid": fid, "news": news,
                                        "beat": abs(b[0] - close) < abs(opn - close), "tie": abs(b[0] - close) == abs(opn - close)})
            if D.get("res") not in ("over", "under"):
                continue
            won = D["res"] == "over"
            fs_ = pr.get("f") or {}
            tip = fs_.get("tip") or next((fs_[k] for k in reversed(ORDER) if k in fs_), None)
            if not tip:
                continue
            pdk = _nv(D["c"][1], D["c"][2])
            kpp = ((kd.get("props") or {}).get(key) or {})
            pk = kpp.get("p") if kpp.get("line") == close else None
            for book in ("ind", "inf"):
                b = tip.get(book)
                p = _prop_p(b, close)
                if p is None or not pdk:
                    continue
                acc.append({"d": ds, "book": book, "mk": D["stat"], "p": p, "dk": pdk, "k": pk, "won": won})
                if D.get("actual") is not None and b:
                    (dy["ind_prop"] if book == "ind" else dy["inf_prop"]).append(abs(b[3] - D["actual"]))
                    if book == "ind":
                        ind_err.setdefault(D["stat"], []).append(b[3] - D["actual"])
    out = {"updated": store.iso(store.now()), "gate_n": GATE_N, "signal": SIGNAL}
    out["moves"] = _moves_summary(moves)
    out["preopen"] = _preopen_summary(preopen)
    out["accuracy"] = _accuracy(acc)
    # independent book's own bias corrections (shrunk toward zero until the sample is large)
    out["ind_bias"] = {k: round(sum(v) / len(v) * len(v) / (len(v) + 100), 2) for k, v in ind_err.items() if v}
    out["ind_bias_n"] = {k: len(v) for k, v in ind_err.items()}
    out["daily"] = {d: {k: (round(sum(v) / len(v), 2) if v else None) for k, v in x.items()} | {"n_prop": len(x["ind_prop"]), "n_game": len(x["ind_mar"])}
                    for d, x in sorted(daily.items()) if any(x.values())}
    out["cuts"], out["paper"] = _cuts(moves, acc, old)
    out["flags_record"] = _flags_record(dk)
    store.save("ourbook_track.json", out)
    a = out["moves"].get("all", {})
    log(f"[ourbook] graded: {a.get('n', 0)} moved lines, {a.get('toward', 0)} toward ours; {len(acc)} priced bets")
    return out


def _flags_record(dk):
    """Book consistency flags, graded: a combo flagged high should go under (low: over)."""
    fl = (store.load("book_flags.json", {}) or {}).get("days") or {}
    rec = {"pra": [0, 0], "ra": [0, 0]}
    for ds, xs in fl.items():
        dd = dk.load(ds)
        for x in xs:
            r = (dd.get("props") or {}).get(f"{x['pid']}|{x['kind']}") or {}
            if r.get("res") not in ("over", "under") or not r.get("c") or r["c"][0] != x["combo"]:
                continue
            want = "under" if x["gap"] > 0 else "over"
            rec[x["kind"]][0 if r["res"] == want else 1] += 1
    return rec


def _rate(rows):
    n = len(rows)
    t = sum(1 for r in rows if r["toward"])
    return {"n": n, "toward": t, "rate": round(t / n, 3) if n else None, "clv": round(sum(r["clv"] for r in rows) / n, 2) if n else None}


def _moves_summary(rows):
    clean = [r for r in rows if not r["news"]]
    out = {"all": _rate(rows), "clean": _rate(clean), "news": _rate([r for r in rows if r["news"]])}
    for key, f in (("book", lambda r: r["book"]), ("market", lambda r: r["mk"]), ("freeze", lambda r: r["fid"]),
                   ("gap", lambda r: "0.5" if r["gap"] < 1 else "1" if r["gap"] < 1.5 else "1.5+")):
        g = {}
        for r in clean:
            g.setdefault(f(r), []).append(r)
        out["by_" + key] = {k: _rate(v) for k, v in sorted(g.items())}
    return out


def _preopen_summary(rows):
    out = {}
    for book in ("ind", "inf"):
        rs = [r for r in rows if r["book"] == book and not r["news"] and not r["tie"]]
        n = len(rs)
        out[book] = {"n": n, "beat": sum(1 for r in rs if r["beat"]), "rate": round(sum(1 for r in rs if r["beat"]) / n, 3) if n else None}
    return out


def _accuracy(rows):
    out = {}
    groups = {}
    for r in rows:
        kind = "games" if r["mk"] in ("spread", "total") else "props"
        for k in (f"{r['book']}|all_{kind}", f"{r['book']}|{r['mk']}"):
            groups.setdefault(k, []).append(r)
    for k, rs in groups.items():
        n = len(rs)
        o = {"n": n, "days": len({r["d"] for r in rs}),
             "ll_ours": round(sum(_ll(r["p"], r["won"]) for r in rs) / n, 4),
             "ll_dk": round(sum(_ll(r["dk"], r["won"]) for r in rs) / n, 4)}
        rk = [r for r in rs if r.get("k") is not None]
        if rk:
            o["kalshi"] = {"n": len(rk), "ll_ours": round(sum(_ll(r["p"], r["won"]) for r in rk) / len(rk), 4),
                           "ll_dk": round(sum(_ll(r["dk"], r["won"]) for r in rk) / len(rk), 4),
                           "ll_k": round(sum(_ll(r["k"], r["won"]) for r in rk) / len(rk), 4)}
        # calibration: when we said X, how often did it happen
        cal = {}
        for r in rs:
            b = min(9, int(r["p"] * 10))
            c = cal.setdefault(b, [0, 0])
            c[0] += 1
            c[1] += r["won"]
        o["cal"] = {f"{b * 10}-{b * 10 + 10}": c for b, c in sorted(cal.items())}
        # blend test: w × ours + (1-w) × DraftKings, fit on all days but one, scored on the day left out
        ws = [0, 0.1, 0.2, 0.3, 0.4, 0.5]
        days = sorted({r["d"] for r in rs})
        fit = {w: round(sum(_ll(w * r["p"] + (1 - w) * r["dk"], r["won"]) for r in rs) / n, 4) for w in ws}
        lodo = 0.0
        if len(days) >= 3:
            for d in days:
                tr = [r for r in rs if r["d"] != d]
                te = [r for r in rs if r["d"] == d]
                bw = min(ws, key=lambda w: sum(_ll(w * r["p"] + (1 - w) * r["dk"], r["won"]) for r in tr))
                lodo += sum(_ll(bw * r["p"] + (1 - bw) * r["dk"], r["won"]) - _ll(r["dk"], r["won"]) for r in te)
            lodo = round(lodo / n, 5)
        best_w = min(ws, key=lambda w: fit[w])
        need = BLEND_N_GAME if k.endswith(("spread", "total", "all_games")) else BLEND_N
        o["blend"] = {"fit": fit, "best_w": best_w, "lodo_gain": -lodo if len(days) >= 3 else None,
                      "ready": n >= need and len(days) >= 3 and lodo < 0 and best_w > 0, "need": need}
        out[k] = o
    return out


def _cuts(moves, acc, old):
    """Gates per cut: book × market group × early/late freeze, on clean lines (no injury news after the freeze)."""
    today = store.now().date().isoformat()
    prev = (old or {}).get("cuts") or {}
    grp = lambda mk: mk if mk in ("spread", "total") else "props"
    cuts = {}
    for r in moves:
        if r["news"]:
            continue
        k = f"{r['book']}|{grp(r['mk'])}|{'early' if r['fid'] in EARLY else 'late'}"
        cuts.setdefault(k, []).append(r)
    # paper results of betting DraftKings' close on our side at tip, by book and market group (needs prices: from acc rows)
    paper = {}
    for r in acc:
        side_over = r["p"] > r["dk"]
        edge = abs(r["p"] - r["dk"])
        if edge < 0.03:
            continue
        k = f"{r['book']}|{grp(r['mk'])}"
        price_p = r["dk"] if side_over else 1 - r["dk"]
        dec = 1 / max(0.05, price_p) * 0.955                 # DraftKings' no-vig price less a typical 4.5% hold
        win = r["won"] == side_over
        x = paper.setdefault(k, {"n": 0, "w": 0, "units": 0.0, "days": set()})
        x["n"] += 1
        x["w"] += win
        x["units"] += (dec - 1) if win else -1
        x["days"].add(r["d"])
    for x in paper.values():
        x["units"] = round(x["units"], 2)
        x["roi"] = round(x["units"] / x["n"], 3) if x["n"] else None
        x["days"] = len(x["days"])
    out = {}
    for k, rs in sorted(cuts.items()):
        rs = sorted(rs, key=lambda r: r["d"])
        a, last = _rate(rs), _rate(rs[-GATE_N:])
        was = prev.get(k) or {}
        if a["n"] < GATE_N:
            status = "collecting"
        elif was.get("status") == "signal" and last["rate"] is not None and last["rate"] < KILL:
            status = "off"                                    # kill switch
        elif last["rate"] >= SIGNAL:
            status = "signal"
        elif last["rate"] >= WATCH:
            status = "watch"
        else:
            status = "no edge"
        book, mg, _ = k.split("|")
        blend = (acc_blend(acc, book, mg))
        stage = 1
        since = was.get("signal_since")
        if status == "signal":
            since = since or today
            if blend:
                stage = 2
                pp = paper.get(f"{book}|{mg}") or {}
                if (store.parse(today + "T00:00:00Z") - store.parse(since + "T00:00:00Z")).days >= STAGE3_DAYS and (pp.get("roi") or 0) > 0:
                    stage = 3
        else:
            since = None
        out[k] = {**a, "last": last, "status": status, "stage": stage, "signal_since": since, "blend_ready": bool(blend),
                  "progress": round(min(1, a["n"] / GATE_N), 3)}
    return out, paper


def acc_blend(acc, book, mg):
    """True when adding our number to DraftKings' measurably helps for this book and market group (leave-one-day-out)."""
    rs = [r for r in acc if r["book"] == book and (r["mk"] == mg if mg in ("spread", "total") else r["mk"] not in ("spread", "total"))]
    need = BLEND_N_GAME if mg in ("spread", "total") else BLEND_N
    days = sorted({r["d"] for r in rs})
    if len(rs) < need or len(days) < 3:
        return False
    ws = [0.1, 0.2, 0.3, 0.4, 0.5]
    gain = 0.0
    for d in days:
        tr = [r for r in rs if r["d"] != d]
        te = [r for r in rs if r["d"] == d]
        bw = min([0] + ws, key=lambda w: sum(_ll(w * r["p"] + (1 - w) * r["dk"], r["won"]) for r in tr))
        gain += sum(_ll(r["dk"], r["won"]) - _ll(bw * r["p"] + (1 - bw) * r["dk"], r["won"]) for r in te)
    return gain > 0


# ------------------------------------------------------------------ book consistency (Step 4.1), today
def flags(day=None, log=print):
    """DraftKings disagreeing with itself: combo lines vs the sum of their parts. Writes book_flags.json."""
    from . import research, dk
    day = day or research.et_today()
    ds = day.isoformat()
    db = dk.load(ds)
    P = db.get("props") or {}
    by = {}
    for k, r in P.items():
        if r.get("c") and r["c"][0] is not None:
            by.setdefault(r["pid"], {})[r["stat"]] = r
    sims = store.load("sims.json", {}) or {}
    sg = sims.get("games") or {} if sims.get("date") == ds else {}

    def our_half(pid, eid, stat):
        h = (((sg.get(eid) or {}).get("players") or {}).get(pid) or {}).get("h") or {}
        h = h.get(stat)
        if not h:
            return None
        a_ = sorted((float(k), v) for k, v in h.items())
        n = sum(v for _, v in a_)
        c, med = 0, a_[-1][0]
        for x, v in a_:
            c += v
            if c >= n / 2:
                med = x
                break
        over = lambda L: sum(v for x, v in a_ if x > L) / n
        cands = [math.floor(med) + j + 0.5 for j in (-2, -1, 0, 1) if math.floor(med) + j + 0.5 >= 0]
        return min(cands, key=lambda L: abs(over(L) - 0.5))
    out = []
    # combo vs parts: DraftKings normally sets P+R+A 0 to +1 above the sum of its points, rebounds and assists lines,
    # and R+A within half a point of rebounds + assists (Nov 5-12: 507 of 542 and 527 of 542). Outside that range one of
    # its lines is out of step. (Nov 5-12: P+R+A at +2 went over 3 times in 12.)
    NORMAL = {"pra": (-0.5, 1.0), "ra": (-0.5, 0.5)}
    for pid, s in by.items():
        line = lambda k: s[k]["c"][0] if k in s else None
        name = next(iter(s.values()))["name"]
        eid = next(iter(s.values()))["espn_id"]
        for kind, parts_k in (("pra", ("pts", "reb", "ast")), ("ra", ("reb", "ast"))):
            parts = [line(k) for k in parts_k]
            if line(kind) is None or any(x is None for x in parts):
                continue
            gap = line(kind) - sum(parts)
            lo, hi = NORMAL[kind]
            if lo <= gap <= hi:
                continue
            lbl = "P+R+A" if kind == "pra" else "R+A"
            ours = our_half(pid, eid, kind)
            out.append({"pid": pid, "name": name, "espn_id": eid, "kind": kind, "combo": line(kind), "parts": sum(parts), "gap": round(gap, 1), "ours": ours,
                        "txt": f"{name}: DraftKings' {lbl} {line(kind):g} vs its separate lines adding up to {sum(parts):g} ({gap:+g}; normally {lo:+g} to {hi:+g}). "
                               f"The {lbl} line looks {'high: lean the combo under or the parts over' if gap > hi else 'low: lean the combo over or the parts under'}"
                               + (f". Our line: {ours:g}." if ours is not None else ".")})
    # each team: DraftKings' player points lines vs its own implied team total (books set player lines a little under, so
    # this is a relative check: flags a team whose lines sit unusually far from its total)
    teams = {}
    for k, r in P.items():
        if r["stat"] == "pts" and r.get("c") and r["c"][0] is not None:
            t = teams.setdefault((r["espn_id"], r["team"]), [0.0, 0])
            t[0] += r["c"][0]
            t[1] += 1
    tt = []
    for (eid, team), (s, n) in teams.items():
        G = (db.get("games") or {}).get(eid) or {}
        c = G.get("close") or G.get("open") or {}
        if c.get("sp") is None or c.get("tot") is None or n < 5:
            continue
        imp = c["tot"] / 2 - c["sp"] / 2 if team == G.get("home") else c["tot"] / 2 + c["sp"] / 2
        tt.append((eid, team, s, n, imp, s / imp))
    if len(tt) >= 4:
        avg = sum(x[5] for x in tt) / len(tt)
        for eid, team, s, n, imp, ratio in tt:
            if abs(ratio - avg) >= 0.08:
                out.append({"espn_id": eid, "team": team, "kind": "team", "gap": round(ratio - avg, 3),
                            "txt": f"{team}: its {n} posted points lines add up to {s:g} against an implied team total of {imp:.1f}, "
                                   f"{'more' if ratio > avg else 'less'} than other teams tonight ({ratio:.0%} vs {avg:.0%} typical). "
                                   f"{'Player overs or the team under may be priced high' if ratio > avg else 'Player unders or the team over may be priced high'}."})
    hist = (store.load("book_flags.json", {}) or {}).get("days") or {}
    hist[ds] = [x for x in out if x["kind"] in ("pra", "ra")]
    hist = {d: v for d, v in sorted(hist.items())[-KEEP_DAYS:]}
    store.save("book_flags.json", {"date": ds, "updated": store.iso(store.now()), "flags": out, "days": hist}, compact=True)
    if out:
        log(f"[ourbook] {len(out)} book consistency flags")
    return out


def prune():
    from . import research
    root = config.DATA / DIR
    if not root.exists():
        return
    cut = (research.et_today() - timedelta(days=KEEP_DAYS)).isoformat()
    for f in root.glob("*.json"):
        if f.stem < cut:
            f.unlink()
