"""Matchup prediction & Monte-Carlo simulation.

Turns team power ratings (:mod:`gridiron.analytics.ratings`) into a projected
score, win probability, and a **Monte-Carlo** outcome distribution for any two
``(team, season)`` sides — including **cross-era** matchups via era normalization.

Two engines share the :class:`Prediction` shape:

* **Ratings** — the transparent SRS/points model below (stdlib only, always on).
* **Logistic** — an opt-in ``scikit-learn`` win model trained on past games; it
  reuses the ratings-based features and the same simulation path. Import is lazy,
  so the core keeps working when the ``ml`` extra isn't installed.

The betting market is deliberately **not** an input to either model — it is kept as
the honest yardstick in :mod:`gridiron.analytics.backtest`.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from gridiron.analytics import ratings as rt

# Defaults tuned to typical CFB scoring; the backtest reports the residual spread
# that justifies them. Callers may override.
WP_SCALE = 13.5  # logistic scale: a ~13.5-pt edge ≈ 73% win probability
MARGIN_SIGMA = 15.0  # game-to-game margin noise for Monte-Carlo draws
TOTAL_SIGMA = 10.0  # combined-points noise
DEFAULT_SIMS = 10000


class MLUnavailable(RuntimeError):
    """Raised when the logistic model is requested but scikit-learn isn't installed."""


@dataclass
class Prediction:
    model: str
    a: str
    season_a: int
    b: str
    season_b: int
    neutral: bool
    home: str | None  # "a" | "b" | None
    era_adjusted: bool
    proj_margin: float  # A's expected margin (positive → A favored)
    win_prob_a: float
    score_a: float
    score_b: float
    hfa: float
    components: dict = field(default_factory=dict)


@dataclass
class Simulation:
    sims: int
    win_prob_a: float
    upset_prob: float
    median_a: float
    median_b: float
    margin_p5: float
    margin_p95: float
    margins: list[float] = field(default_factory=list)


def win_prob_from_margin(margin: float, scale: float = WP_SCALE) -> float:
    return 1.0 / (1.0 + math.exp(-margin / scale))


def margin_from_win_prob(p: float, scale: float = WP_SCALE) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return scale * math.log(p / (1.0 - p))


def _era_scaled(
    r: rt.TeamRating, sr: rt.SeasonRatings, ref_std: float, era_adjusted: bool
) -> tuple[float, float, float]:
    """Scale a team's raw points to a common era via its season's SRS spread."""
    if not era_adjusted or sr.srs_std <= 0:
        return r.srs, r.adj_off, r.adj_def
    factor = ref_std / sr.srs_std
    return r.srs * factor, r.adj_off * factor, r.adj_def * factor


def _project(
    ra: rt.TeamRating,
    sra: rt.SeasonRatings,
    rb: rt.TeamRating,
    srb: rt.SeasonRatings,
    *,
    neutral: bool,
    home: str | None,
    era_adjusted: bool,
) -> tuple[float, float, float, float]:
    """Projected (A-margin, A-score, B-score, hfa) from two teams' ratings."""
    ref_std = statistics.fmean([sra.srs_std, srb.srs_std])
    srs_a, off_a, def_a = _era_scaled(ra, sra, ref_std, era_adjusted)
    srs_b, off_b, def_b = _era_scaled(rb, srb, ref_std, era_adjusted)

    la = statistics.fmean([sra.league_avg_points, srb.league_avg_points])
    total = max((la + off_a - def_b) + (la + off_b - def_a), 0.0)

    hfa = 0.0
    if not neutral and home in ("a", "b"):
        hfa = sra.hfa if home == "a" else srb.hfa
    margin = (srs_a - srs_b) + (hfa if home == "a" else -hfa if home == "b" else 0.0)

    score_a = max((total + margin) / 2.0, 0.0)
    score_b = max((total - margin) / 2.0, 0.0)
    return margin, score_a, score_b, hfa


