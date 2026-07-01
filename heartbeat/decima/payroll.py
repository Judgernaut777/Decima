"""Real payroll rail — running payroll, the money-OUT effect with a compliance tail.

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — recreating payroll is itself the liability. A
payroll provider (Gusto / ADP style) is just an HTTPS API, so the real engine is
reachable over stdlib `urllib` with **zero pip dependencies**: real engine, still
pure-stdlib.

Running payroll is money OUT to *many* employees at once AND a tax / compliance
liability, so it composes the SAME spine PAY1 already enforces — it registers a
FINANCIAL, Morta-gated, spend-capped, idempotent effect via `kernel.integrate_tool`.
Unlike a single charge/payout, a payroll run is a *batch*: a pay-period reference and a
list of per-employee line items (each an integer amount in minor units). The batch
carries its own arithmetic liability, so the TOTAL MUST RECONCILE — the sum of the line
amounts must equal the declared `total_amount` BEFORE any request is made, or the run is
refused with no effect. The receipt maps the provider's outcome to WEFT §8 status:
  - a submitted / processed payroll run → SUCCEEDED, carrying the provider `provider_ref`
                                          (the payroll run id), the total, and headcount;
  - a definite 4xx (insufficient funds  → FAILED (no money moved to any employee);
    / invalid employee)
  - a network error / timeout           → UNKNOWN (we cannot observe whether the run
                                          committed — never fabricated as success or
                                          failure, FOLD §11 #8).

GUARDRAILS (see the dependency-policy memory):
  - **TEST MODE ONLY** in the reference — `run_payroll` refuses any key that is not
    `sk_test_…` (a live key raises before any request). The prefix MIRRORS Stripe's
    test-key convention (`sk_test_`) so the whole engine family shares one guard, and no
    real payroll can ever run from the reference.
  - **Totals reconcile** — sum(line amounts) == total_amount is checked before the first
    byte leaves the box; a mismatch is a definite no-effect (FAILED), never a request.
  - **Credentials via CRED1** — the provider key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Transport seam** — `run_payroll` takes a `transport(url, headers, body) -> (status,
    json)`. The default is a real `urllib` POST; tests inject a fake transport, so the
    offline oracle exercises the full contract with NO network.
  - **HTTPS-only** and **ints only** in signed content (every amount is minor units).

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
PAYROLL_URL = "https://api.gusto.com/v1/payrolls"      # Gusto/ADP-style HTTPS payroll engine
# Mirror Stripe's test-key convention: the reference refuses anything but a test key, so
# the whole real-engine family (charge / payout / payroll) shares ONE test-mode guard.
TEST_PREFIX = "sk_test_"
_OK_STATUSES = ("submitted", "processing", "processed", "paid", "created")


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `run_payroll` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level failure
    (DNS, timeout, TLS) raises — `run_payroll` maps that to UNKNOWN. Never used by the
    offline oracle (tests inject a fake transport)."""
    import urllib.request
    import urllib.error
    if not url.startswith("https://"):                        # HTTPS-only — payroll never over cleartext
        raise executor.ExecError("payroll: refusing a non-HTTPS endpoint")
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry a JSON body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": {"message": f"http {e.code}"}}


def _validate_lines(lines) -> int:
    """Return the summed total of the employee line items (minor units), raising
    `executor.ExecError` (a definite no-effect) if any line is malformed. Ints only —
    a float / bool / non-positive amount is a bad request, never a request."""
    if not isinstance(lines, (list, tuple)) or len(lines) == 0:
        raise executor.ExecError("payroll: at least one employee line item is required")
    total = 0
    for li in lines:
        amt = li.get("amount") if isinstance(li, dict) else None
        if not isinstance(amt, int) or isinstance(amt, bool) or amt <= 0:
            raise executor.ExecError(
                "payroll: each line amount must be a positive integer (minor units)")
        total += amt
    return total


