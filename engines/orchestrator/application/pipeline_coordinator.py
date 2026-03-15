"""Pipeline coordinator — manages individual phase execution with retry and timeout.

The coordinator is responsible for running each phase with:
- Configurable retry logic (exponential backoff)
- Per-phase timeout enforcement
- Failure isolation (one phase failure doesn't block others)
"""
import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Optional

from ..domain.enums import PhaseStatus, PipelinePhase
from ..domain.models import IntelligencePipeline, PhaseResult, PipelineConfig

logger = logging.getLogger(__name__)

PhaseExecutor = Callable[..., Coroutine[Any, Any, dict]]


class PipelineCoordinator:
    """Coordinates execution of pipeline phases with retry and timeout."""

    def __init__(self, config: PipelineConfig) -> None:
        self._config = config

    async def run_phase(
        self,
        pipeline: IntelligencePipeline,
        phase: PipelinePhase,
        executor: PhaseExecutor,
        **kwargs: Any,
    ) -> PhaseResult:
        """Execute a single phase with retry logic."""
        result = pipeline.mark_phase_started(phase)

        for attempt in range(self._config.max_retries + 1):
            try:
                data = await asyncio.wait_for(
                    executor(**kwargs),
                    timeout=self._config.phase_timeout_seconds,
                )
                pipeline.mark_phase_completed(phase, data)
                logger.info(
                    "Phase %s completed for pipeline %s (%.0fms, attempt %d)",
                    phase.value, pipeline.pipeline_id,
                    result.duration_ms or 0, attempt + 1,
                )
                return result

            except asyncio.TimeoutError:
                error = f"Phase {phase.value} timed out after {self._config.phase_timeout_seconds}s"
                logger.warning(
                    "%s (pipeline=%s, attempt=%d/%d)",
                    error, pipeline.pipeline_id, attempt + 1, self._config.max_retries + 1,
                )
                result = pipeline.mark_phase_failed(phase, error)

            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "Phase %s failed (pipeline=%s, attempt=%d/%d): %s",
                    phase.value, pipeline.pipeline_id,
                    attempt + 1, self._config.max_retries + 1, error,
                )
                result = pipeline.mark_phase_failed(phase, error)

            # Exponential backoff before retry
            if attempt < self._config.max_retries:
                backoff = min(2 ** attempt * 0.5, 5.0)
                await asyncio.sleep(backoff)

        logger.error(
            "Phase %s exhausted retries for pipeline %s",
            phase.value, pipeline.pipeline_id,
        )
        return result

    async def run_parallel(
        self,
        pipeline: IntelligencePipeline,
        phase_executors: list[tuple[PipelinePhase, PhaseExecutor, dict]],
    ) -> list[PhaseResult]:
        """Run multiple phases concurrently. Each phase retries independently."""
        tasks = [
            self.run_phase(pipeline, phase, executor, **kwargs)
            for phase, executor, kwargs in phase_executors
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    def should_skip_phase(self, phase: PipelinePhase) -> bool:
        """Check if a phase should be skipped based on config."""
        if phase == PipelinePhase.PREDICTION and not self._config.enable_prediction:
            return True
        if phase == PipelinePhase.TREND and not self._config.enable_trend:
            return True
        if phase == PipelinePhase.EXECUTION and not self._config.enable_execution:
            return True
        return False
