"""Validation tests for known_period_days on the request models."""
import pytest
from pydantic import ValidationError

from backend.app.main import MastQuery, MultisectorQuery


def test_known_period_days_accepts_positive_finite_single_sector():
    req = MastQuery(tic_id=12345, sector=1, known_period_days=3.14)
    assert req.known_period_days == 3.14


def test_known_period_days_defaults_to_none_single_sector():
    req = MastQuery(tic_id=12345, sector=1)
    assert req.known_period_days is None


def test_known_period_days_accepts_positive_finite_multi_sector():
    req = MultisectorQuery(tic_id=12345, known_period_days=2.5)
    assert req.known_period_days == 2.5


def test_known_period_days_defaults_to_none_multi_sector():
    req = MultisectorQuery(tic_id=12345)
    assert req.known_period_days is None


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("-inf"),
                                 float("nan"), 1001])
def test_known_period_days_rejects_invalid_single_sector(bad):
    with pytest.raises(ValidationError):
        MastQuery(tic_id=12345, sector=1, known_period_days=bad)


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("-inf"),
                                 float("nan"), 1001])
def test_known_period_days_rejects_invalid_multi_sector(bad):
    with pytest.raises(ValidationError):
        MultisectorQuery(tic_id=12345, known_period_days=bad)
