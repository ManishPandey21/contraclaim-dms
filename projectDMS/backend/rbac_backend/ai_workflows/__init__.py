"""
LangGraph-oriented workflow utilities for the RBAC backend.

This package contains light-weight orchestration helpers that emulate a
LangGraph-style pipeline without introducing the external dependency.
"""

from .langgraph.letter_pipeline import LetterDraftGraph, LetterGraphResult

__all__ = ["LetterDraftGraph", "LetterGraphResult"]
