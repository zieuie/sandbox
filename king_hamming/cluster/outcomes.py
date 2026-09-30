"""Typed process outcomes shared by supervision and durable queue transitions."""

from __future__ import annotations

from enum import Enum
import signal


class SolverOutcome(str, Enum):
    """Exhaustive meanings assigned to one supervised solver exit."""

    COMPLETE = "complete"
    INTENTIONAL_STOP = "intentional_stop"
    ENGINE_FAILURE = "engine_failure"
    STOP_FAILURE = "stop_failure"


def classify(return_code: int, stopped: bool, forced: bool,
             retry_elsewhere: bool) -> SolverOutcome:
    """Classify an exit without interpreting diagnostic text."""

    if return_code == 0:
        return SolverOutcome.COMPLETE
    if stopped and (retry_elsewhere or return_code in {75, -signal.SIGTERM} or
                    (forced and return_code == -signal.SIGKILL)):
        return SolverOutcome.INTENTIONAL_STOP
    if stopped:
        return SolverOutcome.STOP_FAILURE
    return SolverOutcome.ENGINE_FAILURE
