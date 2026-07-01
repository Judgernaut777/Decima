"""Real bank account-aggregation engine — WRAP the provider (dependency policy).

Decima's policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals
WRAP THE REAL ENGINE rather than reimplement it. Reaching into someone's financial life
— their real account balances and transactions — is exactly such a liability: there is
no "stub bank" that is meaningful, and mishandling the access token or the data is real
harm. A Plaid/Finicity/MX-style account-aggregation provider is just an HTTPS API, so the
real engine rides stdlib `urllib` with ZERO pip dependencies: real engine, still
pure-stdlib. This lane is READ-oriented (it fetches balances/transactions) but SENSITIVE.

GUARDRAILS (mirroring tax_engine.py / stripe_rail.py):
  - **HTTPS-only** — `fetch_accounts` refuses to send the access token / API key to a
    non-`https://` endpoint BEFORE any request is made (never leak a credential in
    cleartext).
  - **key via CRED1** — the provider key lives in the secrets broker; `snapshot` calls
    `broker.use_secret`, which applies the key INSIDE the broker (never returned, never
    logged, never on the Weft). The raw key never appears in a `bank_snapshot` cell.
  - **balances are INT minor units (cents)** — no float ever enters a value that lands on
    the Weft; a non-int balance from the provider fails closed.
  - **account data is sensitive DATA, never an instruction** — the recorded cell is
    stamped `instruction_eligible=False` (an aggregated bank balance is DATA to be
    recalled, never text to be obeyed) and marked `sensitive=True`.
  - **fail closed** — a provider 4xx / declared error, an unreachable/timed-out endpoint,
    or a denied credential records NO `bank_snapshot` cell and returns {"denied": reason}.
  - **transport seam** — `fetch_accounts` takes a `transport(url, headers, body) ->
    (status, json)`; the default is a real `urllib` POST; tests inject a fake, so the
    offline oracle exercises the full contract with NO network.

Composes public secrets / model / manifest / kernel APIs only. No core edit.
"""
import json

from decima.model import assert_content
from decima.hashing import content_id
from decima import manifest as _manifest

BANK_SNAPSHOT = "bank_snapshot"      # the on-Weft record of a provider account snapshot (no key)


class BankingError(Exception):
    """A banking-engine failure — no `bank_snapshot` may be recorded (fail closed).
    Covers a non-HTTPS endpoint, an unreachable/timed-out endpoint, and a provider
    4xx/error body."""


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `fetch_accounts` decides success vs. definite error. A transport-level
    failure (DNS, timeout, TLS) raises — `fetch_accounts` maps that to a BankingError
    (unreachable). Never used by the offline oracle (tests inject a fake transport)."""
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
            return e.code, {"error": f"http {e.code}"}


def _require_int(name: str, v):
    """Guard that a balance the engine will fold / sign is an int minor unit (never a
    float/bool). A provider that returns a float balance is a contract violation — fail
    closed rather than let a float onto the Weft."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise BankingError(f"{name} must be an int (minor units / cents), got {v!r}")
    return int(v)


