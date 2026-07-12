"""`doctor` + `diagnostic_export` — read-only health and support tooling (handoff §13).

Both are PURE reads: they fold the Weft and inspect the durable byte-layout, but they
assert NOTHING, mint no capability, and add no Cell (Law 5 — an operational lens holds
no state of its own). `doctor` returns a structured, deterministic report; a
non-``ok`` overall status is the signal an operator (or a supervising routine) acts on.
`diagnostic_export` produces a SCRUBBED support bundle — versions, error codes, folded
states, and REDACTED log tails — that is safe to hand to a maintainer: it never reads
`keys/`, never emits raw Weft payloads or artifact bytes (which may hold private docs),
and runs every log line it does include through a secret redactor.
"""
from __future__ import annotations

import platform
import re
import shutil
import sqlite3
from typing import Any

from decima.kernel.hashing import blob_id, content_id
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft, WeftError
from decima.services.data_layout import (
    ARTIFACTS,
    CHECKPOINTS,
    LOGS,
    DataDir,
)

try:  # the installed package version, if importable
    from decima import __version__ as _DECIMA_VERSION
except Exception:  # pragma: no cover - defensive
    _DECIMA_VERSION = "unknown"

OK = "ok"
WARN = "warn"
FAIL = "fail"

# Rank so an overall status is the worst individual check (deterministic).
_RANK = {OK: 0, WARN: 1, FAIL: 2}


def _worst(statuses: list[str]) -> str:
    return max(statuses, key=lambda s: _RANK.get(s, 0)) if statuses else OK


# ── individual checks ──────────────────────────────────────────
def _check_versions() -> dict:
    return {"status": OK, "code": "versions",
            "decima": _DECIMA_VERSION, "python": platform.python_version()}


def _raw_row_integrity(db_path: str) -> tuple[int, str | None]:
    """Keyring-free integrity: recompute each event's content id from its bytes. Returns
    (count, first_bad_index_or_None)."""
    import json

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id, payload FROM events ORDER BY seq").fetchall()
    finally:
        conn.close()
    for i, (eid, payload_text) in enumerate(rows):
        try:
            payload = json.loads(payload_text)
        except (ValueError, TypeError):
            return len(rows), str(i)
        if content_id(payload, kind="event") != eid:
            return len(rows), str(i)
    return len(rows), None


def _check_weft(dd: DataDir, keyring: Any) -> tuple[dict, Weave | None]:
    """Weft integrity: a full fold must VERIFY (id recompute + signature) end to end.
    With a keyring we fold; without one we fall back to keyring-free byte integrity."""
    import os

    if not os.path.exists(dd.weft_db):
        return {"status": FAIL, "code": "weft-missing", "detail": dd.weft_db}, None
    if keyring is None:
        count, bad = _raw_row_integrity(dd.weft_db)
        if bad is not None:
            return {"status": FAIL, "code": "weft-tampered", "at": bad, "events": count}, None
        return {"status": WARN, "code": "weft-unverified-no-keyring", "events": count}, None
    try:
        weft = Weft(dd.weft_db, keyring)
        weave = Weave.fold(weft)
    except WeftError as exc:
        return {"status": FAIL, "code": "weft-fold-failed", "detail": str(exc)}, None
    return {"status": OK, "code": "weft", "events": weft.count(),
            "state_root": weave.state_root()}, weave


