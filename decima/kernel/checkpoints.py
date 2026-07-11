"""Checkpoints (DEC-020) — a SIGNED local checkpoint over the snapshot frontier.

A checkpoint is *local integrity evidence*: a small, signed commitment that says
"at this frontier of the Weft, the fold produced exactly this state_root, under this
protocol version, and I (the signer) vouch for it." It is the cheap, portable
witness that lets a later reader detect that either the log frontier or the folded
state drifted from what was committed — WITHOUT re-anchoring to an external service
(external anchoring / third-party notarization is deferred).

Where a Snapshot (snapshot.py) captures the *materialized CellState* so a fold can
resume from a base, a Checkpoint captures only the *commitment*: the frontier (head
id + event count), the fold `state_root`, the protocol version, the signer, and an
Ed25519 signature over the canonical checkpoint bytes. It composes snapshot.py's
`_frontier` for the frontier/count so the two agree by construction.

Purity (Law-aligned, same discipline as the rest of the TCB):
  - No network, no subprocess, no provider — signing goes through the keyring seam,
    hashing through decima.kernel.hashing (the import-boundary guard scans this file).
  - Deterministic: the signed content is ints + strings + a content hash; NO
    wall-clock is ever read here. If a caller wants to stamp a time it must pass one
    in (`recorded_at`), and it becomes part of the signed content verbatim.

API:
  - make_checkpoint(weft, weave, keyring, signer_pid, *, protocol_version,
                    recorded_at=None) -> dict
  - verify_checkpoint(checkpoint, weave, keyring) -> (ok: bool, reason: str)

`verify_checkpoint` recomputes the state_root from the CURRENT fold the caller hands
it and compares it to the committed root (catching state drift), then verifies the
signature over the canonical unsigned bytes (catching any tamper to the frontier,
event count, protocol version, signer, or root). Any failure returns (False, reason);
success returns (True, "ok"). Fail closed — never raises on a bad checkpoint.
"""
from decima.kernel.hashing import content_id
from decima.kernel.snapshot import _frontier

# The checkpoint envelope version — bump when the checkpoint field set changes so an
# old reader refuses a shape it cannot interpret rather than mis-verifying it.
_CKPT_SCHEMA = 1


def _checkpoint_id(checkpoint: dict) -> str:
    """Content id over the UNSIGNED checkpoint bytes (everything but `signature`).
    This is the exact message the signer signs and a verifier re-derives — domain
    separated into its own id space via kind="checkpoint"."""
    unsigned = {k: v for k, v in checkpoint.items() if k != "signature"}
    return content_id(unsigned, kind="checkpoint")


def make_checkpoint(weft, weave, keyring, signer_pid: str, *,
                    protocol_version: str, recorded_at=None) -> dict:
    """Build a signed local checkpoint committing the Weft frontier + fold root.

    `weft`  — the log; its frontier (head id) and event count are captured.
    `weave` — a fold of that weft (Weave.fold(weft)); its `state_root()` is committed.
    `keyring`, `signer_pid` — who vouches; the signature is Ed25519 over the canonical
                              unsigned checkpoint bytes (the private key stays in the
                              custodian — only the signature crosses the seam).
    `protocol_version` — the protocol version STRING recorded in (and signed over) the
                         checkpoint.
    `recorded_at` — optional caller-supplied time (any JSON scalar). NEVER read from a
                    clock here; if omitted it is None. Passed in → part of signed bytes.
    """
    frontier, count = _frontier(weft, None)          # composes snapshot's frontier
    checkpoint = {
        "schema": _CKPT_SCHEMA,
        "protocol_version": str(protocol_version),
        "head": weft.head,                           # the frontier's head event id (or None)
        "frontier": frontier,                        # [head] (empty on a genesis-only weft)
        "event_count": count,                        # exact number of events folded in
        "state_root": weave.state_root(),            # the authoritative fold root
        "signer": signer_pid,
        "recorded_at": recorded_at,                  # caller-supplied; no wall-clock here
        "signature": None,
    }
    checkpoint["signature"] = keyring.sign(signer_pid, _checkpoint_id(checkpoint))
    return checkpoint


def verify_checkpoint(checkpoint: dict, weave, keyring) -> tuple[bool, str]:
    """Verify a checkpoint against a CURRENT fold and the signer's public key.

    Two independent checks, both required (fail closed, never raises):
      1. STATE INTEGRITY — the committed `state_root` equals the state_root of the
         fold the caller hands in. A drifted / forged state (or a swapped root in the
         checkpoint) is rejected here.
      2. SIGNATURE INTEGRITY — the signature verifies, under `signer`'s public key,
         over the canonical unsigned checkpoint bytes. Any tamper to the frontier,
         head, event count, protocol version, signer, or root breaks this.
    Returns (True, "ok") only if both pass; otherwise (False, <reason>).
    """
    if not isinstance(checkpoint, dict):
        return (False, "checkpoint is not a mapping")
    if checkpoint.get("schema") != _CKPT_SCHEMA:
        return (False, "checkpoint schema mismatch")

    signer = checkpoint.get("signer")
    sig = checkpoint.get("signature")
    if not signer or not sig:
        return (False, "checkpoint missing signer/signature")

    # 1. State integrity: the committed root must match the current fold's root.
    try:
        current_root = weave.state_root()
    except Exception as exc:                          # pragma: no cover - defensive
        return (False, f"could not compute current state_root: {exc}")
    if checkpoint.get("state_root") != current_root:
        return (False, "state_root mismatch — fold diverged from the committed frontier")

    # 2. Signature integrity: over the canonical UNSIGNED bytes, under signer's key.
    if not keyring.verify(signer, _checkpoint_id(checkpoint), sig):
        return (False, "signature invalid — checkpoint tampered or wrong signer key")

    return (True, "ok")