def fetch_accounts(secret_key: str, request: dict, *, transport=None) -> dict:
    """Fetch a person's bank account balances from the REAL aggregation provider.

    `request` carries what the provider needs to identify the linked item — `endpoint`
    (the provider's HTTPS accounts URL), plus an `access_token`/`item_id` (the item that
    was linked out-of-band). POSTs it over stdlib `urllib` and returns the provider's
    answer normalized to:
        {accounts: [{account_ref, name, type, balance_cents:int, currency}],
         provider_ref}
    Balances are ints in minor units (cents) — no floats.

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the credential touches the
    wire. Raises `BankingError` on a non-HTTPS endpoint, an unreachable endpoint, or a
    definite provider error (4xx / error body) — the caller (`snapshot`) fails closed."""
    transport = transport or _urllib_transport

    endpoint = str(request.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the access token / API key on the wire in cleartext. Refuse first.
        raise BankingError("refusing to send the bank access token to a non-HTTPS endpoint")

    payload = {
        "access_token": request.get("access_token"),
        "item_id": request.get("item_id"),
        "options": request.get("options", {}),
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
        raise BankingError(f"bank endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise BankingError(f"unparseable bank response (status {status})")
    if status == 200 and "accounts" in resp:
        raw = resp.get("accounts")
        if not isinstance(raw, list):
            raise BankingError("provider returned a non-list of accounts")
        accounts = []
        for a in raw:
            if not isinstance(a, dict):
                raise BankingError("provider returned a non-dict account")
            accounts.append({
                "account_ref": a.get("account_ref") or a.get("account_id") or a.get("id"),
                "name": a.get("name"),
                "type": a.get("type"),
                # the liability-bearing datum — a real balance — folded as int cents.
                "balance_cents": _require_int("balance_cents", a.get("balance_cents")),
                "currency": str(a.get("currency", "usd")),
            })
        return {
            "accounts": accounts,
            "provider_ref": resp.get("provider_ref") or resp.get("request_id") or resp.get("id"),
        }
    err = resp.get("error_description") or resp.get("error_message") or resp.get("error") \
        or f"http {status}"
    raise BankingError(f"provider rejected the bank request: {err}")     # definite error


def snapshot(k, *, endpoint: str, request: dict, credential_handle: str, broker,
             agent_cell, transport=None) -> dict:
    """Fetch a REAL account snapshot and record it on the Weft as sensitive DATA (fail
    closed).

    Resolves the provider key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `fetch_accounts` on `request` against
    the HTTPS `endpoint`, and on success asserts a `bank_snapshot` cell carrying the
    accounts (each with an int `balance_cents`) and the `provider_ref` — NEVER the key,
    NEVER the access token. The cell is stamped `instruction_eligible=False` and
    `sensitive=True`: an aggregated balance is DATA to be recalled, never text to be
    obeyed. Returns {bank_snapshot: <cell id>, total_cents:int, provider_ref}.

    On a denied credential (revoked/unauthorized/over-budget) or any engine error
    (non-HTTPS, unreachable, provider 4xx) it records NO cell and returns
    {"denied": reason}."""
    req = {**request, "endpoint": endpoint}
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: fetch_accounts(key, req, transport=transport))
    except BankingError as e:
        return {"denied": f"banking: {e}"}                   # fail closed — no snapshot cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    # Re-guard every balance is an int as it lands on the Weft, and total them (ints).
    accounts = []
    total_cents = 0
    for a in result["accounts"]:
        bal = _require_int("balance_cents", a["balance_cents"])
        total_cents += bal
        accounts.append({
            "account_ref": a.get("account_ref"),
            "name": a.get("name"),
            "type": a.get("type"),
            "balance_cents": bal,
            "currency": str(a.get("currency", "usd")),
        })

    content = {
        "accounts": accounts,
        "total_cents": int(total_cents),
        "provider_ref": result.get("provider_ref"),
        "instruction_eligible": False,   # aggregated bank data is DATA, never an instruction
        "sensitive": True,               # someone's real financial data — handle as sensitive
    }
    # Content-addressed by the snapshot body (re-snapshotting identical inputs is
    # idempotent and a snapshot keeps one identity on the Log).
    cid = content_id({"bank_snapshot": content})
    assert_content(k.weft, k.decima_agent_id, cid, BANK_SNAPSHOT, content)
    return {
        "bank_snapshot": cid,
        "total_cents": content["total_cents"],
        "provider_ref": content["provider_ref"],
    }


def snapshots(k) -> list:
    """All folded `bank_snapshot` cells on the Weft."""
    return list(k.weave().of_type(BANK_SNAPSHOT))


def register_manifest(k) -> str:
    """Register a discoverable manifest for this engine so `manifest.find`/`registry`
    can surface it for a real financial-data goal before forging a new capability.
    Registration confers NO authority (manifest.py, Law) — it is a description."""
    m = _manifest.capability_manifest(
        "banking", archetype="COMPUTE", effect_class="FINANCIAL_DATA",
        description="fetch bank account balances and transactions (account aggregation)",
        tags=["finance", "bank", "plaid"], source="builtin")
    return _manifest.register(k, m)
