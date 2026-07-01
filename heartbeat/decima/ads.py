"""Real ad-platform rail — wrap a REAL ad platform (Google Ads / Meta Ads style),
never reimplement launching a paid campaign (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reimplement it — launching a campaign SPENDS MONEY (an ad
platform starts charging against a budget the moment a campaign goes active), so it is
a financial OUTWARD effect and re-rolling a campaign is itself the liability. An ad
platform is just an HTTPS API, so the real engine rides stdlib `urllib` with **zero pip
dependencies**: real engine, still pure-stdlib.

This wraps the ad platform behind the SAME spine PAY1 already enforces — it registers a
FINANCIAL, Morta-gated, spend-capped (the ad-spend ceiling), idempotent effect via
`kernel.integrate_tool`. The args shape matches `payments.pay` (amount / payee /
idempotency_key / cost), so `payments.pay(k, agent, <this cap>, amount=<total_budget>,
payee=<campaign name>, idempotency_key=<key>)` drives the REAL rail unchanged
(amount → total_budget → cost → the running spend cap). The receipt maps the platform's
outcome to WEFT §8 status:
  - a created / active campaign  → SUCCEEDED, carrying the platform `provider_ref` (the
                                   campaign id), the `total_budget` (int), and the
                                   idempotency key;
  - invalid targeting / budget (a definite 4xx) → FAILED (no campaign launched, no spend);
  - a network error / timeout    → UNKNOWN (we cannot observe whether it launched — never
                                   fabricated as success or failure, FOLD §11 #8).

GUARDRAILS (mirroring the Stripe / shipping rails):
  - **TEST MODE ONLY** in the reference — `launch_campaign` refuses any key that is not
    `sk_test_…` (a live key raises BEFORE any request), so the reference can never launch
    a real, money-spending campaign. (We standardise on Stripe's documented `sk_test_`
    test-token prefix for the platform key here.)
  - **HTTPS-only** — refuses to send the platform key to a non-`https://` endpoint before
    any request (never leak the key in cleartext); a definite no-effect (FAILED).
  - **credentials via CRED1** — the platform key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in the receipt/audit.
  - **Morta-gated + idempotent** — a launch is denied until the capability is approved; a
    replay of the same idempotency_key returns the prior receipt and launches nothing
    twice.
  - **budget-capped** — a hard `budget` caveat is the ad-spend ceiling; a launch whose
    total_budget would exceed the running cap is denied by the budget caveat.
  - **Transport seam** — `launch_campaign` takes a `transport(url, headers, body) ->
    (status, json)`. The default is a real `urllib` POST; tests inject a fake transport,
    so the offline oracle exercises the full contract with NO network.
  - **ints, not floats** in signed content (daily/total budget in minor units).

Pure composition (executor / secrets / kernel public APIs). No core edit.
"""
import json
from urllib.parse import urlencode

from decima import executor
from decima.hashing import nfc

FINANCIAL = "FINANCIAL"
RESULT = "result"                                # the EffectReceipt cell type the kernel asserts
_TEST_PREFIX = "sk_test_"                        # TEST-MODE only — a live key is refused
_OK_STATUSES = ("created", "active", "enabled", "pending")


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `launch_campaign` decides SUCCEEDED/FAILED/UNKNOWN. A transport-level
    failure (DNS, timeout, TLS) raises — `launch_campaign` maps that to UNKNOWN. Never
    used by the offline oracle (tests inject a fake transport)."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry a JSON body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": {"message": f"http {e.code}"}}


