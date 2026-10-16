"""Postęp długiego importu (AU-106): liczniki, ETA, linia stderr i odstęp komunikatów ≤ 10 s."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

import pytest

from app.core.settings import Settings
from app.modules.imports.api.cli import parse_args
from app.modules.imports.application.progress import (
    DEFAULT_INTERVAL_SECONDS,
    HeartbeatReporter,
    ImportProgress,
)


@dataclass
class Event:
    type_name: str
    start_index: int = 0
    returned: int = 10
    fetched: int = 10
    matched: int | None = 100
    pages_done: int = 1
    pages_total: int | None = 10
    from_store: bool = False


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_counters_follow_page_events_per_type() -> None:
    clock = Clock()
    progress = ImportProgress(clock=clock)
    progress.set_phase("download")
    progress.type_started("app-pog:StrefaPlanistyczna")
    clock.now += 10
    progress.on_page(Event("app-pog:StrefaPlanistyczna", fetched=40, pages_done=4, matched=100))
    progress.on_page(Event("app-pog:StrefaPlanistyczna", fetched=30, pages_done=3, matched=100))  # spóźnione zdarzenie

    data = progress.snapshot()

    state = data["types"]["app-pog:StrefaPlanistyczna"]
    assert (state["fetched"], state["pages_done"], state["matched"], state["pages_total"]) == (40, 4, 100, 10)
    assert data["objects_fetched"] == 40 and data["objects_total"] == 100 and data["phase"] == "download"


def test_eta_comes_from_the_network_rate_and_ignores_pages_taken_from_the_store() -> None:
    clock = Clock()
    progress = ImportProgress(clock=clock)
    progress.set_phase("download")
    progress.on_page(Event("t", returned=50, fetched=50, matched=100, pages_done=5, from_store=True))
    clock.now += 10
    progress.on_page(Event("t", returned=10, fetched=60, matched=100, pages_done=6))

    # 10 obiektów z sieci w 10 s => 1 obiekt/s, do końca zostaje 40 obiektów.
    assert progress.eta_seconds() == pytest.approx(40.0)
    assert progress.snapshot()["objects_from_store"] == 50


def test_eta_is_unknown_until_there_is_something_to_extrapolate_from() -> None:
    progress = ImportProgress(clock=Clock())
    assert progress.eta_seconds() is None
    progress.on_page(Event("t", fetched=10, matched=None))
    assert progress.eta_seconds() is None  # nieznana liczba obiektów — nie zgadujemy


def test_line_is_human_readable_and_mentions_the_resumed_part() -> None:
    clock = Clock()
    progress = ImportProgress(clock=clock)
    progress.set_phase("download")
    progress.type_started("app-pog:StrefaPlanistyczna")
    progress.on_page(Event("app-pog:StrefaPlanistyczna", returned=20, fetched=20, matched=100, pages_done=2, from_store=True))
    clock.now += 4
    progress.on_page(Event("app-pog:StrefaPlanistyczna", fetched=30, matched=100, pages_done=3))

    line = progress.line()

    assert line.startswith("[import] download 4 s")
    assert "app-pog:StrefaPlanistyczna 30/100, strony 3/10" in line
    assert "razem 30/100 obiektów" in line and "w tym 20 z poprzedniego przebiegu" in line
    assert "ETA " in line


def test_phases_are_timed_and_snapshot_is_json_serialisable() -> None:
    import json

    clock = Clock()
    progress = ImportProgress(clock=clock)
    progress.set_phase("download")
    clock.now += 7
    progress.set_phase("parse")
    clock.now += 3
    progress.set_phase("done")

    data = progress.snapshot()

    assert data["phases"]["download"] == 7.0 and data["phases"]["parse"] == 3.0
    json.dumps(data)


def test_heartbeat_prints_at_most_every_interval_and_also_without_new_pages() -> None:
    progress = ImportProgress()
    progress.set_phase("download")
    stream = io.StringIO()
    ticks: list[int] = []
    reporter = HeartbeatReporter(progress, stream, interval=0.05, on_tick=lambda: ticks.append(1))

    reporter.start()
    time.sleep(0.32)  # żadna strona nie dociera — komunikat o żywotności musi się pojawiać
    reporter.stop()

    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) >= 5 and all(line.startswith("[import]") for line in lines)
    assert len(ticks) >= 4  # zapis checkpointu przy każdym komunikacie


def test_tick_failure_does_not_stop_the_reporting() -> None:
    stream = io.StringIO()

    def failing() -> None:
        raise RuntimeError("baza niedostępna")

    reporter = HeartbeatReporter(ImportProgress(), stream, interval=0.05, on_tick=failing)
    reporter.start()
    time.sleep(0.18)
    reporter.stop()

    assert len([line for line in stream.getvalue().splitlines() if line]) >= 3


def test_interval_must_be_positive() -> None:
    with pytest.raises(ValueError):
        HeartbeatReporter(ImportProgress(), io.StringIO(), interval=0)


def test_default_interval_meets_the_ten_second_contract() -> None:
    assert DEFAULT_INTERVAL_SECONDS <= 10.0
    assert Settings(_env_file=None).import_progress_interval_seconds <= 10.0
    assert 1 <= Settings(_env_file=None).import_wfs_concurrency <= 8


@pytest.mark.parametrize("argument", ["0", "9", "x", "-1"])
def test_cli_rejects_concurrency_outside_the_politeness_range(argument: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["pog", "--source", "pog_app", "--act-id", "a", "--concurrency", argument])
    assert exit_info.value.code == 2


@pytest.mark.parametrize("argument", ["0", "-5", "7200", "abc"])
def test_cli_rejects_a_progress_interval_that_would_hide_the_import(argument: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["pog", "--source", "pog_app", "--act-id", "a", "--progress-interval", argument])
    assert exit_info.value.code == 2


def test_cli_parses_the_resume_options() -> None:
    command = parse_args(
        ["pog", "--source", "pog_app", "--act-id", "a", "--concurrency", "6", "--fresh", "--progress-interval", "2.5"]
    )
    assert (command.concurrency, command.fresh, command.no_resume, command.progress_interval) == (6, True, False, 2.5)
    default = parse_args(["pog", "--source", "pog_app", "--act-id", "a"])
    assert (default.concurrency, default.fresh, default.no_resume, default.progress_interval) == (None, False, False, None)
