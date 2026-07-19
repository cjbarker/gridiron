"""Season ingest pipeline.

``ingest_season`` pulls one season from a :class:`DataSource` and upserts it into
the database. It is **idempotent**: entities with natural CFBD IDs are ``merge``d
(insert-or-update by primary key), and the long-form box-score/ranking tables are
replaced within the season's scope. Re-running a season therefore never creates
duplicates.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from gridiron.db.models import (
    BettingLine,
    Drive,
    Game,
    Play,
    PlayerGameStat,
    Ranking,
    TeamGameStat,
)
from gridiron.db.session import session_scope
from gridiron.ingest import transforms as tf
from gridiron.ingest.sources import POSTSEASON, REGULAR, DataSource, load_pbp_parquet


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
    with_rosters: bool = True,
    with_lines: bool = True,
    plays_source: str = "api",
    parquet_loader: Callable[[int], list[dict]] | None = None,
    stub_games: bool = False,
) -> IngestReport:
    """Ingest a full season into the DB. Returns counts of rows upserted.

    ``plays_source="parquet"`` loads plays from the cfbfastR bulk parquet (with
    EPA/WP) instead of the CFBD ``/plays`` API. With ``stub_games=True`` the
    parquet step also synthesizes any missing games, so a backfill can run with no
    CFBD key at all.
    """
    report = IngestReport(year=year)
    with session_scope() as session:
        _ingest_reference(source, year, session, report)
        if with_rosters:
            _ingest_rosters(source, year, session, report)
        game_ids: set[int] = set()
        for st in season_types:
            _ingest_games(source, year, st, session, report, game_ids)
        for st in season_types:
            _ingest_drives(source, year, st, session, report, game_ids)
        if plays_source == "parquet":
            _ingest_plays_parquet(
                year, session, report, game_ids, loader=parquet_loader, stub_games=stub_games
            )
        else:
            for st in season_types:
                _ingest_plays(source, year, st, session, report, game_ids)
        if with_stats:
            _ingest_box_scores(source, year, season_types, session, report, game_ids)
        if with_lines:
            _ingest_betting_lines(source, year, season_types, session, report, game_ids)
        _ingest_rankings(source, year, session, report)
    return report


def current_season(today: _dt.date | None = None) -> int:
    """The season a given date belongs to.

    A college football season spans Aug–Jan, so Aug–Dec map to that calendar
    year and Jan–Jul map to the prior year (bowls/playoff of the season before).
    """
    today = today or _dt.date.today()
    return today.year if today.month >= 8 else today.year - 1


def refresh_current_season(source: DataSource, *, today: _dt.date | None = None, **kwargs) -> IngestReport:
    """Idempotently re-ingest the in-progress season (games/plays/rankings, etc.).

    Thin wrapper over :func:`ingest_season` for the current season; safe to run on
    a schedule (cron) since ingestion upserts.
    """
    return ingest_season(source, current_season(today), **kwargs)


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


def _ingest_rosters(source: DataSource, year: int, session: Session, report: IngestReport) -> None:
    for raw in source.rosters(year):
        p = tf.to_player(raw)
        if p.id is not None:
            session.merge(p)
            report.bump("players")
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
            p = tf.to_play(raw, season=year)
            if p.id is None or p.game_id not in game_ids:
                continue
            if p.drive_id is not None and p.drive_id not in known_drives:
                p.drive_id = None
            session.merge(p)
            report.bump("plays")
        session.flush()


def _ingest_plays_parquet(
    year: int,
    session: Session,
    report: IngestReport,
    game_ids: set[int],
    *,
    loader: Callable[[int], list[dict]] | None = None,
    stub_games: bool = False,
    batch_size: int = 5000,
) -> None:
    """Load a season's plays from the cfbfastR bulk parquet (with EPA/WP).

    Idempotent by season: existing plays for ``year`` are deleted, then the parquet
    rows are bulk-inserted. With ``stub_games`` any game not already present is
    synthesized from its plays so the load works without the CFBD API.
    """
    rows = (loader or load_pbp_parquet)(year)
    if not rows:
        return

    if stub_games:
        by_game: dict[int, list[dict]] = {}
        for r in rows:
            gid = tf._int(tf.pick(r, "gameId", "game_id"))
            if gid is not None:
                by_game.setdefault(gid, []).append(r)
        existing = set(
            session.execute(select(Game.id).where(Game.id.in_(by_game))).scalars().all()
        )
        for gid, plays in by_game.items():
            if gid not in existing:
                session.merge(tf.to_game_stub(year, gid, plays))
                report.bump("games")
            game_ids.add(gid)
        session.flush()

    known_drives = set(session.execute(select(Drive.id)).scalars().all())
    # Replace this season's plays for clean idempotency, then bulk insert.
    session.execute(delete(Play).where(Play.season == year))

    mappings: list[dict] = []
    seen: set[int] = set()
    for raw in rows:
        p = tf.to_play(raw, season=year)
        if p.id is None or p.game_id not in game_ids or p.id in seen:
            continue
        seen.add(p.id)
        if p.drive_id is not None and p.drive_id not in known_drives:
            p.drive_id = None
        mappings.append(_play_mapping(p))
        if len(mappings) >= batch_size:
            session.execute(insert(Play), mappings)
            report.bump("plays", len(mappings))
            mappings = []
    if mappings:
        session.execute(insert(Play), mappings)
        report.bump("plays", len(mappings))
    session.flush()


_PLAY_COLUMNS = [c.name for c in Play.__table__.columns]


def _play_mapping(p: Play) -> dict:
    return {col: getattr(p, col) for col in _PLAY_COLUMNS}


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


def _ingest_betting_lines(
    source: DataSource,
    year: int,
    season_types: tuple[str, ...],
    session: Session,
    report: IngestReport,
    game_ids: set[int],
) -> None:
    if game_ids:
        session.query(BettingLine).filter(BettingLine.game_id.in_(game_ids)).delete(
            synchronize_session=False
        )
    seen: set[tuple[int, str]] = set()  # (game_id, provider) — guards duplicate rows
    for st in season_types:
        for rec in source.betting_lines(year, st):
            gid = tf._int(tf.pick(rec, "id", "gameId", "game_id"))
            if gid not in game_ids:
                continue
            for row in tf.flatten_betting_lines(gid, year, rec):
                key = (gid, row.provider)
                if key in seen:
                    continue
                seen.add(key)
                session.add(row)
                report.bump("betting_lines")
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
