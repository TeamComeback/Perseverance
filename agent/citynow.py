"""Perseverance City's live status (docs/data/city_now.json): which desk did what, in order, during this run.

The Dispatcher marks each step as the run reaches it (`mark(desk, text)`), so the city page can show the run desk by
desk as soon as the run's files land, and say when the last run started and finished. Local file only: no network, no
credits, no extra pushes (it is saved with the rest of the run's data). It must never break a run: every call swallows
its own errors. Desk ids are the ones in docs/city/cast.json."""
import json
import os
import time
from datetime import datetime, timezone

from . import config

SKIP = ("probe", "manual-issue")          # not city work worth showing: a signal probe, the Chairman's own bet form
_S = {"on": False, "t0": 0.0, "doc": None}


def _iso(t=None):
    return datetime.fromtimestamp(t or time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write():
    try:
        p = config.DATA / "city_now.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_S["doc"], separators=(",", ":")))
        tmp.replace(p)
    except Exception:
        pass


def start(mode):
    try:
        if mode in SKIP:
            return
        _S.update(on=True, t0=time.time())
        _S["doc"] = {"mode": mode, "started": _iso(_S["t0"]), "finished": None, "secs": None,
                     "gh_run": os.environ.get("GITHUB_RUN_ID"), "steps": []}
        mark("dispatcher", {"auto": "Woke on the timer: working out what is due",
                            "live": "Woke for live lines", "injwatch": "Woke for the injury watch",
                            "research": "Woke for a research refresh", "props": "Woke for the props run",
                            "props-backup": "Woke for the props backup", "nightly": "Woke for overnight grading",
                            "briefing": "Woke for the 7am Daily Briefing", "cycle": "Woke for a market cycle"}.get(mode, f"Woke for {mode}"))
    except Exception:
        pass


def mark(desk, text):
    """This desk starts working now. Repeats of the same desk and text in a row are ignored."""
    try:
        if not _S["on"]:
            return
        st = _S["doc"]["steps"]
        if st and st[-1]["desk"] == desk and st[-1]["text"] == text:
            return
        if len(st) >= 40:
            return
        st.append({"desk": desk, "text": str(text)[:160], "at": round(time.time() - _S["t0"], 1)})
        _write()
    except Exception:
        pass


def finish():
    try:
        if not _S["on"]:
            return
        now = time.time()
        _S["doc"]["finished"] = _iso(now)
        _S["doc"]["secs"] = round(now - _S["t0"], 1)
        _write()
        _S["on"] = False
    except Exception:
        pass
