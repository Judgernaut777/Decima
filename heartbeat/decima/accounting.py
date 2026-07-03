"""Real bookkeeping engine — WRAP the books, never recreate accounting (dependency policy).

Decima's policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals
WRAP THE REAL ENGINE rather than reimplement it — recreating a general ledger is itself
the liability (the books are the legal system of record; balances, audit trails, close
periods and tax exposure hang off them). LEDGER1 stays Decima's OWN internal double-entry
record; this module COMPLEMENTS it by POSTING a journal entry / invoice to a REAL
bookkeeping provider (a QuickBooks/Xero-style HTTPS API) so the financial record lands in
the actual system of record. The provider is just an HTTPS API, so the real engine rides
stdlib `urllib` with ZERO pip dependencies: real engine, still pure-stdlib.

GUARDRAILS (mirroring the tax engine / OIDC engine / Stripe rail):
  - **entries MUST balance** — a journal entry is a list of debit/credit lines whose
    amounts are INTS in minor units; `post_entry` proves `sum(debits) == sum(credits)`
    (the double-entry invariant) BEFORE any request is made. An unbalanced entry never
    touches the wire.
  - **HTTPS-only** — `post_entry` refuses to send the API key to a non-`https://`
    endpoint before any request is made (never leak the key in cleartext).
  - **key via CRED1** — the provider API key lives in the secrets broker; `post` calls
    `broker.use_secret`, which applies the key INSIDE the broker (never returned, never
    logged, never on the Weft). The raw key never appears in an `accounting_entry` cell.
  - **fail closed** — a provider 4xx / declared error, an unreachable endpoint, or a
    denied credential records NO `accounting_entry` cell and returns `{"denied": reason}`.
  - **ints only in signed content** — amounts are ints in minor units; no float ever
    enters a value that lands on the Weft.
  - **transport seam** — `post_entry` takes a `transport(url, headers, body) -> (status,
    json)`; the default is a real `urllib` POST; tests inject a fake, so the offline
    oracle exercises the full contract with NO network.

Composes public secrets / model / kernel APIs only. No core edit; does not touch ledger.py.
"""
import json

from decima.model import assert_content
from decima.hashing import content_id

ACCOUNTING_ENTRY = "accounting_entry"   # the on-Weft record of a posted journal entry (no key)


class AccountingError(Exception):
    """A bookkeeping-engine failure — no `accounting_entry` may be recorded (fail closed).
    Covers an unbalanced entry, a non-HTTPS endpoint, an unreachable/timed-out endpoint,
    and a provider 4xx/error."""


