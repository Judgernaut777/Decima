"""Real sales-tax engine — WRAP the provider, never roll your own tax (dependency policy).

Decima's policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals
WRAP THE REAL ENGINE rather than reimplement it — recreating tax logic is itself the
liability (rates, nexus, product taxability, and jurisdiction rules change constantly
and are legally binding). TAX1 (`tax.py`) stays an ADVISORY estimator over the ledger
with a stub schedule; this module COMPLEMENTS it by asking a REAL tax provider (a
TaxJar/Avalara-style HTTPS API) to compute the tax on an actual taxable transaction.
The provider is just an HTTPS API, so the real engine rides stdlib `urllib` with ZERO
pip dependencies: real engine, still pure-stdlib.

GUARDRAILS (mirroring the Stripe rail / OIDC engine):
  - **HTTPS-only** — `calculate` refuses to send the API key to a non-`https://`
    endpoint before any request is made (never leak the key in cleartext).
  - **key via CRED1** — the provider API key lives in the secrets broker; `quote` calls
    `broker.use_secret`, which applies the key INSIDE the broker (never returned, never
    logged, never on the Weft). The raw key never appears in a `tax_quote` cell or audit.
  - **fail closed** — a provider 4xx / declared error, an unreachable endpoint, or a
    denied credential records NO `tax_quote` cell and returns `{"denied": reason}`.
  - **ints only in signed content** — amounts are ints in minor units, rates are ints in
    basis points (1% == 100 bps); no float ever enters a value that lands on the Weft.
  - **transport seam** — `calculate` takes a `transport(url, headers, body) -> (status,
    json)`; the default is a real `urllib` POST; tests inject a fake, so the offline
    oracle exercises the full contract with NO network.

Composes public secrets / model / kernel APIs only. No core edit; does not touch tax.py.
"""
import json

from decima.model import assert_content
from decima.hashing import content_id

TAX_QUOTE = "tax_quote"       # the on-Weft record of a provider-computed tax (no key)
BPS = 10_000                  # basis-point denominator (100% == 10000 bps)


class TaxEngineError(Exception):
    """A tax-engine failure — no `tax_quote` may be recorded (fail closed). Covers a
    non-HTTPS endpoint, an unreachable/timed-out endpoint, and a provider 4xx/error."""


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
        "tax_engine", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def _require_int(name: str, v):
    """Guard that a value the engine will fold / sign is an int (never a float/bool)."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise TaxEngineError(f"{name} must be an int (minor units / basis points), got {v!r}")
    return int(v)


def calculate(secret_key: str, request: dict, *, transport=None) -> dict:
    """Compute the tax on a taxable transaction by asking the REAL provider.

    `request` describes the transaction — `endpoint` (the provider's HTTPS tax URL),
    `amount` (int, minor units), `currency`, `from`/`to` jurisdictions, and `line_items`.
    POSTs it over stdlib `urllib` and returns the provider's answer:
    {tax_amount:int, rate_bps:int, jurisdiction, provider_ref, breakdown}. Amounts are
    ints in minor units and rates are ints in basis points (no floats).

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the wire.
    Raises `TaxEngineError` on a non-HTTPS endpoint, an unreachable endpoint, or a
    definite provider error (4xx / error body) — the caller (`quote`) fails closed."""
    transport = transport or _urllib_transport

    endpoint = str(request.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the API key on the wire in cleartext. Refuse before sending.
        raise TaxEngineError("refusing to send the API key to a non-HTTPS tax endpoint")

    amount = request.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise TaxEngineError("amount must be a non-negative integer (minor units)")
    currency = str(request.get("currency", "usd"))

    payload = {
        "amount": int(amount),
        "currency": currency,
        "from": request.get("from"),
        "to": request.get("to"),
        "line_items": request.get("line_items", []),
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
        raise TaxEngineError(f"tax endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise TaxEngineError(f"unparseable tax response (status {status})")
    if status == 200 and "tax_amount" in resp:
        # The provider's answer is the liability-bearing computation — fold it as ints.
        return {
            "tax_amount": _require_int("tax_amount", resp.get("tax_amount")),
            "rate_bps": _require_int("rate_bps", resp.get("rate_bps")),
            "jurisdiction": resp.get("jurisdiction"),
            "provider_ref": resp.get("provider_ref") or resp.get("id"),
            "breakdown": resp.get("breakdown", []),
        }
    err = resp.get("error_description") or resp.get("error") or f"http {status}"
    raise TaxEngineError(f"provider rejected the tax request: {err}")   # definite error


def quote(k, *, endpoint: str, request: dict, credential_handle: str, broker,
          agent_cell, transport=None) -> dict:
    """Get a REAL provider tax quote and record it on the Weft (fail closed).

    Resolves the provider API key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `calculate` on `request` against the
    HTTPS `endpoint`, and on success asserts a `tax_quote` cell carrying
    amount/tax_amount/rate_bps/jurisdiction/provider_ref (all money as int minor units,
    rate as int bps — NEVER the key). Returns
    {tax_quote: <cell id>, tax_amount, rate_bps, provider_ref}.

    On a denied credential (revoked/unauthorized/over-budget) or any engine error
    (non-HTTPS, unreachable, provider 4xx) it records NO cell and returns
    {"denied": reason}."""
    req = {**request, "endpoint": endpoint}
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: calculate(key, req, transport=transport))
    except TaxEngineError as e:
        return {"denied": f"tax_engine: {e}"}                # fail closed — no tax cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    content = {
        "amount": _require_int("amount", req.get("amount")),
        "currency": str(req.get("currency", "usd")),
        "tax_amount": _require_int("tax_amount", result["tax_amount"]),
        "rate_bps": _require_int("rate_bps", result["rate_bps"]),
        "jurisdiction": result.get("jurisdiction"),
        "provider_ref": result.get("provider_ref"),
        "breakdown": result.get("breakdown", []),
    }
    # Content-addressed by the quote body (re-quoting identical inputs is idempotent and
    # a quote keeps one identity on the Log).
    cid = content_id({"tax_quote": content})
    assert_content(k.weft, k.decima_agent_id, cid, TAX_QUOTE, content)
    return {
        "tax_quote": cid,
        "tax_amount": content["tax_amount"],
        "rate_bps": content["rate_bps"],
        "provider_ref": content["provider_ref"],
    }


def quotes(k) -> list:
    """All folded `tax_quote` cells on the Weft."""
    return list(k.weave().of_type(TAX_QUOTE))
