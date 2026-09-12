from __future__ import annotations

from dataclasses import dataclass

from storage.database import SLPDatabase


@dataclass(frozen=True)
class SourceAssessment:
    status: str
    reason: str
    current_count: int
    previous_count: int | None
    severity: str | None = None

    @property
    def publish_allowed(self) -> bool:
        return self.status in {"ok", "warning"}


class SourceHealthAgent:
    """Detect parser/source anomalies before they can replace good data."""

    def __init__(self, database: SLPDatabase):
        self.database = database

    def assess(
        self,
        *,
        source: str,
        scope_date: str,
        current_count: int,
        error: Exception | None = None,
    ) -> SourceAssessment:
        previous = self.database.latest_successful_count(source, scope_date)

        if error is not None:
            return SourceAssessment(
                status="error",
                reason=f"source_error: {type(error).__name__}: {error}",
                current_count=current_count,
                previous_count=previous,
                severity="critical",
            )

        if previous is None:
            if current_count == 0:
                return SourceAssessment(
                    status="warning",
                    reason="first_observation_is_empty",
                    current_count=0,
                    previous_count=None,
                    severity="warning",
                )
            return SourceAssessment(
                status="ok",
                reason="baseline_created",
                current_count=current_count,
                previous_count=None,
            )

        if previous >= 3 and current_count == 0:
            return SourceAssessment(
                status="blocked",
                reason="event_count_collapsed_to_zero",
                current_count=current_count,
                previous_count=previous,
                severity="critical",
            )

        ratio = current_count / previous if previous else 1.0

        if previous >= 5 and ratio <= 0.20:
            return SourceAssessment(
                status="blocked",
                reason="event_count_drop_over_80_percent",
                current_count=current_count,
                previous_count=previous,
                severity="critical",
            )

        if previous >= 10 and ratio <= 0.50:
            return SourceAssessment(
                status="warning",
                reason="event_count_drop_over_50_percent",
                current_count=current_count,
                previous_count=previous,
                severity="warning",
            )

        if previous >= 5 and current_count >= previous * 3:
            return SourceAssessment(
                status="warning",
                reason="event_count_increased_over_3x",
                current_count=current_count,
                previous_count=previous,
                severity="warning",
            )

        return SourceAssessment(
            status="ok",
            reason="source_output_looks_normal",
            current_count=current_count,
            previous_count=previous,
        )
