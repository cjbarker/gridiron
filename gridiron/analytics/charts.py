"""Plotly figure builders.

Each function runs an analytics query (from ``queries.py``) and returns a Plotly
figure serialized to a JSON string via :func:`fig_to_json`. Templates embed that
JSON and render it client-side with ``Plotly.newPlot`` (see ``base.html``). Figures
share a dark theme that matches the site chrome.

Keeping charts here (not in templates) means the same figure can back a page, an
API endpoint, or a static export without duplicating layout logic.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy.orm import Session

from gridiron.analytics import queries as q
from gridiron.analytics.filters import PlayFilter

# Site-matching palette (see web/templates/base.html).
_ACCENT = "#4ea1ff"
_INK = "#e8edf6"
_MUTED = "#93a1b8"
_GRID = "#243149"
_SEQ = ["#4ea1ff", "#38d39f", "#f5a623", "#e0568b", "#9b8cff", "#5ad1e6"]


def fig_to_json(fig: go.Figure) -> str:
    """Serialize a figure to JSON for embedding in a template/endpoint."""
    return pio.to_json(fig)


def _theme(fig: go.Figure, title: str | None = None) -> go.Figure:
    fig.update_layout(
        title=title,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_INK, family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif"),
        margin=dict(l=48, r=16, t=48 if title else 16, b=40),
        colorway=_SEQ,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis=dict(gridcolor=_GRID, zerolinecolor=_GRID),
        yaxis=dict(gridcolor=_GRID, zerolinecolor=_GRID),
        height=320,
    )
    return fig


def _empty(title: str, message: str = "No data") -> str:
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, font=dict(color=_MUTED, size=14))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig_to_json(_theme(fig, title))


def _leaderboard_bar(
    rows: list[dict],
    *,
    value_key: str,
    label_key: str,
    title: str,
    x_title: str,
    color: str,
    hover: str,
    empty_title: str | None = None,
    value_fn=None,
    x_range: list[float] | None = None,
) -> str:
    """A themed horizontal-bar leaderboard: rank rows by ``value_key`` ascending
    (largest on top) and label by ``label_key``. ``value_fn`` transforms the bar
    value for display only (sorting still uses the raw key)."""
    if not rows:
        return _empty(empty_title or title)
    rows = sorted(rows, key=lambda r: r[value_key])
    xs = [value_fn(r[value_key]) if value_fn else r[value_key] for r in rows]
    fig = go.Figure(
        go.Bar(
            x=xs,
            y=[r[label_key] for r in rows],
            orientation="h",
            marker_color=color,
            hovertemplate=hover,
        )
    )
    fig.update_xaxes(title_text=x_title, **({"range": x_range} if x_range else {}))
    return fig_to_json(_theme(fig, title))


# --- Season / team scoring charts ----------------------------------------

def scoring_field_position_fig(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    flt: PlayFilter | None = None,
) -> str:
    rows = q.scoring_by_field_position(session, season, team, flt=flt)
    if not rows:
        return _empty("Points by field position")
    fig = go.Figure(
        go.Bar(
            x=[r["bucket"] for r in rows],
            y=[r["points"] for r in rows],
            marker_color=_ACCENT,
            hovertemplate="%{x}<br>%{y} pts<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="Points")
    return fig_to_json(_theme(fig, "Points by field position (yards to goal)"))


def scoring_type_fig(
    session: Session, season: int | None = None, team: str | None = None
) -> str:
    rows = q.scoring_type_breakdown(session, season, team)
    if not rows:
        return _empty("Scoring by type")
    fig = go.Figure(
        go.Bar(
            x=[r["score_type"] for r in rows],
            y=[r["points"] for r in rows],
            marker_color=_SEQ[1],
            hovertemplate="%{x}<br>%{y} pts<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="Points")
    return fig_to_json(_theme(fig, "Points by scoring type"))


def fg_success_fig(
    session: Session, season: int | None = None, team: str | None = None
) -> str:
    rows = q.field_goal_success_by_distance(session, season, team)
    if not rows:
        return _empty("Field-goal success")
    fig = go.Figure(
        go.Bar(
            x=[r["bucket"] for r in rows],
            y=[r["pct"] for r in rows],
            marker_color=_SEQ[2],
            customdata=[[r["made"], r["attempts"]] for r in rows],
            hovertemplate="%{x}<br>%{y}%% (%{customdata[0]}/%{customdata[1]})<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="Make %", range=[0, 100])
    return fig_to_json(_theme(fig, "Field-goal success by distance"))


def play_type_mix_fig(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    limit: int = 12,
    flt: PlayFilter | None = None,
) -> str:
    rows = q.play_type_mix(session, season, team, limit=limit, flt=flt)
    return _leaderboard_bar(
        rows, value_key="plays", label_key="play_type", title="Play-type mix",
        x_title="Plays", color=_SEQ[4], hover="%{y}<br>%{x} plays<extra></extra>",
    )


# --- Team-specific charts -------------------------------------------------

def team_points_trend_fig(session: Session, team: str, season: int) -> str:
    log = q.team_game_log(session, team, season)
    played = [g for g in log if g["points_for"] is not None]
    if not played:
        return _empty("Scoring trend")
    weeks = [g["week"] for g in played]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=weeks,
            y=[g["points_for"] for g in played],
            mode="lines+markers",
            name="Points for",
            line=dict(color=_ACCENT, width=3),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=weeks,
            y=[g["points_against"] for g in played],
            mode="lines+markers",
            name="Points against",
            line=dict(color=_SEQ[3], width=3),
        )
    )
    fig.update_xaxes(title_text="Week")
    fig.update_yaxes(title_text="Points")
    return fig_to_json(_theme(fig, f"{team} scoring trend"))


def ppa_by_down_fig(
    session: Session,
    season: int | None = None,
    team: str | None = None,
    flt: PlayFilter | None = None,
) -> str:
    rows = q.ppa_by_down(session, season, team, flt=flt)
    if not rows:
        return _empty("PPA by down")
    fig = go.Figure(
        go.Bar(
            x=[f"Down {r['down']}" for r in rows],
            y=[r["avg_ppa"] for r in rows],
            marker_color=_SEQ[5],
            hovertemplate="%{x}<br>%{y} avg PPA<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="Avg PPA/EPA")
    return fig_to_json(_theme(fig, "Efficiency (PPA/EPA) by down"))


def success_explosive_fig(
    session: Session, season: int, team: str, flt: PlayFilter | None = None
) -> str:
    sr = q.success_rate(session, season, team, flt=flt)
    ex = q.explosiveness(session, season, team, flt=flt)
    success = sr[0]["success_rate"] if sr else 0
    explosive = ex[0]["explosive_rate"] if ex else 0
    fig = go.Figure(
        go.Bar(
            x=["Success rate", "Explosive rate"],
            y=[round(success * 100, 1), round(explosive * 100, 1)],
            marker_color=[_ACCENT, _SEQ[2]],
            hovertemplate="%{x}<br>%{y}%%<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="%", range=[0, 100])
    return fig_to_json(_theme(fig, "Offensive efficiency"))


def compare_fig(a_label: str, b_label: str, a: dict, b: dict) -> str:
    """Grouped bar comparing two teams' headline metrics (from `team_compare`)."""
    cats = ["Points / game", "Success %", "Explosive %"]

    def vals(side: dict) -> list[float]:
        return [
            side["summary"].get("ppg") or 0,
            round((side.get("success_rate") or 0) * 100, 1),
            round((side.get("explosive_rate") or 0) * 100, 1),
        ]

    fig = go.Figure()
    fig.add_trace(go.Bar(name=a_label, x=cats, y=vals(a), marker_color=_ACCENT))
    fig.add_trace(go.Bar(name=b_label, x=cats, y=vals(b), marker_color=_SEQ[2]))
    fig.update_layout(barmode="group")
    return fig_to_json(_theme(fig, f"{a_label} vs {b_label}"))


