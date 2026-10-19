"""Hassad AI multi-agent package.

Public surface::

    from agents import DiagnosticAgent, AdvisorAgent, SupervisorAgent

Only the diagnostic and advisor agents touch the Gemini SDK; the supervisor
is pure orchestration and never calls a model itself.
"""

from .advisor_agent import AdvisorAgent
from .base import AgentError
from .config import Settings, get_settings
from .diagnostic_agent import DiagnosticAgent
from .supervisor_agent import SupervisorAgent

__all__ = [
    "AdvisorAgent",
    "AgentError",
    "DiagnosticAgent",
    "Settings",
    "SupervisorAgent",
    "get_settings",
]
