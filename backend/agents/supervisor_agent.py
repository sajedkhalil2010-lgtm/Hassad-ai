"""SupervisorAgent — orchestration, error policy and the JSON wire contract.

This agent **never** calls a model itself. It dispatches to
:class:`~agents.diagnostic_agent.DiagnosticAgent` and
:class:`~agents.advisor_agent.AdvisorAgent`, applies a strict error policy,
and always returns one well-formed JSON-serialisable report.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .advisor_agent import AdvisorAgent
from .base import AgentError, AgentResult, as_dict, as_str, clamp01
from .config import Settings, get_settings
from .diagnostic_agent import DiagnosticAgent
from .language import DEFAULT_LANGUAGE, resolve_language

PIPELINE_ID = "supervisor_v1"

#: Below this diagnosis confidence the plan is delivered as "provisional".
PROVISIONAL_THRESHOLD = 0.55


class SupervisorAgent:
    """Orchestrates diagnostic -> advisory and normalises the final report."""

    name = "supervisor"

    def __init__(
        self,
        settings: Settings | None = None,
        diagnostic: DiagnosticAgent | None = None,
        advisor: AdvisorAgent | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.diagnostic = diagnostic or DiagnosticAgent(self.settings)
        self.advisor = advisor or AdvisorAgent(self.settings)

    # -- public API --------------------------------------------------------

    def run(
        self,
        image_bytes: bytes,
        *,
        filename: str | None = None,
        declared_mime: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Full pipeline. Always returns a JSON-serialisable dict — never raises.

        ``language`` is resolved once, here, so both stages answer in the same
        script. Resolving it per-agent would let the vision and advisory stages
        disagree — a diagnosis in Arabic followed by an English plan is worse
        than either alone, because the farmer cannot tell which one is wrong.
        """
        lang = resolve_language(language)
        run_id = uuid.uuid4().hex
        started = time.perf_counter()
        stages: list[dict[str, Any]] = []

        # ---- Stage 1: diagnosis -----------------------------------------
        t0 = time.perf_counter()
        diagnosis_result = self.diagnostic.analyze(image_bytes, filename=filename, declared_mime=declared_mime, language=lang)
        stages.append(self._stage("diagnostic", t0, diagnosis_result))

        if not diagnosis_result.ok:
            error = diagnosis_result.error or AgentError("api", "Diagnostic stage failed.")
            return self._error_report(run_id, started, stages, error, lang)

        diagnosis = as_dict(diagnosis_result.data)
        confidence = clamp01(diagnosis.get("confidence"), 0.0)
        provisional = confidence < PROVISIONAL_THRESHOLD or bool(diagnosis.get("low_confidence"))

        # ---- Stage 2: advisory ------------------------------------------
        t1 = time.perf_counter()
        advisory_result = self.advisor.build_plan(diagnosis, language=lang)
        stages.append(self._stage("advisor", t1, advisory_result))

        if advisory_result.ok:
            advisory: dict[str, Any] | None = as_dict(advisory_result.data)
        else:
            # Advisory failure is NOT fatal: an honest diagnosis with a clear
            # "no plan" marker beats suppressing the whole report.
            adv_error = advisory_result.error or AgentError("api", "Advisory stage failed.")
            advisory = {"unavailable": True, "reason": adv_error.message, "kind": adv_error.kind}
            if adv_error.message_en:
                advisory["reason_en"] = adv_error.message_en

        total_ms = int((time.perf_counter() - started) * 1000)

        return {
            "ok": True,
            "status": "provisional" if provisional else "confirmed",
            "run_id": run_id,
            "pipeline": PIPELINE_ID,
            "language": lang,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "diagnosis": diagnosis,
            "advisory": advisory,
            "confidence": {
                "overall": confidence,
                "provisional": provisional,
                "threshold": PROVISIONAL_THRESHOLD,
            },
            "stages": stages,
            "timing": {
                "total_ms": total_ms,
                "diagnostic_ms": as_dict(stages[0]).get("duration_ms", 0),
                "advisor_ms": as_dict(stages[1]).get("duration_ms", 0) if len(stages) > 1 else 0,
            },
        }

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _stage(name: str, started: float, result: AgentResult) -> dict[str, Any]:
        duration = int((time.perf_counter() - started) * 1000)
        if result.ok:
            data = result.data if isinstance(result.data, dict) else {}
            meta = as_dict(data.get("meta"))
            return {
                "name": name,
                "status": "ok",
                "duration_ms": duration,
                "source": as_str(meta.get("source"), name),
            }
        error = result.error or AgentError("api", f"{name} failed.")
        return {"name": name, "status": "error", "duration_ms": duration, "error": error.to_dict()}

    @staticmethod
    def _error_report(
        run_id: str,
        started: float,
        stages: list[dict[str, Any]],
        error: AgentError,
        language: str = DEFAULT_LANGUAGE,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "failed",
            "run_id": run_id,
            "pipeline": PIPELINE_ID,
            "language": language,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error": error.to_dict(),
            "diagnosis": None,
            "advisory": None,
            "confidence": {"overall": 0.0, "provisional": True, "threshold": PROVISIONAL_THRESHOLD},
            "stages": stages,
            "timing": {"total_ms": int((time.perf_counter() - started) * 1000)},
        }


__all__ = ["SupervisorAgent", "PIPELINE_ID", "PROVISIONAL_THRESHOLD"]
