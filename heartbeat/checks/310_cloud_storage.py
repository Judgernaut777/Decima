"""Real cloud object-storage engine — WRAP the provider, offline contract (dependency policy).

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals. VAULT1 stays the sovereign substrate (your data IS the Weft); `cloud_storage.py`
pushes an actual blob to a REAL S3/GCS/Dropbox-style HTTPS provider over stdlib `urllib`
(zero deps) and records only a content-addressed REFERENCE on the Weft. This check drives
it entirely OFFLINE via an injected fake transport (the real `urllib` transport is never
called), so the oracle stays deterministic and network-free while proving the full contract:

  - success: an injected 200 with an ETag/digest that MATCHES the local content digest →
    a `stored_object` cell carrying provider_ref / digest / size(int); the raw object BYTES
    are NOT on the Weft, only the digest;
  - integrity: a provider ETag/digest that does NOT match the local digest → {"denied"} and
    NO cell (corruption caught, fail closed);
  - fail closed: a provider 4xx (access denied) → {"denied"} and NO `stored_object` cell;
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the fake transport
    is never called) — the API key never rides a cleartext wire;
  - dispense-don't-disclose: the raw API key never appears in any event payload on the Weft —
    CRED1 applies it inside the broker.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import cloud_storage, secrets
from decima.hashing import blob_id

API_KEY = "sk_live_S3_SUPER_SECRET_ACCESS_KEY"
ENDPOINT = "https://s3.us-east-1.amazonaws.com"
BUCKET = "decima-blobs"
KEY = "reports/2026/q2.pdf"
PAYLOAD = b"%PDF-1.7 the loom holds \x00\x01\x02 binary blob bytes"


def _transport(calls, response):
    """A fake object-storage transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL CLOUD STORAGE ENGINE (wrapped provider, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("s3", API_KEY, service="s3")
    handle = broker.issue("s3", _decima(kk), "store objects in cloud storage")

    obj = {"bucket": BUCKET, "key": KEY, "payload": PAYLOAD, "content_type": "application/pdf"}
    local_digest = blob_id(PAYLOAD, kind="blob")             # what the engine must commit to

    # 1. SUCCESS — provider stores the blob; we record a REFERENCE (not the bytes). ────────
    calls = []
    ok_resp = (200, {"etag": local_digest,                   # provider's content digest matches
                     "version_id": "v_9f3c",
                     "digest": local_digest})
    res = cloud_storage.store(kk, endpoint=ENDPOINT, obj=obj, credential_handle=handle,
                              broker=broker, agent_cell=_decima(kk),
                              transport=_transport(calls, ok_resp))
    assert "stored_object" in res and res["digest"] == local_digest, res
    assert res["provider_ref"] == "v_9f3c" and res["size"] == len(PAYLOAD), res
    assert len(calls) == 1 and calls[0]["url"].startswith("https://"), calls
    assert BUCKET in calls[0]["url"] and calls[0]["body"] == PAYLOAD, calls
    cell = kk.weave().get(res["stored_object"]).content
    assert cell["bucket"] == BUCKET and cell["key"] == KEY, cell
    assert cell["digest"] == local_digest and cell["provider_ref"] == "v_9f3c", cell
    assert isinstance(cell["size"], int) and not isinstance(cell["size"], bool), cell
    assert cell["size"] == len(PAYLOAD), cell
    # the raw object BYTES are NOT on the Weft — only the digest + metadata.
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert PAYLOAD.decode("latin-1") not in payloads, "object bytes must never land on the Weft"
    assert "binary blob bytes" not in payloads, "object bytes must never land on the Weft"
    assert local_digest in payloads, "the content digest (the reference) must be recorded"
    line("  success: injected 200 (matching ETag) → stored_object cell with provider_ref / "
         "digest / size(int); the raw object bytes are NOT on the Weft, only the digest ✓")

    # 2. INTEGRITY — a provider digest that does NOT match → denied, NO cell. ──────────────
    before = len(cloud_storage.stored(kk))
    bad_calls = []
    corrupt = (200, {"etag": "deadbeefdeadbeefdeadbeefdeadbeef",   # != local digest
                     "version_id": "v_bad", "digest": "deadbeefdeadbeefdeadbeefdeadbeef"})
    tampered = cloud_storage.store(kk, endpoint=ENDPOINT, obj=obj, credential_handle=handle,
                                   broker=broker, agent_cell=_decima(kk),
                                   transport=_transport(bad_calls, corrupt))
    assert "denied" in tampered and "integrity" in tampered["denied"], tampered
    assert len(bad_calls) == 1, "the upload happened, but the digest mismatch must fail closed"
    assert len(cloud_storage.stored(kk)) == before, "no stored_object cell on a digest mismatch"
    line("  integrity: provider ETag/digest ≠ local digest → {denied} and NO cell "
         "(corruption caught, fail closed) ✓")

    # 3. FAIL CLOSED — a provider 4xx (access denied) → denied, NO cell. ───────────────────
    err_calls = []
    denied = cloud_storage.store(kk, endpoint=ENDPOINT, obj=obj, credential_handle=handle,
                                 broker=broker, agent_cell=_decima(kk),
                                 transport=_transport(err_calls, (403, {"error": "AccessDenied"})))
    assert "denied" in denied and "cloud_storage" in denied["denied"], denied
    assert len(err_calls) == 1, "the request was made, but the 4xx must fail closed"
    assert len(cloud_storage.stored(kk)) == before, "no stored_object cell on a provider error"
    line("  fail closed: provider 4xx (AccessDenied) → {denied} and NO stored_object cell ✓")

    # 4. HTTPS-ONLY — a non-HTTPS endpoint is refused before any request. ──────────────────
    http_calls = []
    bad = cloud_storage.store(kk, endpoint="http://s3.us-east-1.amazonaws.com", obj=obj,
                              credential_handle=handle, broker=broker, agent_cell=_decima(kk),
                              transport=_transport(http_calls, ok_resp))
    assert "denied" in bad and "HTTPS" in bad["denied"], bad
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    assert len(cloud_storage.stored(kk)) == before, "no stored_object cell on a refused endpoint"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the key is sent "
         "(transport never called) ✓")

    # 5. DISPENSE-DON'T-DISCLOSE — the raw API key never on the Weft. ──────────────────────
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in payloads, "the raw storage API key must never be written to the Weft"
    line("  no raw API key on the Weft — CRED1 applies it inside the broker ✓")

    line("  → storage is wrapped, not reinvented: a real provider (over stdlib urllib, zero "
         "deps) holds the bytes; Decima records only a content-addressed reference on the "
         "Weft, holds the key in CRED1, integrity-checks the upload, refuses cleartext, and "
         "fails closed.")
