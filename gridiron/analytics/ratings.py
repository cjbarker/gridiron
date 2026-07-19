"""Team power ratings for the prediction engine.

A **Simple Rating System (SRS)**: a team's rating is its average scoring margin
adjusted for strength of schedule, solved iteratively. We also solve
opponent-adjusted **offense/defense** point levels, so a matchup can project an
actual *score*, not just a margin. Everything is computed on the fly from the
``games`` table — **no schema, stdlib only** — and is cheap enough to run per
season (and per week for the walk-forward backtest via ``through_week``).

The numbers here are *raw* (points, centered on each season's average). Cross-era
normalization (z-scoring within a season) lives in :mod:`gridiron.analytics.predict`
so this module stays a pure ratings calculator.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from gridiron.db.models import Game

MARGIN_CAP = 24  # damp blowouts so one 70-0 game can't dominate a rating
_ITERATIONS = 20
DEFAULT_HFA = 2.5  # fallback home-field points when a season has no non-neutral games


class GameRow(NamedTuple):
    game_id: int
    home: str
    away: str
    home_points: int
    away_points: int
    neutral: bool
    week: int | None


@dataclass
class TeamRating:
    team: str
    games: int
    srs: float  # points better than an average team (schedule-adjusted margin)
    adj_off: float  # points scored above league average, opponent-adjusted
    adj_def: float  # points allowed *below* league average (higher = better defense)
    sos: float  # average opponent SRS (strength of schedule)
    form: float  # last-3-game average margin (uncapped)
    ppg: float
    papg: float


@dataclass
class SeasonRatings:
    season: int
    teams: dict[str, TeamRating]
    league_avg_points: float
    hfa: float
    srs_std: float  # spread of SRS this season — the scale for era normalization

    def get(self, team: str) -> TeamRating | None:
        return self.teams.get(team)


def _clamp(x: float, cap: float = MARGIN_CAP) -> float:
    return max(-cap, min(cap, x))


def load_season_games(
    session: Session, season: int, *, through_week: int | None = None
) -> list[GameRow]:
    """Completed games for a season as plain rows (optionally only weeks < cutoff)."""
    stmt = select(Game).where(Game.season == season)
    if through_week is not None:
        stmt = stmt.where(Game.week < through_week)
    rows: list[GameRow] = []
    for g in session.execute(stmt.order_by(Game.week, Game.start_date, Game.id)).scalars():
        if g.home_points is None or g.away_points is None or not g.home_team or not g.away_team:
            continue
        rows.append(
            GameRow(
                g.id, g.home_team, g.away_team, g.home_points, g.away_points,
                bool(g.neutral_site), g.week,
            )
        )
    return rows


def home_field_advantage(games: list[GameRow]) -> float:
    """Fit home-field points = mean home margin over non-neutral games."""
    margins = [g.home_points - g.away_points for g in games if not g.neutral]
    return round(statistics.fmean(margins), 2) if margins else DEFAULT_HFA


def compute_ratings(season: int, games: list[GameRow]) -> SeasonRatings:
    """Solve SRS + opponent-adjusted offense/defense from a season's games.

    Pure function over ``games`` (no DB) so it is unit-testable with synthetic data.
    """
    teams = sorted({t for g in games for t in (g.home, g.away)})
    empty = SeasonRatings(season, {}, 0.0, home_field_advantage(games), 0.0)
    if not teams:
        return empty

    # Per-team game views from each side's perspective: (opponent, margin, pf, pa).
    per_team: dict[str, list[tuple[str, int, int, int]]] = {t: [] for t in teams}
    all_points: list[int] = []
    for g in games:
        per_team[g.home].append((g.away, g.home_points - g.away_points, g.home_points, g.away_points))
        per_team[g.away].append((g.home, g.away_points - g.home_points, g.away_points, g.home_points))
        all_points.extend((g.home_points, g.away_points))
    league_avg = statistics.fmean(all_points)

    # --- SRS: rating = avg capped margin + avg opponent rating (iterate, recenter) ---
    srs = {t: statistics.fmean([_clamp(m) for _, m, _, _ in gl]) for t, gl in per_team.items()}
    for _ in range(_ITERATIONS):
        nxt = {
            t: statistics.fmean([_clamp(m) + srs[opp] for opp, m, _, _ in gl])
            for t, gl in per_team.items()
        }
        center = statistics.fmean(list(nxt.values()))
        srs = {t: v - center for t, v in nxt.items()}

    # --- Opponent-adjusted offense/defense (points above/below league average) ---
    # pts_by(t vs o) ≈ league_avg + off[t] + dallow[o]; dallow = points a defense yields
    # above average (higher = worse D). Solve by alternating means.
    off = {t: 0.0 for t in teams}
    dallow = {t: 0.0 for t in teams}
    for _ in range(_ITERATIONS):
        off = {
            t: statistics.fmean([pf - league_avg - dallow[opp] for opp, _, pf, _ in gl])
            for t, gl in per_team.items()
        }
        dallow = {
            t: statistics.fmean([pa - league_avg - off[opp] for opp, _, _, pa in gl])
            for t, gl in per_team.items()
        }

    srs_std = statistics.pstdev(list(srs.values())) if len(srs) > 1 else 0.0
    out: dict[str, TeamRating] = {}
    for t in teams:
        gl = per_team[t]
        sos = statistics.fmean([srs[opp] for opp, _, _, _ in gl])
        recent = [m for _, m, _, _ in gl[-3:]]
        out[t] = TeamRating(
            team=t,
            games=len(gl),
            srs=round(srs[t], 2),
            adj_off=round(off[t], 2),
            adj_def=round(-dallow[t], 2),  # flip so higher = better defense
            sos=round(sos, 2),
            form=round(statistics.fmean(recent), 2) if recent else 0.0,
            ppg=round(statistics.fmean([pf for _, _, pf, _ in gl]), 1),
            papg=round(statistics.fmean([pa for _, _, _, pa in gl]), 1),
        )
    return SeasonRatings(season, out, round(league_avg, 2), home_field_advantage(games), round(srs_std, 2))


def team_ratings(
    session: Session, season: int, *, through_week: int | None = None
) -> SeasonRatings:
    """Load a season's games and solve its ratings (see :func:`compute_ratings`)."""
    return compute_ratings(season, load_season_games(session, season, through_week=through_week))
