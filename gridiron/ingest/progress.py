"""A tiny, dependency-free progress bar for the ingest CLI.

:class:`ProgressReporter` is the callback handed to
:func:`gridiron.ingest.pipeline.ingest_season`. On a TTY it draws a single live
bar (redrawn in place) whose fill spans *all* requested seasons, annotated with
the current season, stage, and running counts. When stdout is not a TTY --- a
cron ``gridiron-refresh``, a redirected log --- it stays silent so the caller's
plain per-season report lines are the whole story.
"""

from __future__ import annotations

import sys
from typing import TextIO

from gridiron.ingest.pipeline import StageProgress


class ProgressReporter:
    """Render :class:`StageProgress` events as a live, in-place progress bar.

    The bar's denominator is ``len(years) * stages_per_season``; because every
    season runs the same enabled stages, the fraction is exact and monotonic
    across a multi-year backfill.
    """

    def __init__(
        self,
        years: list[int],
        *,
        stream: TextIO | None = None,
        enabled: bool | None = None,
        width: int = 26,
    ) -> None:
        self.years = list(years)
        self.stream = stream if stream is not None else sys.stdout
        self.width = width
        # Auto-detect: only animate when attached to a terminal.
        self.enabled = self.stream.isatty() if enabled is None else enabled
        self._dirty = False  # a live bar is currently occupying the line

    def __call__(self, ev: StageProgress) -> None:
        if not self.enabled:
            return
        completed = self.years.index(ev.year) * ev.total + ev.completed
        total = len(self.years) * ev.total
        marker = "→" if ev.running else "✓"
        self._draw(completed, total, f"[{ev.year}] {marker} {ev.stage}", _counts(ev.report))

    def finish_year(self, line: str) -> None:
        """Clear the live bar (if any) and print ``line`` as permanent history.

        The CLI calls this once per season with the final report so completed
        seasons scroll up as normal output while the bar keeps animating below.
        """
        if self.enabled and self._dirty:
            self.stream.write("\r\033[K")
            self._dirty = False
        self.stream.write(line + "\n")
        self.stream.flush()

    def _draw(self, completed: int, total: int, label: str, counts: str) -> None:
        frac = completed / total if total else 1.0
        filled = round(self.width * frac)
        bar = "█" * filled + "░" * (self.width - filled)
        pct = f"{frac * 100:3.0f}%"
        line = f"\r\033[K  {bar} {pct}  {label}"
        if counts:
            line += f"  ·  {counts}"
        self.stream.write(line)
        self.stream.flush()
        self._dirty = True


def _counts(report: object) -> str:
    counts = getattr(report, "counts", {})
    return "  ".join(f"{k}={_short(v)}" for k, v in sorted(counts.items()))


def _short(n: int) -> str:
    """Compact large counts (41200 -> ``41.2k``) so the status line stays short."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.0f}k"
    return str(n)
