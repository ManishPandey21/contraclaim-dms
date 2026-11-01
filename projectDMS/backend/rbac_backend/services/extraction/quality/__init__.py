"""Deterministic extraction quality checks.

Runs on every page from every source - native text layers included. Companion
evidence measured 9 split-digit corruptions in a PDF's own text layer, so
"native means trustworthy" is false.

Equally, it must not cry wolf: a naive numeric rule produced 12 false
mismatches on a document that was entirely correct. Every check here treats an
unestablished structure as NOT_CHECKABLE rather than FAILED, because a false
escalation costs a paid model call and buys nothing.
"""
