"""Progress-bar plumbing: pipeline emits stage events, reporter renders them."""

from __future__ import annotations

import io

from gridiron.ingest.pipeline import IngestReport, StageProgress, ingest_season
from gridiron.ingest.progress import ProgressReporter, _short


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_ingest_season_emits_paired_stage_events(db_env, fixture_source):
    events: list[StageProgress] = []
    ingest_season(fixture_source, 2023, progress=events.append)

    # Every stage fires exactly twice: running=True then running=False.
    assert events, "expected progress events"
    assert [e.running for e in events] == [i % 2 == 0 for i in range(len(events))]

    total = events[0].total
    assert total > 0
    assert all(e.total == total and e.year == 2023 for e in events)
    # completed advances 0..total across the running=False events.
    done = [e.completed for e in events if not e.running]
    assert done == list(range(1, total + 1))


def test_reporter_is_silent_without_a_tty():
    buf = io.StringIO()  # a plain StringIO reports isatty() False
    reporter = ProgressReporter([2023], stream=buf)
    reporter(StageProgress(2023, "games", 0, 4, True, IngestReport(2023)))
    assert buf.getvalue() == ""  # no bar drawn

    # finish_year still prints the permanent report line (cron log behavior).
    reporter.finish_year("[2023] games=2")
    assert buf.getvalue() == "[2023] games=2\n"


def test_reporter_spans_all_years_and_reaches_full():
    buf = _FakeTTY()
    years = [2022, 2023]
    reporter = ProgressReporter(years, stream=buf, width=10)
    report = IngestReport(2023)

    # Last stage of the last year -> bar should be full (100%).
    reporter(StageProgress(2023, "rankings", 4, 4, False, report))
    frame = buf.getvalue().rsplit("\r", 1)[-1]
    assert "100%" in frame
    assert "█" * 10 in frame

    # Half-way through the first of two years -> 25% overall (1 of 2 stages, 1 of 2 years).
    buf.truncate(0)
    buf.seek(0)
    reporter(StageProgress(2022, "games", 1, 2, False, report))
    assert "25%" in buf.getvalue()


def test_reporter_forced_disabled_even_on_tty():
    buf = _FakeTTY()
    reporter = ProgressReporter([2023], stream=buf, enabled=False)
    reporter(StageProgress(2023, "games", 0, 4, True, IngestReport(2023)))
    assert buf.getvalue() == ""


def test_short_number_formatting():
    assert _short(2) == "2"
    assert _short(812) == "812"
    assert _short(41200) == "41k"
    assert _short(2_500_000) == "2.5M"