def run_payroll(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Run a payroll batch via the provider, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (bad request / totals do
    not reconcile / insufficient funds / invalid employee → FAILED, no money moved) and
    `executor.Ambiguous` for an unobservable outcome (network/unexpected → UNKNOWN). On
    success returns the output dict spread into a SUCCEEDED receipt, carrying the payroll
    run id (`provider_ref`), the reconciled `total_amount`, and the `headcount`.

    TEST-MODE INVARIANT: a non-`sk_test_` key is refused before any request is made — no
    real payroll can ever run from the reference.
    RECONCILIATION INVARIANT: sum(line amounts) == total_amount is checked before any
    request; a mismatch is refused with no effect."""
    transport = transport or _urllib_transport
    if test_mode and not str(secret_key).startswith(TEST_PREFIX):
        # Refuse to run real payroll from the reference. Fail closed, no request.
        raise executor.ExecError("payroll: refusing a non-test key (reference is TEST-MODE ONLY)")

    lines = args.get("lines")
    summed = _validate_lines(lines)                          # ints-only; raises FAILED on a bad line
    headcount = len(lines)

    # `total_amount` is authoritative; when driven amount→cost→cap the caller may only
    # carry `amount` (the batch total), so accept either — both are the same integer.
    total_amount = args.get("total_amount")
    if total_amount is None:
        total_amount = args.get("amount")
    if not isinstance(total_amount, int) or isinstance(total_amount, bool) or total_amount <= 0:
        raise executor.ExecError("payroll: total_amount must be a positive integer (minor units)")
    if summed != total_amount:                              # RECONCILE before any byte leaves the box
        raise executor.ExecError(
            f"payroll: totals do not reconcile (sum(lines)={summed} != total_amount={total_amount})")

    period = nfc(str(args.get("payee") or args.get("pay_period") or ""))
    if not period:
        raise executor.ExecError("payroll: a pay-period reference is required")
    idem = str(args.get("idempotency_key") or "")
    currency = str(args.get("currency", "usd"))

    body = urlencode({
        "pay_period": period, "total_amount": total_amount, "headcount": headcount,
        "currency": currency,
        "line_items": json.dumps([{"amount": int(li["amount"])} for li in lines]),
    })
    headers = {
        "Authorization": f"Bearer {secret_key}",               # applied here, never returned
        "Idempotency-Key": idem,                               # provider-level no-double-run
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        status_code, resp = transport(PAYROLL_URL, headers, body)
    except Exception as e:                                     # network/timeout — unobservable
        raise executor.Ambiguous(f"payroll: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"payroll: unparseable response (status {status_code})")
    if status_code == 200 and resp.get("status") in _OK_STATUSES:
        return {"out": f"ran payroll of {total_amount} {currency} for {headcount} "
                       f"employees ({period})",
                "amount": total_amount, "total_amount": total_amount, "headcount": headcount,
                "payee": period, "currency": currency, "idempotency_key": idem,
                "provider_ref": resp.get("id"), "provider_status": resp.get("status"),
                "rail": "payroll"}
    if resp.get("error"):                                     # definite no-effect (funds / invalid employee)
        msg = (resp.get("error", {}) or {}).get("message") or f"http {status_code}"
        raise executor.ExecError(f"payroll: rejected — {msg}")
    raise executor.Ambiguous(f"payroll: unexpected response (status {status_code}) — outcome unknown")


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "payroll", endpoint: str = PAYROLL_URL,
                 transport=None, test_mode: bool = True) -> str:
    """Register a REAL payroll effect (money OUT to many employees + a compliance tail)
    and grant Decima a FINANCIAL capability to run it. Same caveats as the PAY1 stub rail
    (hard running spend cap over TOTAL payroll spend, Morta `requires_approval`, sandbox
    pinned to the rail host), so `payments.pay(k, agent, <cap>, amount=<total_amount>,
    payee=<pay-period ref>, idempotency_key=…)` is args-compatible and drives the money
    spine unchanged (amount→cost drives the running spend cap; the per-employee line items
    ride `args["lines"]`). On each invoke the handler asks the CRED1 broker to apply the
    provider key (`use_secret`) — the key never leaves the broker. Returns the capability
    id."""
    def handler(_impl, args):
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: run_payroll(key, args, transport=transport,
                                                      test_mode=test_mode))
        if "denied" in r:                                     # handle revoked / unauthorized
            raise executor.ExecError(f"payroll: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                   # hard running spend cap (total payroll)
        "requires_approval": True,                            # Morta gate
        "sandbox": {"effects": [name], "network": True, "endpoint": endpoint},  # egress pinned to the rail
    }
    return k.integrate_tool(name, handler, caveats=caveats)