def simulate(
    proj_margin: float,
    proj_total: float,
    *,
    sims: int = DEFAULT_SIMS,
    seed: int = 0,
    margin_sigma: float = MARGIN_SIGMA,
    total_sigma: float = TOTAL_SIGMA,
) -> Simulation:
    """Monte-Carlo the game: draw margin & total, tally wins and a score band."""
    rng = random.Random(seed)
    margins: list[float] = []
    a_scores: list[float] = []
    b_scores: list[float] = []
    a_wins = 0.0
    for _ in range(sims):
        m = rng.gauss(proj_margin, margin_sigma)
        t = max(rng.gauss(proj_total, total_sigma), 0.0)
        margins.append(m)
        a_scores.append(max((t + m) / 2.0, 0.0))
        b_scores.append(max((t - m) / 2.0, 0.0))
        a_wins += 1.0 if m > 0 else 0.5 if m == 0 else 0.0
    win_prob_a = a_wins / sims if sims else 0.5
    ordered = sorted(margins)

    def pct(p: float) -> float:
        if not ordered:
            return 0.0
        return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))], 1)

    return Simulation(
        sims=sims,
        win_prob_a=round(win_prob_a, 4),
        upset_prob=round(min(win_prob_a, 1.0 - win_prob_a), 4),
        median_a=round(statistics.median(a_scores), 1) if a_scores else 0.0,
        median_b=round(statistics.median(b_scores), 1) if b_scores else 0.0,
        margin_p5=pct(0.05),
        margin_p95=pct(0.95),
        margins=margins,
    )


# --- Ratings engine -------------------------------------------------------


def ratings_prediction(
    ra: rt.TeamRating,
    sra: rt.SeasonRatings,
    rb: rt.TeamRating,
    srb: rt.SeasonRatings,
    a: str,
    b: str,
    *,
    neutral: bool,
    home: str | None,
    era_adjusted: bool,
) -> Prediction:
    margin, score_a, score_b, hfa = _project(
        ra, sra, rb, srb, neutral=neutral, home=home, era_adjusted=era_adjusted
    )
    return Prediction(
        model="ratings",
        a=a,
        season_a=sra.season,
        b=b,
        season_b=srb.season,
        neutral=neutral,
        home=home,
        era_adjusted=era_adjusted,
        proj_margin=round(margin, 1),
        win_prob_a=round(win_prob_from_margin(margin), 4),
        score_a=round(score_a, 1),
        score_b=round(score_b, 1),
        hfa=round(hfa, 2),
        components={"a": asdict(ra), "b": asdict(rb)},
    )


# --- Logistic (ML) engine -------------------------------------------------

# Feature vector for a matchup, home minus away (neutral flag last). Trained on the
# same shape so cross-era diffs stay meaningful.
_FEATURES = ("srs", "adj_off", "adj_def", "ppg", "papg", "form", "sos")


def _feature_vector(rh: rt.TeamRating, ravg: rt.TeamRating, neutral: bool) -> list[float]:
    return [getattr(rh, f) - getattr(ravg, f) for f in _FEATURES] + [1.0 if neutral else 0.0]


def _training_matrix(
    session: Session, seasons: list[int], *, through_week: int | None = None
) -> tuple[list[list[float]], list[int]]:
    """Build (features, home-win labels) from completed games in the given seasons."""
    x: list[list[float]] = []
    y: list[int] = []
    for season in sorted(set(seasons)):
        sr = rt.team_ratings(session, season, through_week=through_week)
        for g in rt.load_season_games(session, season, through_week=through_week):
            rh, ra = sr.get(g.home), sr.get(g.away)
            if rh is None or ra is None or g.home_points == g.away_points:
                continue
            x.append(_feature_vector(rh, ra, g.neutral))
            y.append(1 if g.home_points > g.away_points else 0)
    return x, y


def train_logistic(session: Session, seasons: list[int], *, through_week: int | None = None):
    """Fit a logistic-regression home-win model. Raises :class:`MLUnavailable` if
    scikit-learn is absent or there aren't two label classes to learn from."""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise MLUnavailable("scikit-learn is not installed (add the 'ml' extra)") from exc

    x, y = _training_matrix(session, seasons, through_week=through_week)
    if len({*y}) < 2:
        raise MLUnavailable("not enough labeled games to train the logistic model")
    scaler = StandardScaler().fit(x)
    clf = LogisticRegression(max_iter=1000).fit(scaler.transform(x), y)
    return scaler, clf


