"""Real cloud object-storage engine — WRAP the provider, gated PUT + READ GET (dependency policy).

Policy: recreate the design in pure stdlib, but for HIGH-LIABILITY externals WRAP THE
REAL ENGINE rather than reinvent it. Durable object storage (S3 / GCS / R2 / B2 style) is
such an external — putting a blob into someone's bucket is an OUTWARD, durable effect on a
store other systems trust; re-rolling that store is itself the liability. Object storage is
just an HTTPS API, so the real engine rides stdlib `urllib` with **zero pip dependencies**:
real engine, still pure-stdlib.

This complements the content-addressed reference recorder (`cloud_storage.py`, which lands a
`stored_object` reference cell): THIS module wraps the provider behind the SAME gated spine
the payment / CRM rails enforce, so a PUT is a Morta-gated, budget-capped, IDEMPOTENT effect
via `kernel.integrate_tool`, and a GET is a READ effect that needs no approval. The PUT
receipt maps the provider's outcome to WEFT §8 status:
  - a stored object (200/201)  → SUCCEEDED, carrying the ETag as `provider_ref`, the object
                                 key, the byte size (int), the content hash, and the
                                 idempotency (bucket/key/hash) it was stored under;
  - access denied / bad bucket / integrity mismatch (4xx / checksum ≠ local hash)
                               → FAILED (nothing durable was written);
  - a network error / timeout  → UNKNOWN (we cannot observe whether the object landed —
                                 never fabricated as success or failure, FOLD §11 #8).

GUARDRAILS (mirroring the Stripe / CRM rails):
  - **HTTPS-only** — a non-`https://` endpoint is refused BEFORE the key touches the wire
    (never leak the key in cleartext); a definite no-effect (FAILED).
  - **TEST/SANDBOX mode** in the reference — `put_object` refuses any key that is not a
    `sandbox-…` key (a production key raises before any request), so the reference can never
    write to a real production bucket.
  - **credentials via CRED1** — the provider key lives in the secrets broker; the handler
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in a receipt/audit.
  - **content-addressed integrity** — a local content hash (`hashing.blob_id`) is computed
    BEFORE upload; if the provider returns its own checksum it MUST equal the local hash —
    a mismatch fails closed (FAILED, corruption caught). The ETag is the `provider_ref`.
  - **Morta-gated + idempotent by object key** — a PUT is denied until the capability is
    approved; a replay of the same bucket/key/content returns the prior receipt and never
    writes (or double-charges the budget) twice.
  - **object BYTES never on the Weft** — the payload is uploaded; the receipt holds only its
    content hash + metadata (size/key/ETag). The raw object bytes never cross onto the Log.
  - **ints, not floats** in signed content (the byte size / object count).
  - **transport seam** — `put_object` / `get_object` take a `transport(url, headers, body)
    -> (status, json)`; the default is a real `urllib` PUT/GET; tests inject a fake, so the
    offline oracle exercises the full contract with NO network.

Pure composition (executor / secrets / hashing / kernel / manifest public APIs). No core edit.
"""
import json
from urllib.parse import quote

from decima import executor
from decima import manifest as M
from decima.hashing import nfc, blob_id

STORAGE = "STORAGE"                       # effect_class — outward object-store WRITE (not FINANCIAL)
READ = "READ"                             # effect_class — an object read (observing is always allowed)
RESULT = "result"                         # the EffectReceipt cell type the kernel asserts
_OK_STATUSES = (200, 201)
_SANDBOX_PREFIX = "sandbox-"             # reference is TEST/SANDBOX-ONLY: keys must be sandbox keys


def _urllib_put(url: str, headers: dict, body):
    """The real PUT transport: a stdlib `urllib` PUT (no pip dep). On success returns
    (status, {etag, version_id, checksum}) parsed from the response HEADERS (S3 returns the
    ETag in a header, not a body). A 4xx/5xx carries an error body (returned, not raised),
    so `put_object` decides SUCCEEDED/FAILED; a transport-level failure (DNS, timeout, TLS)
    raises — `put_object` maps that to UNKNOWN. Never used by the offline oracle (tests
    inject a fake transport)."""
    import urllib.request
    import urllib.error
    data = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            meta = {k.lower(): v for k, v in r.headers.items()}
            return r.status, {
                "etag": (meta.get("etag") or "").strip('"') or None,
                "version_id": meta.get("x-amz-version-id"),
                "checksum": meta.get("x-amz-content-checksum") or meta.get("x-amz-content-digest"),
            }
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry an error body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"http {e.code}"}


