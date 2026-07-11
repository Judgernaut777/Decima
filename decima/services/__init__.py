"""Operability services for a local Decima install (handoff §12-13).

These modules are NOT part of the trusted computing base (the kernel) and NOT
canonical: they read the Weft, copy the durable byte-artifacts around it, and fold
disposable projections — but they mint no authority and hold no second source of
truth. The Weft remains the sole canonical store; a backup is the log itself made
portable, a restore replays it back through the kernel's own acceptance gate, and
`doctor`/`diagnostic_export` only READ (they assert nothing).
"""
