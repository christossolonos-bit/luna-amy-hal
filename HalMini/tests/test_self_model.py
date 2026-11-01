"""Neurochemical self model (adapted from seed0001/human-sim): decay, events, loneliness, rest."""

from datetime import datetime

import pytest

from db.repository import Repository
from dialogue.self_model import CHEMICALS, HOUR, MINUTE, SelfModel

NOON = datetime(2026, 10, 30, 12, 0).timestamp()  # daytime, so no night-time sleepiness


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def model():
    repo = Repository(":memory:")
    clock = Clock(NOON)
    m = SelfModel(repo, clock=clock)
    m.clock_ref = clock
    yield m
    repo.close()


def test_starts_at_baseline(model):
    assert model.vars["chemicals"] == {n: b for n, (b, _) in CHEMICALS.items()}
    s = model.current()
    assert s["valence"] == 0 and s["loneliness"] == 0 and s["energy"] > 0.85
    assert model.mood_word() == "okay"


def test_events_release_chemicals_and_shift_mood(model):
    model.event("complimented")
    assert model.vars["chemicals"]["dopamine"] == 14.0
    assert model.current()["valence"] > 0.3 and model.is_state("happy")
    model.event("insulted", times=2)
    s = model.current()
    assert model.vars["chemicals"]["cortisol"] == 22.0
    assert s["stress"] > 0.55 and model.is_state("stressed")


def test_exponential_return_to_baseline(model):
    model.event("thanked")  # dopamine 10 -> 13
    model.clock_ref.t += 10 * MINUTE  # one dopamine half-life
    model.current()
    assert model.vars["chemicals"]["dopamine"] == pytest.approx(11.5)
    model.clock_ref.t += 45 * MINUTE  # still within the hour before loneliness lowers baselines
    model.current()
    assert model.vars["chemicals"]["dopamine"] == pytest.approx(10.0, abs=0.1)  # no overshoot


def test_loneliness_builds_without_contact(model):
    model.event("turn")
    model.clock_ref.t += 30 * MINUTE
    assert model.current()["loneliness"] == 0
    model.clock_ref.t += 5 * HOUR
    s = model.current()
    assert s["loneliness"] > 0.5 and model.is_state("lonely")
    assert model.vars["chemicals"]["serotonin"] < 10 and model.vars["chemicals"]["cortisol"] > 10
    model.event("turn")
    assert model.current()["loneliness"] == 0


def test_work_builds_adenosine_and_rest_clears_it(model):
    for _ in range(60):
        model.event("turn")
        model.clock_ref.t += 20
    worked = model.vars["adenosine"]
    assert worked > 3.5
    model.clock_ref.t += REST_HOURS * HOUR if (REST_HOURS := 2) else 0
    model.current()
    assert model.vars["adenosine"] < worked - 2


def test_long_awake_makes_it_tired_and_sleep_wakes_it(model):
    for _ in range(15):
        model.event("turn")
        model.clock_ref.t += 1 * HOUR - 1
    assert model.is_state("tired")
    model.clock_ref.t += 9 * HOUR  # a night of quiet
    model.event("turn")
    s = model.current()
    assert s["energy"] > 0.6 and not model.is_state("tired")
    assert model.vars["chemicals"]["cortisol"] > 14  # human-sim's wake-up cortisol spike


def test_state_persists(tmp_path):
    repo = Repository(str(tmp_path / "s.db"))
    clock = Clock(NOON)
    SelfModel(repo, clock=clock).event("thanked")
    again = SelfModel(repo, clock=clock)
    assert again.vars["chemicals"]["dopamine"] == 13.0
    repo.close()
