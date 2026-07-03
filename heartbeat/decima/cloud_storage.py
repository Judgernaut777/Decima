"""Real cloud object-storage engine — WRAP the provider, keep the bytes off the Weft.

Decima's policy: recreate the design in pure stdlib, but for high-liability externals
WRAP THE REAL ENGINE rather than reimplement it. Durable object storage (S3 / GCS /
Dropbox-style) is such an external — it is a real HTTPS API, so the real engine rides
stdlib `urllib` with ZERO pip dependencies. VAULT1 (`vault.py`) stays the SOVEREIGN
substrate (your data IS the Weft); this module COMPLEMENTS it by pushing an actual blob
to an EXTERNAL provider and recording only a content-addressed REFERENCE on the Weft.

The invariant that makes this safe: the object BYTES are NEVER written to the Weft. We
compute a local content digest (`hashing.blob_id`) BEFORE upload, ship the bytes to the
provider, and land only {bucket, key, size, digest, provider_ref} — a commitment, never
the payload — exactly as the secrets broker lands a credential reference, never the value.

GUARDRAILS (mirroring the tax engine / OIDC engine):
  - **HTTPS-only** — `put_object` refuses to send the API key to a non-`https://`
    endpoint BEFORE the key touches the wire (never leak the key in cleartext).
  - **key via CRED1** — the provider API key lives in the secrets broker; `store` calls
    `broker.use_secret`, which applies the key INSIDE the broker (never returned, never
    logged, never on the Weft). The raw key never appears in a `stored_object` cell.
  - **bytes never on the Weft** — the payload is uploaded to the provider; the Weft holds
    only its content digest + metadata. The raw object bytes never cross onto the Log.
  - **integrity-checked** — if the provider returns a content digest / ETag, it MUST
    equal the locally computed digest; a mismatch fails closed (no cell, corruption caught).
  - **fail closed** — a provider 4xx (access denied / bad bucket), an unreachable/timed-out
    endpoint, a non-HTTPS endpoint, a digest mismatch, or a denied credential records NO
    `stored_object` cell and returns `{"denied": reason}`.
  - **ints only in signed content** — `size` is an int (bytes); no float ever lands.
  - **transport seam** — `put_object` takes a `transport(url, headers, body) -> (status,
    json)`; the default is a real `urllib` PUT; tests inject a fake, so the offline oracle
    exercises the full contract with NO network.

Composes public secrets / model / hashing / kernel APIs only. No core edit; does not
touch vault.py.
"""
import json
from urllib.parse import quote

from decima.model import assert_content
from decima.hashing import content_id, blob_id

STORED_OBJECT = "stored_object"   # the on-Weft REFERENCE to a provider-stored blob (no bytes)


class CloudStorageError(Exception):
    """A storage-engine failure — no `stored_object` may be recorded (fail closed). Covers
    a non-HTTPS endpoint, an unreachable/timed-out endpoint, and a provider 4xx/error."""


def _urllib_transport(url: str, headers: dict, body):
    """(Phase 2 · GO LIVE) FAIL-CLOSED default — the bare stdlib socket default is
    GONE: the armed wire guard (decima/wire.py) refuses ungated egress anyway, so
    `transport=None` on the live path now refuses HERE, first, with the sanctioned
    path named. Build the wire-gated transport via
    `live_wire.gated_put_transport(k, agent_cell, cap_id)`
    (a granted, Morta-approved egress capability) and inject it as `transport=`.
    Injected fake transports (the offline oracle, every test-mode path) never
    resolve to this default and are unaffected."""
    from decima import live_wire
    raise live_wire.NoGatedTransport(
        "cloud_storage", hint='live_wire.gated_put_transport(k, agent_cell, cap_id)')