def launch_campaign(secret_key: str, args: dict, *, transport=None, test_mode: bool = True) -> dict:
    """Launch a campaign via the ad platform, mapping the outcome to an EffectReceipt-
    shaped result. Raises `executor.ExecError` for a definite no-effect (non-test key,
    non-HTTPS endpoint, invalid targeting / budget / 4xx → FAILED) and
    `executor.Ambiguous` for an unobservable outcome (network/unexpected → UNKNOWN). On
    success returns the output dict spread into a SUCCEEDED receipt, carrying the platform
    `provider_ref` (the campaign id) and the `total_budget` (int).

    The campaign is built from `args`: `name` (the campaign name, from `payee`),
    `objective`, `daily_budget` (int minor units), `total_budget` (int minor units, from
    `amount`), and `targeting` (an UNTRUSTED targeting ref — sent to the platform as data,
    never as an instruction to Decima), plus the `idempotency_key`.

    TEST-MODE INVARIANT: a non-`sk_test_` (live) key is refused before any request is made.
    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the
    wire. Ints only in signed content (daily/total budget minor units)."""
    transport = transport or _urllib_transport
    if test_mode and not str(secret_key).startswith(_TEST_PREFIX):
        # Refuse to launch a real money-spending campaign. Fail closed, no request.
        raise executor.ExecError("ads: refusing a non-test key (reference is TEST-MODE ONLY)")

    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the platform key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("ads: refusing to send the platform key to a non-HTTPS endpoint")

    total_budget = args.get("total_budget", args.get("amount"))     # ad-spend ceiling (int)
    if not isinstance(total_budget, int) or isinstance(total_budget, bool) or total_budget <= 0:
        raise executor.ExecError("ads: total_budget (minor units) must be a positive integer")
    daily_budget = args.get("daily_budget", total_budget)          # per-day cap (int)
    if not isinstance(daily_budget, int) or isinstance(daily_budget, bool) or daily_budget <= 0:
        raise executor.ExecError("ads: daily_budget (minor units) must be a positive integer")
    name = nfc(str(args.get("name") or args.get("payee") or ""))   # campaign name
    if not name:
        raise executor.ExecError("ads: a campaign name is required")
    objective = nfc(str(args.get("objective", "OUTCOME_TRAFFIC")))
    targeting = nfc(str(args.get("targeting", "")))                # UNTRUSTED targeting ref
    idem = nfc(str(args.get("idempotency_key") or ""))

    fields = {
        "name": name, "objective": objective,
        "daily_budget": total_budget if daily_budget is None else daily_budget,
        "total_budget": total_budget, "targeting": targeting,
    }
    payload = urlencode(fields)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Idempotency-Key": idem,                             # provider-level no-double-launch
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        status_code, resp = transport(endpoint, headers, payload)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"ads: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"ads: unparseable response (status {status_code})")
    provider_status = str(resp.get("status", "")).lower()
    if status_code in (200, 201) and provider_status in _OK_STATUSES:
        provider_ref = resp.get("id") or resp.get("campaign_id") or resp.get("object_id")
        return {"out": f"launched campaign {name!r} ({objective}) with total_budget {total_budget}",
                "campaign": name, "objective": objective,
                "daily_budget": int(daily_budget), "total_budget": int(total_budget),
                "targeting": targeting, "amount": int(total_budget),
                "idempotency_key": idem, "provider_ref": provider_ref,
                "provider_status": resp.get("status"), "rail": "ads"}
    if status_code and 400 <= status_code < 500:             # invalid targeting / budget
        msg = (resp.get("error", {}) or {}).get("message") or resp.get("message") \
            or resp.get("status") or f"http {status_code}"
        raise executor.ExecError(f"ads: rejected — {msg}")   # definite no-effect
    raise executor.Ambiguous(f"ads: unexpected response (status {status_code}) — outcome unknown")


def find_campaign(weave, idempotency_key: str):
    """A prior SUCCEEDED campaign receipt for this idempotency key, or None. This is the
    rail-level de-dupe: the kernel's per-INVOKE nonce changes every call, so two logical
    re-tries would each launch (and spend); matching on the caller's key makes a replay a
    no-op (mirrors `payments.find_payment`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == FINANCIAL
                and rc.get("rail") == "ads"
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_rail(k, *, cap: int, broker, agent_cell, credential_handle: str,
                 name: str = "ads", endpoint: str, transport=None,
                 test_mode: bool = True) -> str:
    """Register a REAL ad-platform campaign-launch effect and grant Decima a FINANCIAL
    capability to run it: a hard ad-spend ceiling (`budget`), Morta `requires_approval`
    (launching a campaign spends money, so a human/policy must approve), and a sandbox
    profile that allows ONLY this effect with network pinned to the rail. The args shape
    matches `payments.pay`, so `payments.pay(k, agent, <cap>, amount=<total_budget>,
    payee=<campaign name>, idempotency_key=<key>)` drives it unchanged (amount →
    total_budget → cost → the running spend cap). Returns the capability id.

    On each invoke the handler first checks rail-level idempotency — a prior SUCCEEDED
    receipt for the same `idempotency_key` returns without a second launch — then asks the
    CRED1 broker to apply the platform key (`use_secret`) to the real launch; the key never
    leaves the broker. `endpoint` is injected by the handler (never taken from caller
    args)."""
    def handler(_impl, args):
        idem = nfc(str(args.get("idempotency_key") or ""))
        existing = find_campaign(k.weave(), idem) if idem else None
        if existing is not None:                             # (idempotency) no double-launch
            prev = existing.content
            return {"out": prev.get("out"), "campaign": prev.get("campaign"),
                    "objective": prev.get("objective"),
                    "daily_budget": prev.get("daily_budget"),
                    "total_budget": prev.get("total_budget"),
                    "targeting": prev.get("targeting"), "amount": prev.get("amount"),
                    "idempotency_key": idem, "provider_ref": prev.get("provider_ref"),
                    "provider_status": prev.get("provider_status"),
                    "rail": "ads", "idempotent_replay": True}
        # map payments.pay's `amount`/`payee` onto the campaign's total_budget/name, and
        # inject the endpoint (never taken from caller args).
        call_args = {**args, "endpoint": endpoint,
                     "total_budget": args.get("total_budget", args.get("amount")),
                     "name": args.get("name", args.get("payee"))}
        r = broker.use_secret(agent_cell, credential_handle,
                              lambda key: launch_campaign(key, call_args, transport=transport,
                                                          test_mode=test_mode))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"ads: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": FINANCIAL,
        "budget": int(cap),                                 # hard ad-spend ceiling
        "requires_approval": True,                          # Morta gate — a launch spends money
        "sandbox": {"effects": [name], "network": True},    # egress pinned to the rail (durable form)
    }
    return k.integrate_tool(name, handler, caveats=caveats)