def _check_checkpoints(dd: DataDir, weave: Weave | None) -> dict:
    """Checkpoint consistency. Missing checkpoints → WARN. A checkpoint whose committed
    frontier lags the current log → stale (WARN). A checkpoint whose committed
    state_root disagrees with the fold at an equal event count → corrupt (FAIL)."""
    import json

    names = dd.list_files(CHECKPOINTS)
    if not names:
        return {"status": WARN, "code": "checkpoint-missing",
                "detail": "no checkpoint has been recorded"}
    if weave is None:
        return {"status": WARN, "code": "checkpoint-unverified", "count": len(names)}

    current_root = weave.state_root()
    current_count = len(weave._applied)  # events folded into this frontier
    worst = OK
    detail = "checkpoints consistent with the current fold"
    for name in names:
        try:
            with open(dd.path(CHECKPOINTS, name), encoding="utf-8") as fh:
                ckpt = json.load(fh)
        except (OSError, ValueError):
            worst, detail = FAIL, f"unreadable checkpoint {name}"
            continue
        committed_count = ckpt.get("event_count")
        committed_root = ckpt.get("state_root")
        if committed_count == current_count and committed_root != current_root:
            worst = FAIL
            detail = f"checkpoint {name} state_root disagrees with the fold at the same frontier"
        elif isinstance(committed_count, int) and committed_count < current_count and worst != FAIL:
            worst = WARN
            detail = f"checkpoint {name} is stale (log advanced past its frontier)"
    return {"status": worst, "code": "checkpoint", "count": len(names), "detail": detail}


def _check_artifacts(dd: DataDir) -> dict:
    """Artifact digests. Artifacts are content-addressed: filename == blob digest, so a
    tampered artifact's recomputed digest no longer matches its own name."""
    import os

    d = dd.path(ARTIFACTS)
    if not os.path.isdir(d):
        return {"status": OK, "code": "artifacts", "count": 0}
    bad: list[str] = []
    count = 0
    for name in dd.list_files(ARTIFACTS):
        count += 1
        with open(os.path.join(d, name), "rb") as fh:
            if blob_id(fh.read()) != name:
                bad.append(name)
    if bad:
        return {"status": FAIL, "code": "artifact-corrupt", "count": count, "corrupt": bad}
    return {"status": OK, "code": "artifacts", "count": count}


