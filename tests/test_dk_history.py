"""Line history (Nov 24 2026): a point is added only when the line or a price changed."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import dk


def test_hist_add_only_on_change():
    h = {}
    dk.hist_add(h, "props", "1|pts", [10.5, 1.9, 1.87], "t1")
    dk.hist_add(h, "props", "1|pts", [10.5, 1.9, 1.87], "t2")      # same: no point
    dk.hist_add(h, "props", "1|pts", [10.5, 1.95, 1.82], "t3")     # price moved
    dk.hist_add(h, "props", "1|pts", [11.5, 1.9, 1.87], "t4")      # line moved
    dk.hist_add(h, "games", "g", [1.5, 1.87, 1.94, 214.5, 1.9, 1.9, 2.06, 1.8], "t1")
    assert [p[0] for p in h["props"]["1|pts"]] == ["t1", "t3", "t4"]
    assert len(h["games"]["g"]) == 1


def test_edge_record_counts_once_per_game_bucket(tmp_path, monkeypatch):
    import json
    from agent import config
    monkeypatch.setattr(config, "DATA", tmp_path)
    (tmp_path / "dk").mkdir()
    snap = lambda ts, ph: {"ts": ts, "line": {"sp_h": 1.91, "sp_a": 1.91}, "sim": {"p_home_cover": ph},
                           "res": {"spread_home": "won", "spread_away": "lost"}}
    # two snapshots with the same 10%+ edge on home count once; the away side has no edge
    G = {"final": True, "live": [snap("a", 0.60), snap("b", 0.61)]}
    (tmp_path / "dk" / "2026-11-20.json").write_text(json.dumps({"games": {"g1": G}}))
    e = dk.edge_record()["all"]
    assert e["large"]["n"] == 1 and e["large"]["w"] == 1 and e["large"]["units"] == 0.91
    assert e["neg"]["n"] == 1 and e["neg"]["l"] == 1 and e["neg"]["units"] == -1