def player_compare_fig(a_label: str, b_label: str, stats: list[dict]) -> str:
    """Grouped bar comparing two players' shared numeric stats (from `player_compare`)."""
    shared = [r for r in stats if r["a"] is not None and r["b"] is not None]
    if not shared:
        return _empty(f"{a_label} vs {b_label}", "No shared numeric stats")
    cats = [f"{r['category']} {r['stat_type']}" for r in shared]
    fig = go.Figure()
    fig.add_trace(go.Bar(name=a_label, x=cats, y=[r["a"] for r in shared], marker_color=_ACCENT))
    fig.add_trace(go.Bar(name=b_label, x=cats, y=[r["b"] for r in shared], marker_color=_SEQ[2]))
    fig.update_layout(barmode="group")
    return fig_to_json(_theme(fig, f"{a_label} vs {b_label}"))


def drive_outcomes_fig(session: Session, season: int, team: str | None = None) -> str:
    """Bar of how a team's (or a season's) drives end."""
    rows = q.drive_outcomes(session, season, team)
    rows = [r for r in rows if r["drive_result"]]
    if not rows:
        return _empty("Drive outcomes")
    fig = go.Figure(
        go.Bar(
            x=[r["drive_result"] for r in rows],
            y=[r["drives"] for r in rows],
            marker_color=_SEQ[1],
            hovertemplate="%{x}<br>%{y} drives<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="Drives")
    return fig_to_json(_theme(fig, "Drive outcomes"))


def team_trends_fig(session: Session, team: str, history: list[dict] | None = None) -> str:
    """Season-over-season line of a team's scoring offense and defense.

    Pass a precomputed ``history`` (from ``team_season_history``) to avoid recomputing
    the per-season fan-out when the caller already has it.
    """
    hist = history if history is not None else q.team_season_history(session, team)
    if not hist:
        return _empty(f"{team} season trends")
    seasons = [h["season"] for h in hist]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=seasons, y=[h["ppg"] for h in hist], mode="lines+markers",
            name="Points / game", line=dict(color=_ACCENT, width=3),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=seasons, y=[h["papg"] for h in hist], mode="lines+markers",
            name="Allowed / game", line=dict(color=_SEQ[3], width=3),
        )
    )
    fig.update_xaxes(title_text="Season", dtick=1)
    fig.update_yaxes(title_text="Points / game")
    return fig_to_json(_theme(fig, f"{team} — season-over-season"))


