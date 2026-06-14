"""
Compatibility shim: route ai_workflows.langgraph imports to the real langgraph package
(backed by backend/rbac_backend/ai_workflows/langgraph/).
This avoids the file-vs-package collision and keeps existing imports working.
"""
from importlib import import_module
from pathlib import Path

# Make this module behave like a package so submodules resolve under langgraph/
__path__ = [str(Path(__file__).with_name("langgraph"))]

_pkg = import_module(__name__ + ".letter_pipeline")
LetterDraftGraph = _pkg.LetterDraftGraph
LetterGraphResult = _pkg.LetterGraphResult
LetterGraphNodeTrace = getattr(_pkg, "LetterGraphNodeTrace", None)

__all__ = ["LetterDraftGraph", "LetterGraphResult", "LetterGraphNodeTrace"]
