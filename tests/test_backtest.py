"""Tests for walk-forward back-testing (`analytics/backtest.py`)."""

from __future__ import annotations

import itertools

from gridiron.analytics.backtest import backtest_season
from gridiron.db.models import BettingLine, Game
from gridiron.db.session import session_scope

_TEAMS = [f"T{i}" for i in range(6)]
_STRENGTH = {t: i * 7 for i, t in enumerate(_TEAMS)}  # higher index → stronger


def _seed_synthetic_season(session, season: int = 2020) -> None:
    """A deterministic multi-week season where scores track a hidden strength, so the
    model has real signal and every week (after the first) has prior history."""
    gid = 0
    for i, (home, away) in enumerate(itertools.combinations(_TEAMS, 2)):
        gid += 1
        week = i // 3 + 1  # 3 games per week
        margin = _STRENGTH[home] - _STRENGTH[away] + 3  # +3 home edge
        hp = max(round(24 + margin / 2), 0)
        ap = max(round(24 - margin / 2), 0)
        session.add(
            Game(
                id=gid, season=season, week=week, season_type="regular",
                home_team=home, away_team=away, home_points=hp, away_points=ap,
                neutral_site=False, completed=True,
            )
        )
        if i % 2 == 0:  # market line on half the games
            session.add(
                BettingLine(
                    game_id=gid, season=season, provider="test",
                    spread=-(margin) / 2, spread_open=-(margin) / 2, over_under=48.0,
                )
            )


def test_backtest_ratings_scores_and_benchmarks(db_env):
    with session_scope() as s:
        _seed_synthetic_season(s, 2020)
    with session_scope() as s:
        r = backtest_season(s, 2020, model="ratings", min_prior_games=3)
    assert r["games"] > 0
    assert 0.0 <= r["accuracy"] <= 1.0
    assert r["mae"] is not None and r["rmse"] is not None
    assert r["brier"] is not None and r["log_loss"] is not None
    assert isinstance(r["calibration"], list)
    # Market benchmark present because we seeded closing lines.
    assert r["market"] is not None
    assert 0.0 <= r["market"]["accuracy"] <= 1.0
    assert r["market"]["ats_wins"] + r["market"]["ats_losses"] + r["market"]["ats_pushes"] > 0


def test_backtest_logistic_runs_or_falls_back(db_env):
    with session_scope() as s:
        _seed_synthetic_season(s, 2020)
    with session_scope() as s:
        r = backtest_season(s, 2020, model="logistic", min_prior_games=3)
    assert r["requested_model"] == "logistic"
    assert r["model"] in ("logistic", "ratings")  # ratings if sklearn/classes unavailable
    assert r["games"] > 0
