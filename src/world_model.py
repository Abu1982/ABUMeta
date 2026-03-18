"""世界模型与因果推演模块。"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import re
from typing import Dict, Iterable, Sequence


OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
MIN_STABLE_SAMPLE_SIZE = 5
DEFAULT_OUTCOMES: Sequence[str] = (OUTCOME_SUCCESS, OUTCOME_FAILURE)


@dataclass(frozen=True)
class ActionCandidate:
    """待评估动作。"""

    action_type: str
    strategy_name: str
    amount: float
    volatility: float
    expected_profit: float = 0.0

    @property
    def action_key(self) -> str:
        return f"{self.action_type}:{self.strategy_name}"


@dataclass(frozen=True)
class HistorySample:
    """历史样本。"""

    action_type: str
    strategy_name: str
    outcome_type: str
    pnl: float
    timestamp: object | None = None

    @property
    def action_key(self) -> str:
        return f"{self.action_type}:{self.strategy_name}"


@dataclass(frozen=True)
class OutcomeDistribution:
    """动作结果分布。"""

    action_key: str
    probabilities: Dict[str, float]
    sample_size: int
    confidence: float
    intuition_unstable: bool


@dataclass(frozen=True)
class SimulationResult:
    """失败推演结果。"""

    expected_value: float
    failure_probability: float
    simulated_anxiety_if_fail: float
    execution_willingness: float
    block_execute: bool
    reasons: tuple[str, ...]
    confidence: float
    intuition_unstable: bool
    potential_solution: str | None = None
    matched_negative_anchor: str | None = None
    matched_memory_ids: tuple[int, ...] = ()

    def with_backtrack_solution(
        self,
        *,
        solution: str,
        anchor: str | None,
        memory_ids: tuple[int, ...],
    ) -> "SimulationResult":
        reasons = tuple(dict.fromkeys([*self.reasons, "backtrack_solution_found"]))
        return replace(
            self,
            execution_willingness=max(self.execution_willingness, 0.5),
            block_execute=False,
            reasons=reasons,
            potential_solution=solution,
            matched_negative_anchor=anchor,
            matched_memory_ids=memory_ids,
        )


@dataclass(frozen=True)
class WorldModelSnapshot:
    """供世界模型消费的只读快照。"""

    agent_state: object
    history_samples: tuple[HistorySample, ...]
    perception_confidence: float


@dataclass(frozen=True)
class ShadowExecutionObservation:
    """影子执行的物理观测结果。"""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    sandbox_backend: str
    verified: bool
    reasons: tuple[str, ...] = ()
    failure_cause: str | None = None
    missing_dependency: str | None = None
    next_priority_target: str | None = None


class WorldModel:
    """基于快照与历史样本做确定性因果评估。"""

    def build_action_outcome_matrix(
        self,
        history_samples: Iterable[HistorySample],
        outcomes: Sequence[str] = DEFAULT_OUTCOMES,
    ) -> Dict[str, OutcomeDistribution]:
        grouped: Dict[str, list[HistorySample]] = {}
        for sample in history_samples:
            grouped.setdefault(sample.action_key, []).append(sample)

        matrix: Dict[str, OutcomeDistribution] = {}
        for action_key, samples in grouped.items():
            counts = {outcome: 1 for outcome in outcomes}
            for sample in samples:
                counts[sample.outcome_type] = counts.get(sample.outcome_type, 1) + 1

            total = float(sum(counts.values()))
            probabilities = {outcome: counts[outcome] / total for outcome in counts}
            sample_size = len(samples)
            confidence = self.estimate_confidence(sample_size)
            matrix[action_key] = OutcomeDistribution(
                action_key=action_key,
                probabilities=probabilities,
                sample_size=sample_size,
                confidence=confidence,
                intuition_unstable=sample_size < MIN_STABLE_SAMPLE_SIZE,
            )

        return matrix

    def estimate_confidence(self, sample_size: int) -> float:
        if sample_size <= 0:
            return 0.15
        if sample_size < MIN_STABLE_SAMPLE_SIZE:
            return round(min(0.55, 0.15 + sample_size * 0.08), 3)
        return round(min(0.95, 0.55 + (sample_size - MIN_STABLE_SAMPLE_SIZE) * 0.04), 3)

    def apply_nonlinear_expectation(
        self,
        base_value: float,
        anxiety: float,
        volatility: float,
        confidence: float,
    ) -> float:
        normalized_anxiety = self._clamp(anxiety)
        normalized_volatility = self._clamp(volatility)
        normalized_confidence = self._clamp(confidence)

        anxiety_penalty = normalized_anxiety ** (1.0 + normalized_volatility * 2.0)
        volatility_penalty = normalized_volatility**1.5
        confidence_multiplier = 0.35 + normalized_confidence * 0.65
        effective_multiplier = max(
            0.0,
            confidence_multiplier
            * (1.0 - anxiety_penalty)
            * (1.0 - volatility_penalty),
        )
        return round(base_value * effective_multiplier, 6)

    def simulate_failure(
        self,
        snapshot: WorldModelSnapshot,
        action_candidate: ActionCandidate,
        distribution: OutcomeDistribution,
    ) -> SimulationResult:
        failure_probability = distribution.probabilities.get(OUTCOME_FAILURE, 0.5)
        expected_profit = action_candidate.expected_profit or 0.0
        base_expected_value = ((1.0 - failure_probability) * expected_profit) - (
            failure_probability * action_candidate.amount
        )
        adjusted_expected_value = self.apply_nonlinear_expectation(
            base_expected_value,
            anxiety=getattr(snapshot.agent_state, "anxiety", 0.0),
            volatility=action_candidate.volatility,
            confidence=distribution.confidence,
        )

        current_balance = max(float(getattr(snapshot.agent_state, "balance", 0.0)), 0.0)
        current_ratio = self._clamp(getattr(snapshot.agent_state, "balance_ratio", 1.0))
        inferred_initial_balance = (
            current_balance / current_ratio
            if current_balance > 0 and current_ratio > 0
            else max(current_balance, 1.0)
        )
        post_fail_balance = max(0.0, current_balance - action_candidate.amount)
        post_fail_ratio = (
            post_fail_balance / inferred_initial_balance
            if inferred_initial_balance > 0
            else 0.0
        )

        loss_severity = (
            1.0
            if current_balance <= 0
            else self._clamp(action_candidate.amount / current_balance)
        )
        uncertainty_penalty = 0.12 if distribution.intuition_unstable else 0.0
        simulated_anxiety_if_fail = self._clamp(
            getattr(snapshot.agent_state, "anxiety", 0.0)
            + loss_severity * 0.7
            + self._clamp(action_candidate.volatility) * 0.2
            + failure_probability * 0.15
            + uncertainty_penalty
        )

        hunger_risk = bool(
            getattr(snapshot.agent_state, "is_hunger_mode", False)
            or post_fail_ratio < 0.1
        )
        reasons: list[str] = []
        if distribution.intuition_unstable:
            reasons.append("intuition_unstable")
        if simulated_anxiety_if_fail > 0.95:
            reasons.append("simulated_anxiety_above_threshold")
        if hunger_risk:
            reasons.append("hunger_mode_risk")

        block_execute = (
            "simulated_anxiety_above_threshold" in reasons
            or "hunger_mode_risk" in reasons
        )

        if block_execute:
            execution_willingness = 0.0
        else:
            willingness_base = (1.0 - failure_probability) * distribution.confidence
            anxiety_drag = self._clamp(
                getattr(snapshot.agent_state, "anxiety", 0.0)
                * (1.0 + self._clamp(action_candidate.volatility))
            )
            uncertainty_drag = 0.2 if distribution.intuition_unstable else 0.0
            execution_willingness = self._clamp(
                willingness_base - anxiety_drag - uncertainty_drag
            )

        return SimulationResult(
            expected_value=adjusted_expected_value,
            failure_probability=round(failure_probability, 6),
            simulated_anxiety_if_fail=round(simulated_anxiety_if_fail, 6),
            execution_willingness=round(execution_willingness, 6),
            block_execute=block_execute,
            reasons=tuple(reasons),
            confidence=distribution.confidence,
            intuition_unstable=distribution.intuition_unstable,
        )

    def evaluate_action(
        self, snapshot: WorldModelSnapshot, action_candidate: ActionCandidate
    ) -> SimulationResult:
        matrix = self.build_action_outcome_matrix(snapshot.history_samples)
        distribution = matrix.get(action_candidate.action_key)
        if distribution is None:
            distribution = OutcomeDistribution(
                action_key=action_candidate.action_key,
                probabilities={OUTCOME_SUCCESS: 0.5, OUTCOME_FAILURE: 0.5},
                sample_size=0,
                confidence=self.estimate_confidence(0),
                intuition_unstable=True,
            )
        return self.simulate_failure(snapshot, action_candidate, distribution)

    def verify_shadow_execution(
        self,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_seconds: float,
        sandbox_backend: str,
        timed_out: bool = False,
    ) -> ShadowExecutionObservation:
        reasons: list[str] = []
        failure_cause: str | None = None
        missing_dependency: str | None = None
        next_priority_target: str | None = None
        combined_output = "\n".join(part for part in (stdout, stderr) if part).strip()
        if timed_out:
            reasons.append("shadow_timeout")
            failure_cause = "timeout"
        if exit_code != 0:
            reasons.append("shadow_non_zero_exit")
        if stderr.strip():
            reasons.append("shadow_stderr_observed")
        missing_match = re.search(
            r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]",
            combined_output,
        )
        if missing_match:
            missing_dependency = missing_match.group(1)
            next_priority_target = f"安装 {missing_dependency}"
            failure_cause = "missing_dependency"
            reasons.append("shadow_missing_dependency")
        if failure_cause is None and exit_code != 0:
            failure_cause = "runtime_error"
        verified = (not timed_out) and exit_code == 0
        return ShadowExecutionObservation(
            success=verified,
            exit_code=int(exit_code),
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(float(duration_seconds), 6),
            sandbox_backend=sandbox_backend,
            verified=verified,
            reasons=tuple(reasons),
            failure_cause=failure_cause,
            missing_dependency=missing_dependency,
            next_priority_target=next_priority_target,
        )

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))
