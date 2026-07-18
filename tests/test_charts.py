"""Tests for the Plotly chart builders — each returns valid figure JSON."""

from __future__ import annotations

import json

from gridiron.analytics import charts as ch
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import ingest_season


def _valid_figure(spec: str) -> bool:
    d = json.loads(spec)
    return isinstance(d, dict) and "data" in d and "layout" in d


def test_season_charts_are_valid_json(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        for spec in (
            ch.scoring_field_position_fig(s, 2023),
            ch.scoring_type_fig(s, 2023),
            ch.fg_success_fig(s, 2023),
            ch.play_type_mix_fig(s, 2023),
            ch.ppa_by_down_fig(s, 2023),
        ):
            assert _valid_figure(spec)


def test_team_charts_are_valid_json(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        trend = ch.team_points_trend_fig(s, "Georgia", 2023)
        eff = ch.success_explosive_fig(s, 2023, "Georgia")
    trend_fig = json.loads(trend)
    assert len(trend_fig["data"]) == 2  # points for + against
    eff_fig = json.loads(eff)
    assert eff_fig["data"][0]["y"] == [75.0, 16.7]  # success %, explosive %


def test_empty_chart_still_valid(db_env, fixture_source):
    ingest_season(fixture_source, 2023)
    with session_scope() as s:
        # A team with no plays should still yield a valid (empty-state) figure.
        assert _valid_figure(ch.scoring_field_position_fig(s, 2023, team="Nobody"))
