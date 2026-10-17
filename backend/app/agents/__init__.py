"""Hassad AI v3 — pure agentic autonomy (zero raw APIs).

Three agents coordinate every AI workflow through a reasoning
blackboard:

* :class:`~app.agents.supervisor_agent.SupervisorAgent` — plans the
  stage sequence, dispatches agents, evaluates results, decides the
  next move, returns one unified JSON report.
* :class:`~app.agents.diagnostic_agent.DiagnosticAgent` — autonomous
  visual pathology: hypothesize → self-verify → bounded refine.
* :class:`~app.agents.advisor_agent.AdvisorAgent` — grounded,
  localized treatment plans + RAG-grounded conversational advice.

Infrastructure (no agent touches an SDK):

* :class:`~app.agents.cognition.CognitionEngine` — the **only** module
  that imports ``google.genai``; bounded, schema-enforced, rotating.
* :class:`~app.agents.key_manager.KeyPoolManager` — key-state policy:
  rotation windows, quota memo (zero wasted requests), size for
  /health (never key material).
* :mod:`app.agents.knowledge` — local agronomy knowledge layer (no API
  calls): Arabic-aware retrieval, citations, prompt assembly, seeder.
* :mod:`app.agents.base` — shared contracts (``AgentFailure``,
  ``AgentContext``, ``RunTrace`` blackboard, coercers, crop vocab).
"""

from app.agents.advisor_agent import AdvisorAgent
from app.agents.base import AgentContext, AgentFailure, RunTrace
from app.agents.cognition import CognitionEngine, engine
from app.agents.diagnostic_agent import DiagnosticAgent
from app.agents.key_manager import KeyPoolManager
from app.agents.supervisor_agent import SupervisorAgent

__all__ = [
    "AdvisorAgent",
    "AgentContext",
    "AgentFailure",
    "CognitionEngine",
    "DiagnosticAgent",
    "KeyPoolManager",
    "RunTrace",
    "SupervisorAgent",
    "engine",
]
