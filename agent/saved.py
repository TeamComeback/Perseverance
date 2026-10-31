"""Saved props: props you bookmark from a player card for later analysis (no money involved).

Saving opens a prefilled GitHub issue titled "Save: ..." (same flow as logging a bet). The workflow
records it in docs/data/saved.json with a snapshot of what the card showed (projection, hit rates,
matchup), then grades it from the ESPN box score after the game, so over time you can see which
kinds of props you save actually hit. Comment /remove on the issue to delete one.
"""
import re
from datetime import timedelta

from . import nba_data as nd
from . import nba_model as nm
from . import store

FILE = "saved.json"


def _form(body):
    out = {}
    for label, val in re.findall(r"###\s*(.+?)\s*\n+(.*?)(?=\n###|\Z)", body or "", re.S):
        val = val.strip()
        if val and val != "_No response_":
            out[label.strip().lower()] = val
    return out


def load():
    return store.load(FILE, {"items": []})


def from_issue(ev):
    issue = ev["issue"]
    num = issue["number"]
    db = load()
    item = next((x for x in db["items"] if x.get("issue") == num), None)
    if "comment" in ev:
        cmd = ev["comment"]["body"].strip().lower()
        if cmd.startswith("/remove") and item:
            db["items"].remove(item)
            store.save(FILE, db)
            store.gh_comment(num, "Removed from your saved props.")
        return
    if item:
        return
    f = _form(issue.get("body"))
    try:
        line = float(f.get("line", ""))
    except ValueError:
        store.gh_comment(num, "Couldn't read the line. It should be a number like `7.5`.")
        return
    stat = f.get("stat", "").lower()
    if stat not in nm.STATS:
        store.gh_comment(num, f"Unknown stat `{stat}`. Use one of: {', '.join(nm.STATS)}.")
        return
    side = "Under" if f.get("side", "Over").lower().startswith("u") else "Over"
    idx = (store.load("players.json", {}).get("players") or {}).get(f.get("player id", ""), {})
    item = {"issue": num, "saved": store.iso(store.now()), "player": f.get("player", idx.get("name", "")),
            "pid": f.get("player id"), "stat": stat, "side": side, "line": line,
            "espn_id": f.get("espn game id") or idx.get("game"), "team": idx.get("team"), "opp": idx.get("opp"),
            "start": idx.get("start"), "snapshot": (f.get("snapshot") or "")[:600], "notes": (f.get("notes") or "")[:600],
            "result": None, "actual": None}
    db["items"].append(item)
    store.save(FILE, db)
    store.gh_comment(num, f"Saved: {item['player']} {side} {line:g} {nm.STATS[stat]}. "
                          "I'll record how it does after the game. Comment `/remove` to delete it.")
    store.gh_close(num)


def grade():
    db = load()
    changed = 0
    boxes = {}
    for it in db["items"]:
        if it.get("result") is not None or not it.get("espn_id"):
            continue
        if it.get("start") and store.parse(it["start"]) > store.now() - timedelta(hours=2.5):
            continue
        eid = it["espn_id"]
        if eid not in boxes:
            try:
                boxes[eid] = nd.parse_summary(eid)
            except Exception:
                boxes[eid] = None
        g = boxes[eid]
        if not g:
            if it.get("start") and store.parse(it["start"]) < store.now() - timedelta(days=4):
                it["result"] = "void"
                changed += 1
            continue
        row = next((p for p in g["players"] if p[nd.P_ID] == it.get("pid")), None)
        if row is None:
            it["result"] = "dnp"
        else:
            v = nm._val(row, it["stat"])
            it["actual"] = v
            it["min"] = row[nd.P_MIN]
            d = (v - it["line"]) if it["side"] == "Over" else (it["line"] - v)
            it["result"] = "hit" if d > 0 else "miss" if d < 0 else "push"
        it["graded"] = store.iso(store.now())
        changed += 1
    if changed:
        store.save(FILE, db)
    return changed