def logistic_prediction(
    session: Session,
    ra: rt.TeamRating,
    sra: rt.SeasonRatings,
    rb: rt.TeamRating,
    srb: rt.SeasonRatings,
    a: str,
    b: str,
    *,
    neutral: bool,
    home: str | None,
    era_adjusted: bool,
    model=None,
) -> Prediction:
    """Predict with the logistic model; falls back to the ratings total for the score.

    ``home`` orients the model's home/away features; the margin is recovered from the
    predicted win probability so the Monte-Carlo path is shared with the ratings engine.
    """
    scaler, clf = model or train_logistic(session, [sra.season, srb.season])
    # The LR is oriented home-vs-away; map A/B onto that with the home flag.
    if home == "b":
        feats = _feature_vector(rb, ra, neutral)
        p_home = float(clf.predict_proba(scaler.transform([feats]))[0][1])
        win_prob_a = 1.0 - p_home
    else:  # A at home, or neutral (A treated as the nominal home row)
        feats = _feature_vector(ra, rb, neutral)
        win_prob_a = float(clf.predict_proba(scaler.transform([feats]))[0][1])

    margin = margin_from_win_prob(win_prob_a)
    _, sc_a, sc_b, hfa = _project(ra, sra, rb, srb, neutral=neutral, home=home, era_adjusted=era_adjusted)
    total = sc_a + sc_b
    return Prediction(
        model="logistic",
        a=a,
        season_a=sra.season,
        b=b,
        season_b=srb.season,
        neutral=neutral,
        home=home,
        era_adjusted=era_adjusted,
        proj_margin=round(margin, 1),
        win_prob_a=round(win_prob_a, 4),
        score_a=round(max((total + margin) / 2.0, 0.0), 1),
        score_b=round(max((total - margin) / 2.0, 0.0), 1),
        hfa=round(hfa, 2),
        components={"a": asdict(ra), "b": asdict(rb)},
    )


# --- Public entry point ---------------------------------------------------


def ml_available() -> bool:
    """True when the logistic model can be used (scikit-learn importable)."""
    try:
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def predict_matchup(
    session: Session,
    a: str,
    season_a: int,
    b: str,
    season_b: int,
    *,
    neutral: bool = False,
    home: str | None = "a",
    model: str = "ratings",
    era_adjusted: bool | None = None,
    sims: int = DEFAULT_SIMS,
    seed: int = 0,
) -> dict | None:
    """Predict a matchup and simulate it. Returns ``None`` if either side has no data.

    ``era_adjusted`` defaults to on for cross-season matchups (and is a no-op within
    a single season). ``model="logistic"`` falls back to ratings if the ML extra is
    unavailable, flagged via ``ml_fell_back`` in the result.
    """
    if era_adjusted is None:
        era_adjusted = season_a != season_b
    if neutral:
        home = None

    sra = rt.team_ratings(session, season_a)
    srb = rt.team_ratings(session, season_b)
    ra, rb = sra.get(a), srb.get(b)
    if ra is None or rb is None:
        return None

    ml_fell_back = False
    if model == "logistic":
        try:
            pred = logistic_prediction(
                session, ra, sra, rb, srb, a, b,
                neutral=neutral, home=home, era_adjusted=era_adjusted,
            )
        except MLUnavailable:
            ml_fell_back = True
            model = "ratings"
    if model != "logistic":
        pred = ratings_prediction(
            ra, sra, rb, srb, a, b, neutral=neutral, home=home, era_adjusted=era_adjusted
        )

    sim = simulate(pred.proj_margin, pred.score_a + pred.score_b, sims=sims, seed=seed)
    return {
        "prediction": asdict(pred),
        "simulation": {k: v for k, v in asdict(sim).items() if k != "margins"},
        "margins": sim.margins,
        "ml_fell_back": ml_fell_back,
        "ml_available": ml_available(),
    }
