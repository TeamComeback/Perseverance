"""A running record of the sportsbook line on every player prop the agent prices, and what the
player then scored. Past lines can't be bought back cheaply, so this builds the history going
forward: first line seen, last line before tip-off (the closing line), then the final stat."""
import math
from datetime import timedelta

from . import config, store
from . import nba_data as nd
from . import nba_model as nm

FILE = "lines.json"
KEEP_DAYS = 90


def load():
    return store.load(FILE, {"entries": {}})


def record(db, day, priced_lines, infos):
    """priced_lines: [{pid, stat, point, p_mkt, over, under, over_book, under_book, n}] for one scan;
    infos: {pid: {name, team, opp, espn_id, start}}. Keeps the line nearest a coin flip as 'the line'."""
    best = {}
    for x in priced_lines:
        k = (x["pid"], x["stat"])
        if k not in best or abs(x["p_mkt"] - 0.5) < abs(best[k]["p_mkt"] - 0.5):
            best[k] = x
    now = store.now()
    n = 0
    for (pid, stat), x in best.items():
        info = infos.get(pid)
        if not info:
            continue
        key = f"{day}|{pid}|{stat}"
        e = db["entries"].get(key)
        if e and e.get("start") and store.parse(e["start"]) <= now:
            continue                                   # the game has started: the last line stands as the close
        if not e:
            e = {"pid": pid, "name": info["name"], "team": info["team"], "opp": info["opp"], "espn_id": info["espn_id"],
                 "start": info["start"], "stat": stat, "date": day, "first": x["point"], "first_at": store.iso(now),
                 "actual": None, "result": None}
        e.update(line=x["point"], over=round(x["over"], 3) if x["over"] else None,
                 under=round(x["under"], 3) if x["under"] else None, over_book=x["over_book"], under_book=x["under_book"],
                 p_over=round(x["p_mkt"], 4), books=x["n"], at=store.iso(now))
        if x.get("p_model") is not None:
            e["p_model"] = round(x["p_model"], 4)          # the model's own over probability at this line
            e["w"] = x.get("w")
        cvb = (info.get("cv") or {}).get(stat)
        if cvb:
            e["cv"] = cvb[2]
        if (info.get("lean") or {}).get(stat):
            e["lean"] = info["lean"][stat]
        db["entries"][key] = e
        n += 1
    cut = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    db["entries"] = {k: v for k, v in db["entries"].items() if v["date"] >= cut}
    return n


def grade(db):
    """Fill in what each player scored once his game is over. Returns how many were filled."""
    cut = store.now() - timedelta(hours=config.GRADE_AFTER_H)
    boxes, filled = {}, 0
    for e in db["entries"].values():
        if e.get("actual") is not None or e.get("result") == "dnp" or not e.get("start") or store.parse(e["start"]) > cut:
            continue
        eid = e["espn_id"]
        if eid not in boxes:
            try:
                boxes[eid] = nd.parse_summary(eid)
            except Exception as ex:
                print(f"[lines] box {eid} failed: {ex}")
                boxes[eid] = None
        g = boxes[eid]
        if not g:
            continue
        row = next((p for p in g["players"] if p[nd.P_ID] == e["pid"]), None)
        if row is None:
            e["result"] = "dnp"
            continue
        v = nm._val(row, e["stat"]) if e["stat"] in nm.STATS else None
        if v is None:
            continue
        e["actual"] = v
        e["result"] = "over" if v > e["line"] else "under" if v < e["line"] else "push"
        filled += 1
    return filled