def _require_int(name: str, v):
    """Guard that a value the engine will sign onto the Weft is an int (never float/bool)."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise CloudStorageError(f"{name} must be an int (bytes), got {v!r}")
    return int(v)


def _as_bytes(payload) -> bytes:
    """The object payload as bytes — a str is UTF-8 encoded, bytes pass through."""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise CloudStorageError("object payload must be bytes or str")


def put_object(secret_key: str, obj: dict, *, transport=None) -> dict:
    """Upload one object to the REAL provider over stdlib `urllib`.

    `obj` describes the object — `endpoint` (the provider's HTTPS base URL), `bucket`,
    `key` (the object key/path), `payload` (bytes or str), and `content_type`. The local
    content digest is computed with `hashing.blob_id` BEFORE the upload; the bytes are then
    PUT to `{endpoint}/{bucket}/{key}`. Returns
    {provider_ref, bucket, key, size:int, digest, provider_digest} — provider_ref is the
    ETag / version id, `size` is the byte length (int), `digest` is the LOCAL digest, and
    `provider_digest` is the provider's returned ETag/digest (or None) for the integrity
    check. The object BYTES are never returned or recorded — only the digest + metadata.

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the wire.
    Raises `CloudStorageError` on a non-HTTPS endpoint, an unreachable endpoint, or a
    definite provider error (4xx / error body) — the caller (`store`) fails closed."""
    transport = transport or _urllib_transport

    endpoint = str(obj.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the API key on the wire in cleartext. Refuse before sending.
        raise CloudStorageError("refusing to send the API key to a non-HTTPS storage endpoint")

    bucket = str(obj.get("bucket", ""))
    key = str(obj.get("key", ""))
    if not bucket or not key:
        raise CloudStorageError("object needs a bucket and a key")

    body = _as_bytes(obj.get("payload"))
    size = len(body)
    digest = blob_id(body, kind="blob")                      # content-address BEFORE upload
    content_type = str(obj.get("content_type", "application/octet-stream"))

    url = f"{endpoint.rstrip('/')}/{quote(bucket)}/{quote(key)}"
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": content_type,
        "Content-Length": str(size),
    }
    try:
        status, resp = transport(url, headers, body)
    except Exception as e:                                    # network/timeout — unreachable
        raise CloudStorageError(f"storage endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise CloudStorageError(f"unparseable storage response (status {status})")
    if status in (200, 201):
        return {
            "provider_ref": resp.get("version_id") or resp.get("etag") or resp.get("id"),
            "bucket": bucket,
            "key": key,
            "size": int(size),
            "digest": digest,
            # the provider's own content commitment (if any) — checked against `digest`.
            "provider_digest": resp.get("digest") or resp.get("etag"),
        }
    err = resp.get("error") or resp.get("message") or f"http {status}"
    raise CloudStorageError(f"provider rejected the upload: {err}")   # definite error


def store(k, *, endpoint: str, obj: dict, credential_handle: str, broker,
          agent_cell, transport=None) -> dict:
    """Store an object in the REAL provider and record its REFERENCE on the Weft (fail closed).

    Resolves the provider API key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `put_object` against the HTTPS
    `endpoint`, verifies the provider's returned digest/ETag matches the local digest, and
    on success asserts a `stored_object` cell carrying bucket / key / size (int) / digest /
    provider_ref (NEVER the API key, NEVER the raw object bytes). Returns
    {stored_object: <cell id>, provider_ref, digest, size}.

    On a denied credential (revoked/unauthorized/over-budget), any engine error (non-HTTPS,
    unreachable, provider 4xx), or a digest mismatch (integrity), it records NO cell and
    returns {"denied": reason}."""
    o = {**obj, "endpoint": endpoint}
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: put_object(key, o, transport=transport))
    except CloudStorageError as e:
        return {"denied": f"cloud_storage: {e}"}             # fail closed — no cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    # Integrity: if the provider committed to the content, it MUST match our local digest.
    provider_digest = result.get("provider_digest")
    if provider_digest is not None and provider_digest != result["digest"]:
        return {"denied": f"cloud_storage: provider digest {provider_digest!r} != local "
                          f"digest {result['digest']!r} (integrity check failed)"}

    content = {
        "bucket": str(result["bucket"]),
        "key": str(result["key"]),
        "size": _require_int("size", result["size"]),
        "digest": result["digest"],
        "provider_ref": result.get("provider_ref"),
        "content_type": str(obj.get("content_type", "application/octet-stream")),
        "disclosed": False,                                  # neither the key nor the bytes
    }
    # Content-addressed by bucket/key/digest: re-storing identical content is idempotent
    # and one blob keeps one identity on the Log.
    cid = content_id({"stored_object": {"bucket": content["bucket"],
                                        "key": content["key"], "digest": content["digest"]}})
    assert_content(k.weft, k.decima_agent_id, cid, STORED_OBJECT, content)
    return {
        "stored_object": cid,
        "provider_ref": content["provider_ref"],
        "digest": content["digest"],
        "size": content["size"],
    }


def stored(k) -> list:
    """All folded `stored_object` reference cells on the Weft."""
    return list(k.weave().of_type(STORED_OBJECT))
