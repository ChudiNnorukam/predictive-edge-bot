"""
RAG (Retrieval-Augmented Generation) Architecture
==================================================

Persistent knowledge management for the trading bot.

Components:
- knowledge_store.py: ChromaDB vector storage for learnings
- learning_capture.py: Hooks to capture learnings from trade outcomes
- retrieval.py: Query relevant learnings before trade decisions
"""

from .knowledge_store import KnowledgeStore
from .learning_capture import LearningCapture

__all__ = ["KnowledgeStore", "LearningCapture"]
