"""Real insurance-claim rail — wrap the carrier's filing engine, never fake a filing.

Decima's dependency policy: recreate the design in pure stdlib, but for HIGH-LIABILITY
externals WRAP THE REAL ENGINE rather than reinvent it. FILING an insurance claim is an
OUTWARD, high-liability legal/financial submission to a real carrier — asserting a loss,
an amount, and evidence on the record. Faking that filing (pretending a claim was lodged
when it was not) IS the liability, so this NEVER simulates the outcome. A carrier's claims
API is just an HTTPS endpoint, so the real engine is reachable over stdlib `urllib` with
**zero pip deps**: real engine, still pure-stdlib.

This complements the EXISTING `insurance.py` composition stub (which models policies /
coverage / payouts against the ledger) by wrapping a REAL carrier for the ACT of filing,
behind the SAME spine the payment / e-sign rails already enforce, as a LEGAL effect. It
registers a Morta-gated, budget-capped, idempotent effect via `kernel.integrate_tool`; the
receipt maps the carrier's outcome to WEFT §8 status:
  - a created/received claim (201/200)   → SUCCEEDED, carrying the carrier `provider_ref`
                                           (the claim id), the claim status
                                           ("received"/"under_review"), and the claimed
                                           amount (an int, minor units);
  - a definite 4xx bad request           → FAILED (invalid policy / bad request; nothing
                                           was filed);
  - a network error / timeout            → UNKNOWN (we cannot observe whether it filed —
                                           never fabricated as success or failure,
                                           FOLD §11 #8).

POLICY / GUARDRAILS (mirroring the Stripe / e-sign rails):
  - **wrap the real carrier** — zero pip deps; the real HTTPS carrier over `urllib`.
  - **HTTPS-only** — refuses to put the key / the claim on a non-`https://` endpoint before
    any request is made (a legal submission and the API key must never travel in cleartext).
  - **credentials via CRED1** — the carrier key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in a receipt/audit.
  - **Morta-gated** — filing a claim `requires_approval`; denied until approved.
  - **idempotent** — a prior SUCCEEDED receipt for the same idempotency key returns without
    a second filing (no duplicate claim). Provider-level Idempotency-Key header too.
  - **claimant input is UNTRUSTED DATA** — the policy ref, incident description, evidence
    refs are treated as data, validated/normalized, never as instructions. Ints only in
    signed content (incident date, claimed amount — both minor units / epoch ints).
  - **transport seam** — `file_claim` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json

from decima import executor
from decima.hashing import nfc

LEGAL = "LEGAL"                    # effect_class — an outward legal/financial submission
RESULT = "result"                 # the EffectReceipt cell type the kernel asserts
_OK_STATUSES = (200, 201)
_RECEIVED = ("received", "under_review")


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
        "insurance_claim", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def _int(label: str, value) -> int:
    """Coerce-check an int (reject bool/float) so no float ever reaches signed content."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise executor.ExecError(f"insclaim: {label} must be an int, got {value!r}")
    return int(value)


def _evidence(args: dict) -> list:
    """Normalize the UNTRUSTED evidence input to a clean list of evidence refs. Accepts a
    single ref string or a list; strips blanks; normalizes each. Never interpreted as
    anything but DATA."""
    raw = args.get("evidence", args.get("evidence_refs"))
    if isinstance(raw, str):
        raw = [raw]
    return [nfc(str(r)) for r in (raw or []) if str(r).strip()]


