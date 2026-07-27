"""AnalysisEvaluator: the Version 2 scoring engine's orchestration layer.

This is the single integration point a later phase wires into the live
or replay controllers; nothing else in the analysis package needs to
change to support that. The integration itself is intentionally not
done in this phase - see the module docstring in rules.py and the
project notes on the LC29HEA reference receiver.

Usage:

    evaluator = AnalysisEvaluator(
        config.analysis,
        expected_latitude_deg=config.site.expected.latitude_deg,
        expected_longitude_deg=config.site.expected.longitude_deg,
        reference_receiver_ids={c.id for c in config.channels if c.is_reference},
    )
    evaluator.update("neo6m_1", ReceiverSample(...))
    ...
    results = evaluator.evaluate_all()  # dict[receiver_id, AnalysisResult]

update() is called once per new reading (however often the caller
decides "there is a new sample"); evaluate_all() can be called as often
as needed (e.g. every dashboard refresh) without side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

from gnss_monitor.analysis import rules
from gnss_monitor.analysis.rules import RuleOutcome
from gnss_monitor.analysis.score import HealthState, classify, total_score
from gnss_monitor.analysis.state import ReceiverHistory, ReceiverSample
from gnss_monitor.config.schema import AnalysisConfig


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """One receiver's scoring outcome for one evaluate_all() pass."""

    receiver_id: str
    score: float
    state: HealthState
    triggered_rules: tuple[RuleOutcome, ...]


class AnalysisEvaluator:
    """Scores every receiver it has seen a sample for against AnalysisConfig."""

    def __init__(
        self,
        config: AnalysisConfig,
        expected_latitude_deg: float,
        expected_longitude_deg: float,
        reference_receiver_ids: FrozenSet[str] = frozenset(),
    ) -> None:
        self._config = config
        self._expected_latitude_deg = expected_latitude_deg
        self._expected_longitude_deg = expected_longitude_deg
        self._reference_receiver_ids = reference_receiver_ids
        self._history: dict[str, ReceiverHistory] = {}

    def update(self, receiver_id: str, sample: ReceiverSample) -> None:
        """Record `sample` as the latest reading for `receiver_id`."""
        self._history.setdefault(receiver_id, ReceiverHistory()).push(sample)

    def evaluate_all(self) -> dict[str, AnalysisResult]:
        """Evaluate every receiver update() has been called for at least once."""
        positions = self._fixed_positions_by_receiver()
        return {
            receiver_id: self._evaluate_one(receiver_id, history, positions)
            for receiver_id, history in self._history.items()
        }

    # -- internals ------------------------------------------------------

    def _fixed_positions_by_receiver(self) -> dict[str, tuple[float, float]]:
        """Current positions of non-reference receivers with a valid fix.

        Reference (multi-constellation) receivers are excluded: they
        aren't peers of the dedicated single-constellation receivers for
        the purpose of "do the constellation-dedicated receivers agree
        with each other", so they neither contribute to, nor are scored
        against, the disagreement centroid.
        """
        positions: dict[str, tuple[float, float]] = {}
        for receiver_id, history in self._history.items():
            if receiver_id in self._reference_receiver_ids:
                continue
            sample = history.current
            if (
                sample is not None
                and sample.has_fix
                and sample.latitude_deg is not None
                and sample.longitude_deg is not None
            ):
                positions[receiver_id] = (
                    sample.latitude_deg,
                    sample.longitude_deg,
                )
        return positions

    def _evaluate_one(
        self,
        receiver_id: str,
        history: ReceiverHistory,
        positions: dict[str, tuple[float, float]],
    ) -> AnalysisResult:
        sample = history.current or ReceiverSample()
        prior = history.previous

        if receiver_id in self._reference_receiver_ids:
            disagreement_outcome = RuleOutcome(
                "disagreement",
                False,
                0.0,
                "reference receiver; not scored for disagreement",
            )
        else:
            disagreement_outcome = rules.disagreement(
                receiver_id, positions, self._config.disagreement
            )

        if self._config.signal is None:
            signal_outcome = RuleOutcome(
                "signal_anomaly", False, 0.0, "signal scoring not configured"
            )
        else:
            signal_outcome = rules.signal_anomaly(sample, self._config.signal)

        outcomes = (
            rules.no_fix(sample, self._config.no_fix),
            rules.position_offset(
                sample,
                self._config.position,
                self._expected_latitude_deg,
                self._expected_longitude_deg,
            ),
            rules.sudden_speed(sample, self._config.speed),
            rules.satellite_anomaly(sample, prior, self._config.satellites),
            rules.hdop_anomaly(sample, self._config.hdop),
            rules.time_discontinuity(sample, prior, self._config.time),
            signal_outcome,
            disagreement_outcome,
        )
        score = total_score(outcomes)
        return AnalysisResult(
            receiver_id=receiver_id,
            score=score,
            state=classify(score, self._config.overall),
            triggered_rules=tuple(o for o in outcomes if o.triggered),
        )