def _urllib_get(url: str, headers: dict, _body):
    """The real GET transport: a stdlib `urllib` GET (no pip dep). On success returns
    (status, {body, etag, checksum}); a 4xx/5xx carries an error body (returned, not
    raised); a transport-level failure raises. Never used by the offline oracle."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            meta = {k.lower(): v for k, v in r.headers.items()}
            return r.status, {
                "body": r.read(),
                "etag": (meta.get("etag") or "").strip('"') or None,
                "checksum": meta.get("x-amz-content-checksum") or meta.get("x-amz-content-digest"),
            }
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"http {e.code}"}


def _require_int(name: str, v):
    """Guard that a value the engine will sign onto the Weft is an int (never float/bool)."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise executor.ExecError(f"storage: {name} must be an int (bytes), got {v!r}")
    return int(v)


def _as_bytes(payload) -> bytes:
    """The object payload as bytes — a str is UTF-8 encoded, bytes pass through."""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise executor.ExecError("storage: object payload must be bytes or str")


def stage(blobs: dict, payload) -> str:
    """Content-address `payload` (bytes or str) into the local staging store and return its
    `blob_ref` (the content hash). The RAW BYTES stay in `blobs` — off the Weft — while the
    invoke carries only this hash. Same content → same ref (dedup falls out of addressing)."""
    body = _as_bytes(payload)
    ref = blob_id(body, kind="blob")
    blobs[ref] = body
    return ref