def file_claim(secret_key: str, args: dict, *, transport=None) -> dict:
    """File a claim with the carrier, mapping the outcome to an EffectReceipt-shaped result.
    Raises `executor.ExecError` for a definite no-effect (invalid policy / bad request /
    4xx → FAILED, nothing filed) and `executor.Ambiguous` for an unobservable outcome
    (network/timeout → UNKNOWN — we don't know if it filed). On success returns the output
    dict spread into a SUCCEEDED receipt, carrying `provider_ref` (the claim id), the claim
    status ("received"/"under_review"), and the claimed amount (an int, minor units).

    HTTPS-ONLY INVARIANT: a non-`https://` endpoint is refused before any request is made —
    a legal submission and the API key must never travel in cleartext. Claimant input (the
    policy ref, incident description, evidence refs) is treated as UNTRUSTED DATA (validated
    and normalized, never obeyed). All signed numbers are ints (§1)."""
    transport = transport or _urllib_transport
    endpoint = str(args.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put a legal claim / the API key on the wire in cleartext. Fail closed.
        raise executor.ExecError("insclaim: refusing to file a claim to a non-HTTPS endpoint")

    policy = nfc(str(args.get("policy", "")))                 # UNTRUSTED — DATA, validated
    if not policy:
        raise executor.ExecError("insclaim: a policy reference is required")
    description = nfc(str(args.get("description", "")))       # UNTRUSTED — DATA, validated
    if not description:
        raise executor.ExecError("insclaim: an incident description is required")
    incident_date = _int("incident date", args.get("incident_date"))    # epoch int
    amount = _int("claimed amount", args.get("amount"))                  # minor units, int
    if amount <= 0:
        raise executor.ExecError("insclaim: claimed amount must be a positive integer (minor units)")
    evidence = _evidence(args)                                # UNTRUSTED — DATA, validated
    idem = str(args.get("idempotency_key") or "")

    payload = {
        "policy": policy,
        "description": description,
        "incident_date": incident_date,                      # int, never a float (§1)
        "amount": amount,                                     # int, never a float (§1)
        "evidence": evidence,
        "evidence_count": len(evidence),                     # int
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-duplicate-filing
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"insclaim: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"insclaim: unparseable response (status {status_code})")
    claim_id = resp.get("claim_id") or resp.get("id")
    if status_code in _OK_STATUSES and claim_id:
        raw_status = str(resp.get("status", "received"))
        claim_status = raw_status if raw_status in _RECEIVED else "received"
        return {"out": f"filed claim for policy {policy} ({amount} minor units)",
                "provider_ref": claim_id,                     # the carrier's claim id
                "provider_status": claim_status,              # "received"/"under_review"
                "claimed_amount": amount,                     # int, minor units
                "policy": policy, "incident_date": incident_date,
                "evidence": len(evidence),                    # int
                "idempotency_key": idem, "rail": "insclaim"}
    if 400 <= int(status_code) < 500:                        # definite bad request → no filing
        msg = (resp.get("error", {}) or {}).get("message") if isinstance(resp.get("error"), dict) \
            else resp.get("error")
        msg = msg or resp.get("message") or f"http {status_code}"
        raise executor.ExecError(f"insclaim: rejected — {msg}")  # definite no-effect (FAILED)
    # 5xx / anything else after submission — we can't observe whether it filed.
    raise executor.Ambiguous(f"insclaim: unexpected response (status {status_code}) — outcome unknown")


def find_claim(weave, idempotency_key: str):
    """A prior SUCCEEDED claim receipt for this idempotency key, or None. This is the
    rail-level dedupe (mirrors `esign.find_envelope` / `payments.find_payment`): the
    kernel's per-INVOKE nonce changes every call, so two logical re-tries would each FILE;
    matching on the caller's idempotency key makes a replay a no-op — no duplicate claim."""
    key = nfc(str(idempotency_key))
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == LEGAL
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "insclaim", endpoint: str, transport=None) -> str:
    """Register a REAL insurance-claim filing effect and grant Decima a LEGAL capability to
    run it: a hard `budget` cap (max claims filed), Morta `requires_approval` (filing a claim
    needs approval), and a sandbox profile pinned to this rail (egress to the carrier only).

    The handler does RAIL-LEVEL idempotency first: a prior SUCCEEDED receipt for the same
    idempotency key returns WITHOUT a second filing (mirrors `esign.find_envelope` /
    `payments.find_payment`) — no duplicate claim. Otherwise it asks the CRED1 broker to
    apply the carrier key (`use_secret`) — the key never leaves the broker — and injects the
    configured `endpoint`. A broker denial (revoked / unauthorized handle) raises ExecError →
    a FAILED receipt. Returns the capability id."""
    # The provider-facing fields to carry forward on an idempotent replay (the kernel
    # re-stamps of/executor/idempotency/effect_class itself, so we pass only the payload).
    _CARRY = ("out", "provider_ref", "provider_status", "claimed_amount", "policy",
              "incident_date", "evidence", "idempotency_key", "rail")

    def handler(_impl, args):
        # RAIL-LEVEL idempotency: never file a second claim for a key already SUCCEEDED.
        existing = find_claim(k.weave(), args.get("idempotency_key"))
        if existing is not None:
            prior = existing.content
            return {**{f: prior.get(f) for f in _CARRY if f in prior},
                    "idempotent_replay": True, "prior_receipt": existing.id}
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: file_claim(key, {**args, "endpoint": endpoint}, transport=transport))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"insclaim: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": LEGAL,
        "budget": int(cap),                                  # hard cap on claims filed
        "requires_approval": True,                           # Morta gate — filing needs approval
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
