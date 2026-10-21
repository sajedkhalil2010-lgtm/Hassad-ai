"""Hassad AI multi-agent package.

Public surface::

    from agents import DiagnosticAgent, AdvisorAgent, ChatAgent, SupervisorAgent

The diagnostic, advisor and chat agents each call the Groq SDK; the
supervisor is pure orchestration and never calls a model itself.
"""

from .advisor_agent import AdvisorAgent
from .base import AgentError
from .chat_agent import ChatAgent
from .config import Settings, get_settings
from .diagnostic_agent import DiagnosticAgent
from .supervisor_agent import SupervisorAgent

__all__ = [
    "AdvisorAgent",
    "AgentError",
    "ChatAgent",
    "DiagnosticAgent",
    "Settings",
    "SupervisorAgent",
    "get_settings",
]
