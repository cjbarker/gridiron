"""Season ingest pipeline.

``ingest_season`` pulls one season from a :class:`DataSource` and upserts it into
the database. It is **idempotent**: entities with natural CFBD IDs are ``merge``d
(insert-or-update by primary key), and the long-form box-score/ranking tables are
replaced within the season's scope. Re-running a season therefore never creates
duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from gridiron.db.models import (
    Drive,
    Game,
    Play,
    PlayerGameStat,
    Ranking,
    Team,
    TeamGameStat,
    Venue,
)
from gridiron.db.session import session_scope
from gridiron.ingest import transforms as tf
from gridiron.ingest.sources import POSTSEASON, REGULAR, DataSource


@dataclass
class IngestReport:
    year: int
    counts: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def __str__(self) -> str:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
        return f"[{self.year}] {parts}"


def ingest_season(
    source: DataSource,
    year: int,
    *,
    season_types: tuple[str, ...] = (REGULAR, POSTSEASON),
    with_stats: bool = True,
) -> IngestReport:
    """Ingest a full season into the DB. Returns counts of rows upserted."""
    report = IngestReport(year=year)
    with session_scope() as session:
        _ingest_reference(source, year, session, report)
        game_ids: set[int] = set()
        for st in season_types:
            _ingest_games(source, year, st, session, report, game_ids)
        for st in season_types:
            _ingest_drives(source, year, st, session, report, game_ids)
        for st in season_types:
            _ingest_plays(source, year, st, session, report, game_ids)
        if with_stats:
            _ingest_box_scores(source, year, season_types, session, report, game_ids)
        _ingest_rankings(source, year, session, report)
    return report


def _ingest_reference(source: DataSource, year: int, session: Session, report: IngestReport) -> None:
    for raw in source.venues():
        v = tf.to_venue(raw)
        if v.id is not None:
            session.merge(v)
            report.bump("venues")
    for raw in source.teams(year):
        t = tf.to_team(raw)
        if t.id is not None:
            session.merge(t)
            report.bump("teams")
    session.flush()


def _ingest_games(
    source: DataSource,
    year: int,
    season_type: str,
    session: Session,
    report: IngestReport,
    game_ids: set[int],
) -> None:
    for raw in source.games(year, season_type):
        g = tf.to_game(raw)
        if g.id is None:
            continue
        session.merge(g)
        game_ids.add(g.id)
        report.bump("games")
    session.flush()


def _ingest_drives(
    source: DataSource,
    year: int,
    season_type: str,
    session: Session,
    report: IngestReport,
    game_ids: set[int],
) -> None:
    for raw in source.drives(year, season_type):
        d = tf.to_drive(raw)
        if d.id is None or d.game_id not in game_ids:
            continue
        session.merge(d)
        report.bump("drives")
    session.flush()


def _ingest_plays(
    source: DataSource,
    year: int,
    season_type: str,
    session: Session,
    report: IngestReport,
    game_ids: set[int],
) -> None:
    # Known drive IDs so we never dangle a play FK at a drive we didn't ingest.
    known_drives = set(session.execute(select(Drive.id)).scalars().all())
    for week in source.weeks(year, season_type):
        for raw in source.plays(year, week, season_type):
            p = tf.to_play(raw)
            if p.id is None or p.game_id not in game_ids:
                continue
            if p.drive_id is not None and p.drive_id not in known_drives:
                p.drive_id = None
            session.merge(p)
            report.bump("plays")
        session.flush()


def _ingest_box_scores(
    source: DataSource,
    year: int,
    season_types: tuple[str, ...],
    session: Session,
    report: IngestReport,
    game_ids: set[int],
) -> None:
    # Replace this season's box-score rows for clean idempotency.
    if game_ids:
        session.query(PlayerGameStat).filter(PlayerGameStat.game_id.in_(game_ids)).delete(
            synchronize_session=False
        )
        session.query(TeamGameStat).filter(TeamGameStat.game_id.in_(game_ids)).delete(
            synchronize_session=False
        )
    for st in season_types:
        for week in source.weeks(year, st):
            for rec in source.player_game_stats(year, week, st):
                gid = tf._int(tf.pick(rec, "id", "gameId", "game_id"))
                if gid not in game_ids:
                    continue
                rows = tf.flatten_player_game_stats(gid, year, rec)
                session.add_all(rows)
                report.bump("player_game_stats", len(rows))
            for rec in source.team_game_stats(year, week, st):
                gid = tf._int(tf.pick(rec, "id", "gameId", "game_id"))
                if gid not in game_ids:
                    continue
                rows = tf.flatten_team_game_stats(gid, year, rec)
                session.add_all(rows)
                report.bump("team_game_stats", len(rows))
            session.flush()


def _ingest_rankings(source: DataSource, year: int, session: Session, report: IngestReport) -> None:
    records = source.rankings(year)
    if not records:
        return
    session.query(Ranking).filter(Ranking.season == year).delete(synchronize_session=False)
    rows = tf.flatten_rankings(year, records)
    session.add_all(rows)
    report.bump("rankings", len(rows))
    session.flush()
