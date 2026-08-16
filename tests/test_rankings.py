"""Ranking ingest: tolerate duplicate poll entries from the CFBD feed.

Some lower-division CFBD polls list the same team twice in a single week, which
collides with the ``uq_ranking`` unique key. Ingest must dedup rather than raise.
"""

from __future__ import annotations

from sqlalchemy import func, select

from gridiron.db.models import Ranking
from gridiron.db.session import session_scope
from gridiron.ingest.pipeline import IngestReport, _ingest_rankings

# One poll listing "Washington St. Louis" twice in week 8 (the crash from the field).
_DUP_RECORDS = [
    {
        "week": 8,
        "seasonType": "regular",
        "polls": [
            {
                "poll": "AFCA Division III Coaches Poll",
                "ranks": [
                    {"rank": 21, "school": "Washington St. Louis", "conference": "CCIW", "points": 231},
                    {"rank": 21, "school": "Washington St. Louis", "conference": "CCIW", "points": 231},
                ],
            }
        ],
    }
]


class _RankSource:
    """Minimal stand-in exposing only what ``_ingest_rankings`` touches."""

    def __init__(self, records: list[dict]) -> None:
        self._records = records

    def rankings(self, year: int) -> list[dict]:
        return self._records


def test_duplicate_poll_entries_are_deduped(db_env):
    report = IngestReport(2022)
    with session_scope() as session:
        _ingest_rankings(_RankSource(_DUP_RECORDS), 2022, session, report)

    # The collision no longer raises; exactly one row survives and the count agrees.
    assert report.counts["rankings"] == 1
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Ranking)) == 1


def test_rankings_ingest_is_idempotent(db_env):
    src = _RankSource(_DUP_RECORDS)
    for _ in range(2):
        with session_scope() as session:
            _ingest_rankings(src, 2022, session, IngestReport(2022))
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Ranking)) == 1
