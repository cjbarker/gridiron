"""SQLAlchemy ORM models — the Gridiron schema.

Field names mirror the CollegeFootballData (CFBD) API v2 payloads so that
ingest transforms are a near 1:1 mapping. Primary keys reuse CFBD's own stable
integer IDs where they exist (teams, games, drives, plays) which makes ingestion
idempotent: re-running a season simply upserts the same rows.

Portable column types only (Integer/BigInteger/Float/Boolean/String/Text/DateTime)
so the same models run on SQLite (dev/test) and PostgreSQL (production).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    school: Mapped[str] = mapped_column(String(128), index=True)
    mascot: Mapped[str | None] = mapped_column(String(128))
    abbreviation: Mapped[str | None] = mapped_column(String(16))
    conference: Mapped[str | None] = mapped_column(String(64), index=True)
    division: Mapped[str | None] = mapped_column(String(64))
    classification: Mapped[str | None] = mapped_column(String(32))  # fbs / fcs / ...
    color: Mapped[str | None] = mapped_column(String(16))
    alt_color: Mapped[str | None] = mapped_column(String(16))
    logo: Mapped[str | None] = mapped_column(String(256))


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(96))
    state: Mapped[str | None] = mapped_column(String(32))
    capacity: Mapped[int | None] = mapped_column(Integer)
    grass: Mapped[bool | None] = mapped_column(Boolean)
    dome: Mapped[bool | None] = mapped_column(Boolean)


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int | None] = mapped_column(Integer, index=True)
    season_type: Mapped[str | None] = mapped_column(String(16))  # regular / postseason
    start_date: Mapped[datetime | None] = mapped_column(DateTime)
    completed: Mapped[bool | None] = mapped_column(Boolean)
    neutral_site: Mapped[bool | None] = mapped_column(Boolean)
    conference_game: Mapped[bool | None] = mapped_column(Boolean)
    attendance: Mapped[int | None] = mapped_column(Integer)
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"))

    home_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    home_team: Mapped[str | None] = mapped_column(String(128))
    home_conference: Mapped[str | None] = mapped_column(String(64))
    home_points: Mapped[int | None] = mapped_column(Integer)

    away_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team: Mapped[str | None] = mapped_column(String(128))
    away_conference: Mapped[str | None] = mapped_column(String(64))
    away_points: Mapped[int | None] = mapped_column(Integer)

    excitement_index: Mapped[float | None] = mapped_column(Float)

    drives: Mapped[list["Drive"]] = relationship(back_populates="game")
    plays: Mapped[list["Play"]] = relationship(back_populates="game")


class Drive(Base):
    __tablename__ = "drives"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    offense: Mapped[str | None] = mapped_column(String(128))
    defense: Mapped[str | None] = mapped_column(String(128))
    drive_number: Mapped[int | None] = mapped_column(Integer)
    scoring: Mapped[bool | None] = mapped_column(Boolean)
    start_period: Mapped[int | None] = mapped_column(Integer)
    start_yardline: Mapped[int | None] = mapped_column(Integer)
    start_yards_to_goal: Mapped[int | None] = mapped_column(Integer)
    end_period: Mapped[int | None] = mapped_column(Integer)
    end_yardline: Mapped[int | None] = mapped_column(Integer)
    end_yards_to_goal: Mapped[int | None] = mapped_column(Integer)
    plays: Mapped[int | None] = mapped_column(Integer)
    yards: Mapped[int | None] = mapped_column(Integer)
    drive_result: Mapped[str | None] = mapped_column(String(48))

    game: Mapped["Game"] = relationship(back_populates="drives")


class Play(Base):
    __tablename__ = "plays"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    # Denormalized season (set at ingest) so analytics filter plays directly by
    # year instead of joining games — a real win at multi-million-row scale.
    season: Mapped[int | None] = mapped_column(Integer, index=True)
    drive_id: Mapped[int | None] = mapped_column(ForeignKey("drives.id"), index=True)
    drive_number: Mapped[int | None] = mapped_column(Integer)
    play_number: Mapped[int | None] = mapped_column(Integer)

    offense: Mapped[str | None] = mapped_column(String(128), index=True)
    defense: Mapped[str | None] = mapped_column(String(128))
    offense_score: Mapped[int | None] = mapped_column(Integer)
    defense_score: Mapped[int | None] = mapped_column(Integer)

    period: Mapped[int | None] = mapped_column(Integer)
    clock_minutes: Mapped[int | None] = mapped_column(Integer)
    clock_seconds: Mapped[int | None] = mapped_column(Integer)

    down: Mapped[int | None] = mapped_column(Integer)
    distance: Mapped[int | None] = mapped_column(Integer)
    # Field position. yard_line is the absolute 0-100 spot; yards_to_goal is
    # distance to the opponent's end zone (0-100). Together these answer
    # "from what yardage on the field" for any play, scoring or not.
    yard_line: Mapped[int | None] = mapped_column(Integer)
    yards_to_goal: Mapped[int | None] = mapped_column(Integer, index=True)
    yards_gained: Mapped[int | None] = mapped_column(Integer)

    play_type: Mapped[str | None] = mapped_column(String(64), index=True)
    play_text: Mapped[str | None] = mapped_column(Text)

    # Scoring: `scoring` flags any play that changed the score; `points_scored`
    # is the point value derived from the play type (6 TD, 3 FG, 2 safety, ...).
    scoring: Mapped[bool | None] = mapped_column(Boolean, index=True)
    points_scored: Mapped[int | None] = mapped_column(Integer)

    # Advanced metrics. `ppa` comes straight from CFBD /plays; `epa`/`wp`/`wpa`
    # are populated when loading the cfbfastR bulk parquet (nullable otherwise).
    ppa: Mapped[float | None] = mapped_column(Float)
    epa: Mapped[float | None] = mapped_column(Float)
    wp: Mapped[float | None] = mapped_column(Float)
    # Win probability added on this play, credited to the primary player.
    wpa: Mapped[float | None] = mapped_column(Float)
    wpa_player: Mapped[str | None] = mapped_column(String(160), index=True)

    game: Mapped["Game"] = relationship(back_populates="plays")

    __table_args__ = (Index("ix_plays_game_drive", "game_id", "drive_id"),)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str | None] = mapped_column(String(96))
    last_name: Mapped[str | None] = mapped_column(String(96))
    team: Mapped[str | None] = mapped_column(String(128), index=True)
    position: Mapped[str | None] = mapped_column(String(16))
    height: Mapped[int | None] = mapped_column(Integer)
    weight: Mapped[int | None] = mapped_column(Integer)
    jersey: Mapped[int | None] = mapped_column(Integer)
    year: Mapped[int | None] = mapped_column(Integer)
    home_city: Mapped[str | None] = mapped_column(String(96))
    home_state: Mapped[str | None] = mapped_column(String(32))


class PlayerGameStat(Base):
    """One statistic for one player in one game (long/EAV form).

    e.g. (game 401550883, player 4429795, category 'passing', stat_type 'YDS', stat '305').
    """

    __tablename__ = "player_game_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    season: Mapped[int | None] = mapped_column(Integer, index=True)
    team: Mapped[str | None] = mapped_column(String(128), index=True)
    player_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    player: Mapped[str | None] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(32))  # passing, rushing, ...
    stat_type: Mapped[str] = mapped_column(String(32))  # YDS, TD, C/ATT, ...
    stat: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "game_id", "player_id", "category", "stat_type", name="uq_player_game_stat"
        ),
    )


class TeamGameStat(Base):
    """One team-level statistic for one team in one game (long/EAV form)."""

    __tablename__ = "team_game_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    season: Mapped[int | None] = mapped_column(Integer, index=True)
    team: Mapped[str] = mapped_column(String(128), index=True)
    stat_type: Mapped[str] = mapped_column(String(48))  # totalYards, possessionTime, ...
    stat: Mapped[str | None] = mapped_column(String(48))

    __table_args__ = (
        UniqueConstraint("game_id", "team", "stat_type", name="uq_team_game_stat"),
    )


class Ranking(Base):
    __tablename__ = "rankings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int | None] = mapped_column(Integer)
    season_type: Mapped[str | None] = mapped_column(String(16))
    poll: Mapped[str] = mapped_column(String(64))  # AP Top 25, Coaches Poll, ...
    rank: Mapped[int | None] = mapped_column(Integer)
    team: Mapped[str] = mapped_column(String(128), index=True)
    conference: Mapped[str | None] = mapped_column(String(64))
    points: Mapped[int | None] = mapped_column(Integer)
    first_place_votes: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "season", "week", "season_type", "poll", "team", name="uq_ranking"
        ),
    )


class BettingLine(Base):
    """One sportsbook's line for a game (CFBD /lines, one row per provider)."""

    __tablename__ = "betting_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    season: Mapped[int | None] = mapped_column(Integer, index=True)
    provider: Mapped[str] = mapped_column(String(48))
    spread: Mapped[float | None] = mapped_column(Float)  # closing, home-team perspective
    spread_open: Mapped[float | None] = mapped_column(Float)  # opening, home-team perspective
    formatted_spread: Mapped[str | None] = mapped_column(String(64))
    over_under: Mapped[float | None] = mapped_column(Float)  # closing total
    over_under_open: Mapped[float | None] = mapped_column(Float)  # opening total
    home_moneyline: Mapped[int | None] = mapped_column(Integer)
    away_moneyline: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("game_id", "provider", name="uq_betting_line"),
    )