def put_object(secret_key: str, args: dict, *, blobs: dict, transport=None,
               sandbox_mode: bool = True) -> dict:
    """Upload one object to the REAL provider, mapping the outcome to an EffectReceipt-shaped
    result. Raises `executor.ExecError` for a definite no-effect (non-HTTPS endpoint,
    non-sandbox key, missing bucket/key, 4xx, or an integrity/checksum mismatch → FAILED,
    nothing durable written) and `executor.Ambiguous` for an unobservable outcome
    (network/unexpected → UNKNOWN). On success returns the output dict spread into a
    SUCCEEDED receipt, carrying the ETag as `provider_ref`, the object key, the byte size
    (int), and the LOCAL content hash.

    `bucket` / `key` are UNTRUSTED DATA (the object's coordinates); the payload rides through
    `blobs` (staged out-of-band) as a `blob_ref` content hash, so the object BYTES never enter
    the invoke args and never land on the Weft — only the hash + metadata are recorded.

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the wire
    (no request is made). SANDBOX INVARIANT: a non-`sandbox-` key is refused before any
    request (the reference never writes a real production bucket)."""
    transport = transport or _urllib_put

    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        # Never put the provider key on the wire in cleartext. Fail closed, no request.
        raise executor.ExecError("storage: refusing to send the key to a non-HTTPS endpoint")
    if sandbox_mode and not str(secret_key).startswith(_SANDBOX_PREFIX):
        # Refuse to write a real production bucket from the reference. Fail closed, no request.
        raise executor.ExecError("storage: refusing a non-sandbox key (reference is SANDBOX-ONLY)")

    bucket = nfc(str(args.get("bucket") or ""))              # UNTRUSTED — object coordinate
    key = nfc(str(args.get("key") or ""))                    # UNTRUSTED — object coordinate
    if not bucket or not key:
        raise executor.ExecError("storage: an object needs a bucket and a key")

    blob_ref = str(args.get("blob_ref") or "")
    if blob_ref not in blobs:
        raise executor.ExecError("storage: unknown blob_ref (stage the payload first)")
    body = _as_bytes(blobs[blob_ref])
    size = len(body)
    content_hash = blob_id(body, kind="blob")                # content-address BEFORE upload
    if content_hash != blob_ref:
        # The staged bytes do not match the reference they were asked for — fail closed.
        raise executor.ExecError("storage: staged content hash does not match blob_ref")
    content_type = str(args.get("content_type") or "application/octet-stream")

    url = f"{endpoint.rstrip('/')}/{quote(bucket)}/{quote(key)}"
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": content_type,
        "Content-Length": str(size),
        "x-amz-content-checksum": content_hash,              # our content commitment
    }
    try:
        status_code, resp = transport(url, headers, body)
    except Exception as e:                                    # network/timeout — unobservable
        raise executor.Ambiguous(f"storage: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"storage: unparseable response (status {status_code})")
    etag = resp.get("etag") or resp.get("version_id") or resp.get("id")
    if status_code in _OK_STATUSES and etag:
        # Integrity: if the provider committed to the content, it MUST match our local hash.
        provider_checksum = resp.get("checksum")
        if provider_checksum is not None and provider_checksum != content_hash:
            raise executor.ExecError(
                f"storage: provider checksum {provider_checksum!r} != local hash "
                f"{content_hash!r} (integrity check failed)")            # definite no-effect
        return {"out": f"stored {bucket}/{key} ({size} bytes)",
                "bucket": bucket, "key": key,
                "size": int(size),                           # int, never a float (§1)
                "content_hash": content_hash,                # LOCAL content hash (verified)
                "provider_ref": str(etag),                   # the ETag / version id
                "content_type": content_type,
                "idempotency_key": f"{bucket}/{key}#{content_hash}",
                "bytes_on_weft": False,                      # only the hash + metadata land
                "rail": "storage"}
    if status_code and 400 <= int(status_code) < 500:        # access denied / bad bucket
        err = resp.get("error") or resp.get("message") or f"http {status_code}"
        raise executor.ExecError(f"storage: rejected — {err}")          # definite no-effect (FAILED)
    # 5xx / anything else after submission — we can't observe whether the object landed.
    raise executor.Ambiguous(f"storage: unexpected response (status {status_code}) — outcome unknown")


def get_object(secret_key: str, args: dict, *, blobs: dict, transport=None) -> dict:
    """Read one object from the REAL provider (a READ effect). Raises `executor.ExecError`
    for a definite failure (non-HTTPS endpoint, missing bucket/key, 4xx/not-found, or a
    content-hash mismatch against an expected hash) and `executor.Ambiguous` for an
    unobservable outcome (network → UNKNOWN). On success the retrieved BYTES are staged into
    `blobs` under their content hash (never onto the Weft); the receipt carries only the
    verified content hash + metadata, with the ETag as `provider_ref`.

    HTTPS INVARIANT: a non-`https://` endpoint is refused before the key is put on the wire."""
    transport = transport or _urllib_get

    endpoint = str(args.get("endpoint") or "")
    if not endpoint.startswith("https://"):
        raise executor.ExecError("storage: refusing to send the key to a non-HTTPS endpoint")

    bucket = nfc(str(args.get("bucket") or ""))
    key = nfc(str(args.get("key") or ""))
    if not bucket or not key:
        raise executor.ExecError("storage: an object needs a bucket and a key")

    url = f"{endpoint.rstrip('/')}/{quote(bucket)}/{quote(key)}"
    headers = {"Authorization": f"Bearer {secret_key}", "Accept": "application/octet-stream"}
    try:
        status_code, resp = transport(url, headers, None)
    except Exception as e:                                    # network — unobservable
        raise executor.Ambiguous(f"storage: transport error, outcome unknown: {e}")

    if not isinstance(resp, dict):
        raise executor.Ambiguous(f"storage: unparseable response (status {status_code})")
    if status_code == 200 and "body" in resp:
        body = _as_bytes(resp.get("body"))
        size = len(body)
        content_hash = blob_id(body, kind="blob")            # verify what we actually received
        expected = args.get("expected_hash")
        if expected is not None and str(expected) != content_hash:
            raise executor.ExecError(
                f"storage: content hash {content_hash!r} != expected {expected!r} "
                f"(integrity check failed)")
        blobs[content_hash] = body                            # stage the read bytes OFF the Weft
        return {"out": f"read {bucket}/{key} ({size} bytes)",
                "bucket": bucket, "key": key,
                "size": int(size),                           # int, never a float
                "content_hash": content_hash,                # verified content hash
                "blob_ref": content_hash,                    # where the staged bytes now live
                "provider_ref": str(resp.get("etag") or ""), # the ETag
                "bytes_on_weft": False,
                "rail": "storage"}
    if status_code and 400 <= int(status_code) < 500:        # not-found / access denied
        err = resp.get("error") or resp.get("message") or f"http {status_code}"
        raise executor.ExecError(f"storage: read rejected — {err}")
    raise executor.Ambiguous(f"storage: unexpected read response (status {status_code}) — outcome unknown")


def find_object(weave, idempotency_key: str):
    """A prior SUCCEEDED storage-PUT receipt for this idempotency key (bucket/key#hash), or
    None. This is the rail-level de-dupe: the kernel's per-INVOKE nonce changes every call,
    so two logical re-tries would each upload; matching on the object coordinates + content
    hash makes a replay a no-op — no duplicate write (mirrors `crm_engine.find_record`)."""
    key = nfc(str(idempotency_key))
    if not key:
        return None
    for c in weave.of_type(RESULT):
        rc = c.content
        if (rc.get("effect_class") == STORAGE
                and rc.get("idempotency_key") == key
                and rc.get("status") == executor.SUCCEEDED):
            return c
    return None


def install_put_rail(k, *, cap: int, broker, agent_cell, credential_handle: str, blobs: dict,
                     name: str = "storage_put", endpoint: str, transport=None,
                     sandbox_mode: bool = True) -> str:
    """Register a REAL object-store PUT effect and grant Decima a STORAGE capability to run
    it: a hard write cap (`budget`), Morta `requires_approval` (a PUT lands a durable object
    other systems trust), and a sandbox profile that allows ONLY this effect with network
    pinned to the rail. Returns the capability id.

    The payload is staged out-of-band in `blobs` (via `stage`) and named by its `blob_ref`
    content hash in the invoke args — the raw bytes never enter the args or the Weft. On each
    invoke the handler first checks rail-level idempotency — a prior SUCCEEDED receipt for the
    same bucket/key/content returns without a second upload — then asks the CRED1 broker to
    apply the provider key (`use_secret`) to the real upload; the key never leaves the broker.
    `endpoint` is injected by the handler (never taken from caller args)."""
    def handler(_impl, args):
        bucket = nfc(str(args.get("bucket") or ""))
        key = nfc(str(args.get("key") or ""))
        blob_ref = str(args.get("blob_ref") or "")
        idem = f"{bucket}/{key}#{blob_ref}"
        existing = find_object(k.weave(), idem) if bucket and key and blob_ref else None
        if existing is not None:                             # (idempotency) no double-write
            prev = existing.content
            return {"out": prev.get("out"), "bucket": prev.get("bucket"),
                    "key": prev.get("key"), "size": prev.get("size"),
                    "content_hash": prev.get("content_hash"),
                    "provider_ref": prev.get("provider_ref"),
                    "content_type": prev.get("content_type"),
                    "idempotency_key": prev.get("idempotency_key"),
                    "bytes_on_weft": False, "rail": "storage",
                    "idempotent_replay": True}
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda skey: put_object(skey, call_args, blobs=blobs, transport=transport,
                                    sandbox_mode=sandbox_mode))
        if "denied" in r:                                    # revoked / unauthorized handle
            raise executor.ExecError(f"storage: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": STORAGE,
        "budget": int(cap),                                  # hard cap on objects written
        "requires_approval": True,                           # Morta gate — a PUT is durable
        "sandbox": {"effects": [name], "network": True},     # egress pinned to the rail
    }
    return k.integrate_tool(name, handler, caveats=caveats)