def _check_disk(dd: DataDir) -> dict:
    """Disk space under the base. WARN below a small floor so an operator sees it before
    a write fails."""
    usage = shutil.disk_usage(dd.base)
    free_mb = usage.free // (1024 * 1024)
    status = WARN if free_mb < 64 else OK
    return {"status": status, "code": "disk", "free_mb": int(free_mb),
            "total_mb": int(usage.total // (1024 * 1024))}


def _check_unresolved(weave: Weave | None) -> dict:
    """Unresolved effects: receipts still UNKNOWN plus invocations with no receipt at
    all. A pure fold — folded exactly like the rest of the system's state."""
    if weave is None:
        return {"status": WARN, "code": "effects-unverified"}
    receipts = weave.of_type("result") + weave.of_type("receipt")
    unknown = sum(1 for r in receipts if r.content.get("status") == "UNKNOWN")
    receipted = {r.content.get("of") for r in receipts} | {
        r.content.get("invocation") for r in receipts}
    unreceipted = sum(1 for inv in weave.invocations if inv.event not in receipted)
    total = int(unknown + unreceipted)
    status = WARN if total else OK
    return {"status": status, "code": "unresolved-effects",
            "unknown_receipts": int(unknown), "unreceipted_invocations": int(unreceipted),
            "total": total}


def doctor(base: str, *, keyring: Any = None) -> dict:
    """Run every operational check over `base` and return a structured report.

    Checks: package/python version, Weft integrity (a full fold must verify),
    checkpoint consistency, artifact digests, disk space, and unresolved effects. The
    overall `status` is the worst individual status (ok < warn < fail). A `keyring`
    unlocks the fold-based checks (Weft, checkpoints, effects); without one those degrade
    to keyring-free integrity + a warning rather than a silent pass. Pure read — asserts
    nothing.
    """
    dd = DataDir(base)
    versions = _check_versions()
    weft_check, weave = _check_weft(dd, keyring)
    checks = [
        versions,
        weft_check,
        _check_checkpoints(dd, weave),
        _check_artifacts(dd),
        _check_disk(dd),
        _check_unresolved(weave),
    ]
    return {
        "base": base,
        "status": _worst([c["status"] for c in checks]),
        "checks": checks,
    }


# ── scrubbed support bundle ───────────────────────────────────────
# Whole-line redaction triggers: a line mentioning any of these is dropped to a marker.
_SECRET_KEYWORDS = re.compile(
    r"(?i)(secret|token|password|passwd|api[_-]?key|apikey|seed|private[_-]?key|"
    r"bearer|authorization|credential)")
# Inline redaction: long hex / base64-ish runs (keys, seeds, signatures, ids).
_SECRET_BLOB = re.compile(r"[A-Za-z0-9+/=_-]{20,}")
_REDACTED = "[REDACTED]"


def _redact_line(line: str) -> str:
    """Redact one log line: a line naming a secret is masked whole; otherwise long
    opaque blobs (keys/seeds/signatures) inside it are masked in place. Aggressive on
    purpose — a support bundle must never carry a secret off the box."""
    if _SECRET_KEYWORDS.search(line):
        return _REDACTED
    return _SECRET_BLOB.sub(_REDACTED, line)


def _redacted_log_tails(dd: DataDir, max_lines: int = 50) -> dict[str, list[str]]:
    """The last few lines of each log file, every line redacted. Only `logs/` is read;
    keys, config, and Weft payloads are NEVER read into the bundle."""
    out: dict[str, list[str]] = {}
    for name in dd.list_files(LOGS):
        try:
            with open(dd.path(LOGS, name), encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        out[name] = [_redact_line(ln) for ln in lines[-max_lines:]]
    return out


# ── model surface: report the catalogue's capabilities (pure read, no authority) ─
def model_surface(registry: Any) -> dict:
    """A read-only lens over a model catalogue for operator diagnostics.

    Reports, per enabled entry, its int-clean capability metadata (reasoning /
    coding / planning / structured-reliability scores, latency & cost class, context
    limit, locality) alongside the forward-guidance recommended LOCAL model. It is a
    PURE read — it asserts nothing, mints no capability, and the capability tags it
    surfaces confer no authority (they steer selection only). Deterministic order
    (registry insertion). `registry` is any object exposing ``enabled_entries()``
    returning ``ModelEntry`` (a ``ModelRegistry`` or a ``ModelStack.registry``)."""
    from decima.services.api.models_setup import RECOMMENDED_LOCAL_MODEL

    entries = []
    for e in registry.enabled_entries():
        c = e.to_content()
        entries.append({
            "model": c["model"],
            "provider": c["provider"],
            "local": c["local"],
            "context_limit": c["context_limit"],
            "reasoning_strength": c["reasoning_strength"],
            "coding": c["coding"],
            "planning": c["planning"],
            "structured_reliability": c["structured_reliability"],
            "latency_class": c["latency_class"],
            "cost_class": c["cost_class"],
        })
    return {
        "schema": 1,
        "kind": "decima-model-surface",
        "recommended_local_model": RECOMMENDED_LOCAL_MODEL,
        "count": len(entries),
        "models": entries,
    }


def diagnostic_export(base: str, *, keyring: Any = None) -> dict:
    """Produce a SCRUBBED support bundle for `base` — safe to hand to a maintainer.

    Contains: package/python versions, the `doctor` report reduced to per-check
    status + error code + numeric fields, folded state COUNTS, and REDACTED log tails.
    Contains NO secrets/keys, NO raw Weft payloads, NO artifact bytes, NO private
    config — `keys/` is never opened and only redacted `logs/` tails are included.
    """
    dd = DataDir(base)
    report = doctor(base, keyring=keyring)
    # Reduce each check to non-sensitive scalars: status, code, and numeric fields only.
    safe_checks = []
    for c in report["checks"]:
        entry = {"code": c.get("code"), "status": c.get("status")}
        for k, v in c.items():
            if k in ("code", "status"):
                continue
            if isinstance(v, int) and not isinstance(v, bool):
                entry[k] = v
        safe_checks.append(entry)
    return {
        "schema": 1,
        "kind": "decima-diagnostic-export",
        "decima_version": _DECIMA_VERSION,
        "python_version": platform.python_version(),
        "overall_status": report["status"],
        "checks": safe_checks,
        "logs": _redacted_log_tails(dd),
    }
