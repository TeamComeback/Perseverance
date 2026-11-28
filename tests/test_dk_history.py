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


def test_calibration_learns_to_shrink_and_scores_out_of_sample(tmp_path, monkeypatch):
    import json, random
    from agent import config, calib
    monkeypatch.setattr(config, "DATA", tmp_path)
    (tmp_path / "dk").mkdir()
    random.seed(1)
    for d in range(1, 6):                      # five days; the sim says 70% on bets that really hit 50% (market says 50%)
        games = {}
        for g in range(30):
            won = random.random() < 0.5
            games[f"g{g}"] = {"final": [100, 99], "live": [{"ts": f"t{g}", "line": {"sp_h": 1.91, "sp_a": 1.91},
                              "sim": {"p_home_cover": 0.7}, "res": {"spread_home": "won" if won else "lost", "spread_away": "lost" if won else "won"}}]}
        (tmp_path / "dk" / f"2026-11-0{d}.json").write_text(json.dumps({"games": games}))
    w = calib.fit(calib.rows())
    assert w["all"] <= 0.25                    # it learns the sim's 70% isn't trustworthy
    assert calib.apply({"all": 0.0, "spread": 0.0}, "spread", 0.7, 0.5) == 0.5
    out = calib.build()
    assert out["score"]["cal"] < out["score"]["sim"] and out["games"] == 150 and out["ready"] is False
    assert out["shadow"]["cal"]["n"] < out["shadow"]["raw"]["n"]     # fewer fake edges once shrunk
