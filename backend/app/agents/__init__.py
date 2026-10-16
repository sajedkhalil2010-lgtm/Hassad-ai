"""Hassad AI v2 — autonomous multi-agent framework.

Three agents coordinate every AI workflow:

* :class:`~app.agents.diagnostic_agent.DiagnosticAgent` — deep visual
  pathology on uploaded crop images (Gemini vision, schema-enforced JSON).
* :class:`~app.agents.advisor_agent.AdvisorAgent` — localized treatment
  plans and RAG-grounded conversational advice.
* :class:`~app.agents.supervisor_agent.SupervisorAgent` — orchestrates
  the sequence, handles fallbacks, returns one unified JSON report.

Supporting components:

* :mod:`app.agents.key_manager` — smart API-key pool (rotation on 429,
  quota memo, zero SDK retries; keys never leave the process).
* :mod:`app.agents.knowledge` — local agronomy knowledge layer (no API
  calls): Arabic-aware retrieval, citations, prompt assembly, seeder.
* :mod:`app.agents.base` — shared contracts (``AgentFailure``,
  ``AgentContext``, coercers, crop vocabulary).
"""

from app.agents.advisor_agent import AdvisorAgent
from app.agents.base import AgentContext, AgentFailure
from app.agents.diagnostic_agent import DiagnosticAgent
from app.agents.key_manager import KeyPoolManager
from app.agents.supervisor_agent import SupervisorAgent

__all__ = [
    "AdvisorAgent",
    "AgentContext",
    "AgentFailure",
    "DiagnosticAgent",
    "KeyPoolManager",
    "SupervisorAgent",
]
