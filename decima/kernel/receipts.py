"""Effect / receipt outcome status constants (DEC-019).

Extracted verbatim from the reference `executor` module so the kernel fold can
name a receipt's terminal state without importing the effect-execution machinery
(which belongs in isolated workers, not the trusted core). Values are identical
to the reference — the fold and any conformance fixture see the same strings.
"""

SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
COMPENSATED = "COMPENSATED"
CANCELLED = "CANCELLED"
