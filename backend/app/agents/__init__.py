"""Hassad AI v4 — 100% local multi-agent autonomy.

Three agents coordinate every AI workflow through a reasoning
blackboard, entirely in-process (no external APIs, no keys, no rate
limits):

* :class:`~app.agents.supervisor_agent.SupervisorAgent` — plans the
  stage sequence, dispatches agents, evaluates results, decides the
  next move, returns one unified JSON report.
* :class:`~app.agents.diagnostic_agent.DiagnosticAgent` — autonomous
  quantitative pathology: segment → measure → match condition profiles
  (confidence from real segmentation consensus).
* :class:`~app.agents.advisor_agent.AdvisorAgent` — Tébessa-localized
  treatment plans + local-KB conversational advice.

Supporting modules (also fully local):

* :mod:`app.agents.local_vision` — OpenCV + NumPy leaf/lesion
  segmentation engine (quantitative metrics, Dice consensus).
* :mod:`app.agents.knowledge` — local agronomy knowledge layer:
  Arabic-aware retrieval, citations, prompt assembly, seeder.
* :mod:`app.agents.base` — shared contracts (``AgentFailure``,
  ``AgentContext``, ``RunTrace`` blackboard, coercers, crop vocab).
"""

from app.agents.advisor_agent import AdvisorAgent
from app.agents.base import AgentContext, AgentFailure, RunTrace
from app.agents.diagnostic_agent import DiagnosticAgent
from app.agents.supervisor_agent import SupervisorAgent

__all__ = [
    "AdvisorAgent",
    "AgentContext",
    "AgentFailure",
    "DiagnosticAgent",
    "RunTrace",
    "SupervisorAgent",
]
