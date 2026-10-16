"""ORM models package for Hassad AI.

Importing this package registers every table on ``Base.metadata``.
"""

from app.models.chatbot import ChatMessage, ChatSession, KnowledgeBase
from app.models.diagnosis import CropSource, Diagnosis, DiagnosisStatus
from app.models.farm import Farm, Plot
from app.models.user import User, UserRole

__all__ = [
    "ChatMessage",
    "ChatSession",
    "CropSource",
    "Diagnosis",
    "DiagnosisStatus",
    "Farm",
    "KnowledgeBase",
    "Plot",
    "User",
    "UserRole",
]