class TeamRecruitingRank(Base):
    """A team's recruiting-class ranking for a cycle (CFBD /recruiting/teams)."""

    __tablename__ = "team_recruiting_ranks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    team: Mapped[str] = mapped_column(String(128), index=True)
    rank: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("season", "team", name="uq_team_recruiting"),
    )


class CoachSeason(Base):
    """One coach's record with a team for one season (CFBD /coaches)."""

    __tablename__ = "coach_seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coach: Mapped[str] = mapped_column(String(160), index=True)
    first_name: Mapped[str | None] = mapped_column(String(96))
    last_name: Mapped[str | None] = mapped_column(String(96))
    team: Mapped[str] = mapped_column(String(128), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    games: Mapped[int | None] = mapped_column(Integer)
    wins: Mapped[int | None] = mapped_column(Integer)
    losses: Mapped[int | None] = mapped_column(Integer)
    ties: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("coach", "team", "season", name="uq_coach_season"),
    )


class Transfer(Base):
    """A transfer-portal entry (CFBD /player/portal)."""

    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    player: Mapped[str | None] = mapped_column(String(160))
    position: Mapped[str | None] = mapped_column(String(16))
    origin: Mapped[str | None] = mapped_column(String(128), index=True)
    destination: Mapped[str | None] = mapped_column(String(128), index=True)
    rating: Mapped[float | None] = mapped_column(Float)
    stars: Mapped[int | None] = mapped_column(Integer)
