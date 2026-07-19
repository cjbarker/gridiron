"""Regenerate the tiny cfbfastR-schema play-by-play parquet used by tests.

Run: ``python tests/fixtures/pbp/generate_fixture.py``

The column names deliberately mirror the real cfbfastR bulk parquet
(``pos_team``/``def_pos_team``, ``id_play``, dotted ``clock.minutes``, ``EPA``,
``wp_before`` …) so the transform mapping in ``gridiron.ingest.transforms.to_play``
is locked against the real schema. Game 401403910 (Ohio State vs Notre Dame, 2022
wk1) is intentionally NOT in the JSON fixtures, so the backfill's game-stub
synthesis is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

GAME_ID = 401403910
YEAR = 2022


def _play(idp, drive_id, drive_no, pnum, pos, dfe, pos_s, def_s, period, mins, secs,
          down, dist, ytg, gained, ptype, text, scoring, epa, wp):
    return {
        "id_play": str(idp),
        "game_id": GAME_ID,
        "year": YEAR,
        "week": 1,
        "home": "Ohio State",
        "away": "Notre Dame",
        "drive_id": drive_id,
        "drive_number": drive_no,
        "play_number": pnum,
        "pos_team": pos,
        "def_pos_team": dfe,
        "pos_team_score": pos_s,
        "def_pos_team_score": def_s,
        "period": period,
        "clock.minutes": mins,
        "clock.seconds": secs,
        "down": down,
        "distance": dist,
        "yards_to_goal": ytg,
        "yards_gained": gained,
        "play_type": ptype,
        "play_text": text,
        "scoring": scoring,
        "EPA": epa,
        "wp_before": wp,
        "wpa": round((wp - 0.5) * 0.1, 4),  # small synthetic WPA per play
        "rusher_player_name": ("Rush" in ptype) and text.split(" run")[0] or None,
        "passer_player_name": ("Passing" in ptype) and text.split(" pass")[0] or None,
    }


ROWS = [
    _play(1, 1, 1, 5, "Ohio State", "Notre Dame", 7, 0, 1, 10, 0, 1, 3, 3, 3,
          "Rushing Touchdown", "TreVeyon Henderson run for 3 yds for a TD", True, 2.1, 0.52),
    _play(2, 2, 2, 4, "Notre Dame", "Ohio State", 3, 7, 2, 8, 12, 4, 5, 20, 0,
          "Field Goal Good", "Blake Grupe 37 yd field goal good", True, 0.8, 0.45),
    _play(3, 3, 3, 2, "Ohio State", "Notre Dame", 7, 3, 2, 3, 44, 2, 7, 45, 6,
          "Rush", "Miyan Williams run for 6 yds", False, 0.3, 0.60),
    _play(4, 3, 3, 6, "Ohio State", "Notre Dame", 14, 3, 2, 1, 5, 1, 10, 12, 12,
          "Passing Touchdown", "C.J. Stroud pass to Emeka Egbuka for 12 yds TD", True, 3.9, 0.66),
    _play(5, 4, 4, 3, "Notre Dame", "Ohio State", 10, 14, 3, 6, 20, 1, 10, 15, 15,
          "Passing Touchdown", "Drew Pyne pass for 15 yds for a TD", True, 3.5, 0.40),
    _play(6, 5, 5, 8, "Ohio State", "Notre Dame", 21, 10, 4, 2, 30, 1, 2, 2, 2,
          "Rushing Touchdown", "Miyan Williams run for 2 yds for a TD", True, 2.6, 0.78),
]


def main() -> None:
    out = Path(__file__).with_name(f"play_by_play_{YEAR}.parquet")
    pq.write_table(pa.Table.from_pylist(ROWS), out)
    print(f"wrote {out} ({len(ROWS)} rows)")


if __name__ == "__main__":
    main()