def _urllib_transport(url: str, headers: dict, body: str):
    """(Phase 2 · GO LIVE) FAIL-CLOSED default — the bare stdlib socket default is
    GONE: the armed wire guard (decima/wire.py) refuses ungated egress anyway, so
    `transport=None` on the live path now refuses HERE, first, with the sanctioned
    path named. Build the wire-gated transport via
    `live_wire.gated_transport(k, agent_cell, cap_id)`
    (a granted, Morta-approved egress capability) and inject it as `transport=`.
    Injected fake transports (the offline oracle, every test-mode path) never
    resolve to this default and are unaffected."""
    from decima import live_wire
    raise live_wire.NoGatedTransport(
        "accounting", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def _require_int(name: str, v):
    """Guard that a value the engine will fold / sign is an int (never a float/bool)."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise AccountingError(f"{name} must be an int (minor units), got {v!r}")
    return int(v)


def _normalize_lines(lines) -> tuple:
    """Validate the journal lines and return (normalized_lines, total_debit, total_credit).

    Each line is a dict with an `account` and exactly one of `debit` / `credit` as a
    NON-NEGATIVE int in minor units. Raises `AccountingError` on any non-int amount, a
    negative amount, a line with both/neither side, or an empty entry."""
    if not isinstance(lines, (list, tuple)) or not lines:
        raise AccountingError("a journal entry must carry at least one debit/credit line")
    normalized, total_debit, total_credit = [], 0, 0
    for i, ln in enumerate(lines):
        if not isinstance(ln, dict):
            raise AccountingError(f"line {i} must be a dict, got {ln!r}")
        has_debit = "debit" in ln and ln.get("debit") is not None
        has_credit = "credit" in ln and ln.get("credit") is not None
        if has_debit == has_credit:
            raise AccountingError(f"line {i} must have exactly one of debit/credit")
        account = ln.get("account")
        if has_debit:
            amt = _require_int(f"line {i} debit", ln.get("debit"))
            if amt < 0:
                raise AccountingError(f"line {i} debit must be non-negative")
            total_debit += amt
            normalized.append({"account": account, "debit": amt})
        else:
            amt = _require_int(f"line {i} credit", ln.get("credit"))
            if amt < 0:
                raise AccountingError(f"line {i} credit must be non-negative")
            total_credit += amt
            normalized.append({"account": account, "credit": amt})
    return normalized, total_debit, total_credit


def post_entry(secret_key: str, entry: dict, *, transport=None) -> dict:
    """Post a journal entry / invoice to the REAL bookkeeping provider.

    `entry` describes the posting — `endpoint` (the provider's HTTPS journal/invoice URL),
    `lines` (a list of debit/credit lines whose amounts are ints in minor units and MUST
    balance), a `memo`, and a `reference` (an idempotency / document reference). The lines
    are validated and PROVEN to balance (`sum(debits) == sum(credits)`) BEFORE any request
    is made; an unbalanced or malformed entry raises `AccountingError` before the key
    touches the wire. POSTs it over stdlib `urllib` and returns the provider's answer:
    {provider_ref, status, posted_amount:int}. Amounts are ints in minor units (no floats).

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the wire.
    Raises `AccountingError` on an unbalanced entry, a non-HTTPS endpoint, an unreachable
    endpoint, or a definite provider error (4xx / error body) — the caller (`post`) fails
    closed."""
    transport = transport or _urllib_transport

    # Double-entry invariant, checked BEFORE any request (and before the key is read).
    lines, total_debit, total_credit = _normalize_lines(entry.get("lines"))
    if total_debit != total_credit:
        raise AccountingError(
            f"entry does not balance: debits {total_debit} != credits {total_credit}")

    endpoint = str(entry.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the API key on the wire in cleartext. Refuse before sending.
        raise AccountingError("refusing to send the API key to a non-HTTPS accounting endpoint")

    memo = str(entry.get("memo", ""))
    reference = str(entry.get("reference", ""))
    payload = {
        "lines": lines,
        "memo": memo,
        "reference": reference,               # idempotency / document reference
    }
    body = json.dumps(payload)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unreachable
        raise AccountingError(f"accounting endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise AccountingError(f"unparseable accounting response (status {status})")
    if status in (200, 201) and (resp.get("provider_ref") or resp.get("id")):
        # The provider accepted the posting — it is now the system of record.
        return {
            "provider_ref": resp.get("provider_ref") or resp.get("id"),
            "status": resp.get("status", "posted"),
            "posted_amount": total_debit,     # == total_credit, an int in minor units
        }
    err = resp.get("error_description") or resp.get("error") or f"http {status}"
    raise AccountingError(f"provider rejected the journal entry: {err}")   # definite error


def post(k, *, endpoint: str, entry: dict, credential_handle: str, broker,
         agent_cell, transport=None) -> dict:
    """Post a journal entry to the REAL books and record it on the Weft (fail closed).

    Resolves the provider API key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `post_entry` on `entry` against the
    HTTPS `endpoint`, and on success asserts an `accounting_entry` cell carrying the
    balanced lines / posted_amount / provider_ref / memo (all money as int minor units —
    NEVER the key). Returns {accounting_entry: <cell id>, provider_ref, posted_amount}.

    On a denied credential (revoked/unauthorized/over-budget) or any engine error
    (unbalanced entry, non-HTTPS, unreachable, provider 4xx) it records NO cell and
    returns {"denied": reason}."""
    ent = {**entry, "endpoint": endpoint}
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: post_entry(key, ent, transport=transport))
    except AccountingError as e:
        return {"denied": f"accounting: {e}"}                # fail closed — no entry cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    # Re-validate/normalize the lines so the signed cell carries clean ints (the balance
    # invariant already held in post_entry; this cannot raise here).
    lines, total_debit, _ = _normalize_lines(ent.get("lines"))
    content = {
        "lines": lines,
        "posted_amount": _require_int("posted_amount", result["posted_amount"]),
        "provider_ref": result.get("provider_ref"),
        "status": result.get("status"),
        "memo": str(ent.get("memo", "")),
        "reference": str(ent.get("reference", "")),
    }
    # Content-addressed by the provider ref (the external system of record's id): the same
    # posted entry keeps one identity on the Log.
    cid = content_id({"accounting_entry": content["provider_ref"], "amount": total_debit})
    assert_content(k.weft, k.decima_agent_id, cid, ACCOUNTING_ENTRY, content)
    return {
        "accounting_entry": cid,
        "provider_ref": content["provider_ref"],
        "posted_amount": content["posted_amount"],
    }


def entries(k) -> list:
    """All folded `accounting_entry` cells on the Weft."""
    return list(k.weave().of_type(ACCOUNTING_ENTRY))
