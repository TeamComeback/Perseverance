"""HQ items t20261123run3 (end run #2, start run #3 at $100) and p20261119perseverance (playbook scorecard)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import book as bk, config


def _bet(book, pb, stake, status, profit=None, run=None, close_p=None):
    b = {"id": pb + str(len(book["bets"])), "run": run or book["run"], "playbook": pb, "stake": stake, "status": status,
         "profit": profit, "legs": [{"price": 2.0, "close_p": close_p}]}
    book["bets"].append(b)


def test_wind_down_blocks_new_bets_then_rolls_over():
    book = bk.new_book("T", run=2)
    book["cash"] = 30.0
    _bet(book, "arb", 5.0, "open")
    bk.request_end(book, "owner")
    leg = {"price": 2.0, "result": None}
    assert bk.place(book, [leg], 5.0, "value_fav") is None          # nothing new while winding down
    assert bk.check_race(book, "T") is None                          # an open bet is still out
    book["bets"][0].update(status="won", profit=5.0); book["cash"] += 10.0
    r = bk.check_race(book, "T")
    assert r["result"] == "ended" and r["run"] == 2 and r["note"] == "owner" and r["won"] == 1
    assert book["run"] == 3 and book["cash"] == config.START_BANKROLL == 100.0 and "wind_down" not in book
    assert bk.place(book, [leg], 5.0, "value_fav")["run"] == 3        # betting resumes in run #3


def test_scorecard_current_run_and_all_runs():
    book = bk.new_book("T", run=3)
    _bet(book, "value_dog", 5, "lost", -5.0, run=2, close_p=0.4)
    _bet(book, "value_dog", 5, "won", 6.0, run=3, close_p=0.6)
    _bet(book, "arb", 5, "open")
    s = bk.playbook_scorecard(book, ["value_dog"])
    assert s["run"] == 3 and set(s["current"]) == {"value_dog", "arb"} and s["all"]["value_dog"]["bets"] == 2
    assert s["current"]["value_dog"]["roi"] == 1.2 and s["current"]["value_dog"]["paused"]
    assert s["current"]["arb"]["open"] == 1 and s["current"]["arb"]["roi"] is None


def test_out_of_funds_loses_run_now_and_old_bets_grade_under_old_run():
    book = bk.new_book("T", run=4)
    book["cash"] = 0.67
    _bet(book, "arb", 5.0, "open")
    _bet(book, "arb", 10.0, "open")
    r = bk.check_race(book, "T")
    assert r["result"] == "busted" and r["loss"] and r["open_at_end"] == 2 and r["final"] == 15.67
    assert book["run"] == 5 and book["cash"] == 100.0 and bk.equity(book) == 100.0      # new run is clean
    old = book["bets"][0]
    old["legs"][0]["result"] = "win"
    assert bk.settle(book, old)
    assert book["cash"] == 100.0 and book["runs"][0]["final"] == 20.67 and book["runs"][0]["late"]   # credited to run 4 only


def test_owner_reset_ends_run_as_loss():
    book = bk.new_book("T", run=1)
    _bet(book, "arb", 5.0, "open")
    r = bk.check_race(book, "T", force="ended")
    assert r["result"] == "ended" and r["loss"] and book["run"] == 2 and book["cash"] == 100.0