def install_get_rail(k, *, broker, agent_cell, credential_handle: str, blobs: dict,
                     name: str = "storage_get", endpoint: str, transport=None) -> str:
    """Register a REAL object-store GET effect and grant Decima a READ capability to run it.
    A read observes — no Morta approval, no budget — but it still rides the SAME spine: ocap
    gates who may read, and the receipt records `effect_class = READ`. Retrieved bytes are
    staged into `blobs` (off the Weft). The handler asks the CRED1 broker to apply the
    provider key; the key never leaves the broker. Returns the capability id."""
    def handler(_impl, args):
        call_args = {**args, "endpoint": endpoint}           # endpoint injected by the rail
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda skey: get_object(skey, call_args, blobs=blobs, transport=transport))
        if "denied" in r:
            raise executor.ExecError(f"storage: credential denied — {r['denied']}")
        return r["ok"]

    caveats = {
        "effect_class": READ,                                # a read — always allowed by the ladder
        "sandbox": {"effects": [name], "network": True},
    }
    return k.integrate_tool(name, handler, caveats=caveats)


def register_manifest(k) -> str:
    """Record a discoverable manifest for the object-storage PUT rail (source="builtin"), so
    the plug-in-or-forge discovery layer can find the real storage engine before forging a
    new one. A manifest GRANTS NOTHING (manifest.py, Law) — the rail keeps its own gated
    install path; this only makes it findable. Returns the manifest cell id."""
    m = M.capability_manifest(
        "storage",
        description="store (PUT) or read (GET) a cloud object in an S3-style bucket",
        archetype="EFFECT", effect_class=STORAGE,
        caveats={"requires_approval": True},                 # a PUT lands a durable object
        source="builtin", version=1,
        tags=["storage", "s3", "object", "bucket", "cloud"])
    return M.register(k, m)
