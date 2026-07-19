"""Walk-forward back-testing for the prediction engine.

For a season we replay it **week by week**: to predict week *W* we build ratings
(and, for the ML engine, train the logistic model) from **weeks < W only**, so a
prediction never sees its own game — the validation is leakage-safe by construction.

We report winner **accuracy**, margin **MAE/RMSE**, **Brier**/**log-loss**, and a
**calibration** table, then benchmark against the closing betting line (the market's
own accuracy/MAE and our **against-the-spread** record). The market is the yardstick,
never a model input.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from sqlalchemy.orm import Session

from gridiron.analytics import ratings as rt
from gridiron.analytics.predict import (
    MLUnavailable,
    logistic_prediction,
    ratings_prediction,
    train_logistic,
)
from gridiron.analytics.queries import _first_lines_by_game

MIN_PRIOR_GAMES = 15  # skip early weeks until the ratings have some history


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _calibration(rows: list[tuple[float, int]], bins: int = 10) -> list[dict[str, Any]]:
    """Reliability table: mean predicted vs actual home-win rate per probability bin."""
    out: list[dict[str, Any]] = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        bucket = [(p, o) for p, o in rows if (lo <= p < hi or (i == bins - 1 and p == 1.0))]
        if not bucket:
            continue
        out.append(
            {
                "bin_lo": round(lo, 2),
                "bin_hi": round(hi, 2),
                "n": len(bucket),
                "predicted": round(statistics.fmean([p for p, _ in bucket]), 3),
                "actual": round(statistics.fmean([o for _, o in bucket]), 3),
            }
        )
    return out


def backtest_season(
    session: Session, season: int, *, model: str = "ratings", min_prior_games: int = MIN_PRIOR_GAMES
) -> dict[str, Any]:
    """Replay a season walk-forward and score the chosen engine (see module docstring)."""
    games = rt.load_season_games(session, season)
    weeks = sorted({g.week for g in games if g.week is not None})
    lines = _first_lines_by_game(session, season)

    n = 0
    correct = 0
    abs_errs: list[float] = []
    sq_errs: list[float] = []
    brier: list[float] = []
    logloss: list[float] = []
    calib_rows: list[tuple[float, int]] = []

    mkt_n = 0
    mkt_correct = 0
    mkt_abs: list[float] = []
    ats_w = ats_l = ats_p = 0

    used_model = model
    for w in weeks:
        prior = rt.team_ratings(session, season, through_week=w)
        if sum(t.games for t in prior.teams.values()) < min_prior_games:
            continue
        lr = None
        if used_model == "logistic":
            try:
                lr = train_logistic(session, [season], through_week=w)
            except MLUnavailable:
                used_model = "ratings"  # no ML available → score ratings instead

        for g in games:
            if g.week != w:
                continue
            rh, ra = prior.get(g.home), prior.get(g.away)
            if rh is None or ra is None:
                continue
            home = None if g.neutral else "a"  # side A = home team
            if used_model == "logistic" and lr is not None:
                pred = logistic_prediction(
                    session, rh, prior, ra, prior, g.home, g.away,
                    neutral=g.neutral, home=home, era_adjusted=False, model=lr,
                )
            else:
                pred = ratings_prediction(
                    rh, prior, ra, prior, g.home, g.away,
                    neutral=g.neutral, home=home, era_adjusted=False,
                )

            actual = g.home_points - g.away_points
            abs_errs.append(abs(pred.proj_margin - actual))
            sq_errs.append((pred.proj_margin - actual) ** 2)
            if actual != 0:  # winner metrics skip the rare tie
                outcome = 1 if actual > 0 else 0
                p = min(max(pred.win_prob_a, 1e-6), 1.0 - 1e-6)
                n += 1
                correct += int(_sign(pred.proj_margin) == _sign(actual))
                brier.append((p - outcome) ** 2)
                logloss.append(-(outcome * math.log(p) + (1 - outcome) * math.log(1 - p)))
                calib_rows.append((pred.win_prob_a, outcome))

            bl = lines.get(g.game_id)
            if bl is not None and bl.spread is not None and actual != 0:
                mkt_margin = -bl.spread  # CFBD spread is home-negative → home expected margin
                mkt_n += 1
                mkt_correct += int(_sign(mkt_margin) == _sign(actual))
                mkt_abs.append(abs(mkt_margin - actual))
                edge = pred.proj_margin - mkt_margin  # our lean vs the market
                cover = actual - mkt_margin
                if edge == 0 or cover == 0:
                    ats_p += 1
                elif (edge > 0) == (cover > 0):
                    ats_w += 1
                else:
                    ats_l += 1

    market = None
    if mkt_n:
        market = {
            "games": mkt_n,
            "accuracy": round(mkt_correct / mkt_n, 3),
            "mae": round(statistics.fmean(mkt_abs), 2),
            "ats_wins": ats_w,
            "ats_losses": ats_l,
            "ats_pushes": ats_p,
            "ats_rate": round(ats_w / (ats_w + ats_l), 3) if (ats_w + ats_l) else None,
        }

    return {
        "season": season,
        "model": used_model,
        "requested_model": model,
        "games": n,
        "accuracy": round(correct / n, 3) if n else None,
        "mae": round(statistics.fmean(abs_errs), 2) if abs_errs else None,
        "rmse": round(math.sqrt(statistics.fmean(sq_errs)), 2) if sq_errs else None,
        "brier": round(statistics.fmean(brier), 4) if brier else None,
        "log_loss": round(statistics.fmean(logloss), 4) if logloss else None,
        "residual_margin_std": round(statistics.pstdev(
            [math.sqrt(s) for s in sq_errs]), 2) if len(sq_errs) > 1 else None,
        "calibration": _calibration(calib_rows),
        "market": market,
    }