def win_probability_fig(session: Session, game_id: int, home: str, away: str) -> str:
    """Game-flow win-probability line for the home team (0-100%)."""
    rows = q.game_win_probability(session, game_id)
    if not rows:
        return _empty("Win probability", "No win-probability data")
    fig = go.Figure(
        go.Scatter(
            x=[r["index"] for r in rows],
            y=[round(r["home_wp"] * 100, 1) for r in rows],
            mode="lines",
            line=dict(color=_ACCENT, width=2),
            hovertemplate="%{y}%% " + home + "<extra></extra>",
            fill="tozeroy",
            fillcolor="rgba(78,161,255,0.12)",
        )
    )
    fig.add_hline(y=50, line_dash="dot", line_color=_MUTED)
    fig.update_xaxes(title_text="Play")
    fig.update_yaxes(title_text=f"{home} win %", range=[0, 100])
    return fig_to_json(_theme(fig, f"Win probability — {away} @ {home}"))


def wp_leaders_fig(session: Session, season: int, limit: int = 15) -> str:
    """Bar of teams by average in-game win probability."""
    rows = q.wp_leaders(session, season, min_plays=1, limit=limit)
    return _leaderboard_bar(
        rows, value_key="avg_wp", label_key="team",
        title="Average in-game win probability", empty_title="Win-probability leaders",
        x_title="Avg win %", color=_ACCENT, hover="%{y}<br>%{x}%% avg WP<extra></extra>",
        value_fn=lambda v: round(v * 100, 1), x_range=[0, 100],
    )


def player_wpa_leaders_fig(session: Session, season: int, limit: int = 15) -> str:
    """Bar of players by total win probability added."""
    rows = q.player_wpa_leaders(session, season, min_plays=1, limit=limit)
    return _leaderboard_bar(
        rows, value_key="total_wpa", label_key="player",
        title="Win probability added (WPA) leaders",
        empty_title="Win probability added (WPA)", x_title="Total WPA",
        color=_SEQ[1], hover="%{y}<br>%{x} total WPA<extra></extra>",
    )


def winningest_fig(session: Session, limit: int = 15) -> str:
    """Bar of career wins for the winningest coaches (across ingested seasons)."""
    rows = q.winningest_coaches(session, min_games=1, limit=limit)
    return _leaderboard_bar(
        rows, value_key="wins", label_key="coach", title="Winningest coaches",
        x_title="Career wins", color=_SEQ[2], hover="%{y}<br>%{x} wins<extra></extra>",
    )


def clv_leaders_fig(session: Session, season: int, limit: int = 15) -> str:
    """Bar of teams by average closing line value (market moved toward them)."""
    rows = q.clv_leaders(session, season, limit=limit)
    return _leaderboard_bar(
        rows, value_key="avg_clv_points", label_key="team",
        title="Closing line value (market movement toward team)",
        empty_title="Closing line value", x_title="Avg CLV (points)",
        color=_SEQ[5], hover="%{y}<br>%{x} avg CLV pts<extra></extra>",
    )
