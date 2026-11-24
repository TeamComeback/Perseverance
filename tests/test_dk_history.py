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
