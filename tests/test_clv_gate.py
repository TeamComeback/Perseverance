"""value_dog stays benched until its closing-line value is positive (HQ item p20261031perseverance)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import brain


def arm(clv_n, clv_sum, gate=True, bets=11, profit=-30.1, staked=55.0):
    return {"bets": bets, "staked": staked, "profit": profit, "clv_n": clv_n, "clv_sum": clv_sum, "clv_gate": gate}


def test_value_dog_benched_on_negative_clv():
    assert brain.benched(arm(10, -0.41))


def test_value_dog_returns_on_positive_clv():
    assert not brain.benched(arm(12, 0.24, bets=13, profit=2.0, staked=65.0))


def test_needs_enough_legs():
    assert brain.benched(arm(3, 0.2))


def test_ungated_playbook_unchanged():
    assert not brain.benched(arm(10, -0.41, gate=False))
