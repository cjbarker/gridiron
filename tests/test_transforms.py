"""Unit tests for the pure transform functions."""

from __future__ import annotations

import json
from pathlib import Path

from gridiron.ingest import transforms as tf

FIXTURES = Path(__file__).parent / "fixtures" / "season2023"


def _load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_points_for_play_type():
    assert tf.points_for_play_type("Passing Touchdown") == 6
    assert tf.points_for_play_type("Rushing Touchdown") == 6
    assert tf.points_for_play_type("Field Goal Good") == 3
    assert tf.points_for_play_type("Safety") == 2
    assert tf.points_for_play_type("Two Point Rush") == 2
    assert tf.points_for_play_type("Extra Point Good") == 1
    assert tf.points_for_play_type("Rush") == 0
    assert tf.points_for_play_type(None) == 0


def test_to_game_camelcase():
    g = tf.to_game(_load("games")[0])
    assert g.id == 401520281
    assert g.season == 2023
    assert g.home_team == "Georgia" and g.home_points == 27
    assert g.away_team == "Alabama" and g.away_points == 24
    assert g.neutral_site is True
    assert g.venue_id == 3657


def test_to_play_derives_points_and_field_position():
    plays = {p["playType"]: tf.to_play(p) for p in _load("plays")}
    td = plays["Passing Touchdown"]
    assert td.points_scored == 6 and td.scoring is True
    assert td.yards_to_goal == 15 and td.yard_line == 85
    assert plays["Field Goal Good"].points_scored == 3
    assert plays["Extra Point Good"].points_scored == 1
    # Non-scoring plays carry 0 points even when a play type is present.
    assert plays["Rush"].points_scored == 0 and plays["Rush"].scoring is False
    assert plays["Field Goal Missed"].points_scored == 0


def test_flatten_player_game_stats():
    rows = tf.flatten_player_game_stats(401520281, 2023, _load("player_game_stats")[0])
    beck_td = [r for r in rows if r.player == "Carson Beck" and r.stat_type == "TD"]
    assert beck_td and beck_td[0].stat == "1" and beck_td[0].category == "passing"
    assert all(r.game_id == 401520281 and r.season == 2023 for r in rows)


def test_flatten_rankings():
    rows = tf.flatten_rankings(2023, _load("rankings"))
    top = [r for r in rows if r.rank == 1]
    assert top and top[0].team == "Georgia" and top[0].poll == "AP Top 25"
