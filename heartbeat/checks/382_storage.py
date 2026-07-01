"""Real cloud object-storage engine — WRAP the provider, gated PUT + READ GET, offline contract.

Policy: recreate the design in pure stdlib, but WRAP the real engine for high-liability
externals — writing a blob into someone's bucket is a durable OUTWARD effect on a store
other systems trust, and re-rolling that store is the liability. `storage.py` talks to a
REAL S3-style HTTPS API over stdlib `urllib` (zero deps). This check drives the rails
entirely OFFLINE via an injected fake transport (the real `urllib` transport is never
called), so the oracle stays deterministic and network-free while proving the full contract:

  - PUT is Morta-gated: unapproved → denied, and NO request is made before approval;
  - PUT success: a stored object → SUCCEEDED receipt carrying the ETag as `provider_ref`,
    the object key, the byte size (int), and the verified content hash;
  - idempotent by object key: the same bucket/key/content returns the prior receipt and
    makes NO second upload;
  - content-addressed integrity: a provider checksum ≠ the local content hash → FAILED;
  - GET is a READ effect (effect_class READ on the receipt), needs no approval, verifies
    the content hash of what it received;
  - HTTPS-only + fail-closed: a non-`https://` endpoint is refused BEFORE any request;
  - receipt mapping: stored → SUCCEEDED, 4xx → FAILED, timeout → UNKNOWN (never fabricated);
  - SANDBOX-mode: a non-`sandbox-` key is refused BEFORE any request (never a real bucket);
  - dispense-don't-disclose: the raw provider key never appears on the Weft — CRED1 applies
    it inside the broker;
  - the manifest is discoverable in the registry (EFFECT, effect_class STORAGE).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import storage, secrets, manifest
from decima.hashing import blob_id

S3_KEY = "sandbox-DECIMA_S3_SECRET_KEY_123"         # a raw provider key that must never leak
HTTPS = "https://s3.sandbox.example/v1"


def _put_transport(calls, response):
    """A fake object-store transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body})
        return response() if callable(response) else response
    return t


def _agent(kk):
    """A FRESH decima agent cell — refetched before each invoke (its spend/lease state
    advances on the Weave, so a stale cell must never drive a later invoke)."""
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL CLOUD OBJECT-STORAGE ENGINE (wrapped S3-style provider, offline contract) — dependency policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("s3", S3_KEY, service="s3")
    handle = broker.issue("s3", _agent(kk), "store and read cloud objects")

    payload = b"decima report: quarter results"
    local_hash = blob_id(payload, kind="blob")          # the content-address of the bytes

    # Object BYTES are staged OFF the Weft: the invoke carries only the blob_ref (hash). ─
    blobs = {}
    ref = storage.stage(blobs, payload)
    assert ref == local_hash, "stage must content-address the payload to its hash"

    # 1. PUT SUCCESS + Morta gate + ETag provider_ref + content hash. ──────────────────
    calls = []
    cap = storage.install_put_rail(
        kk, cap=100, broker=broker, agent_cell=_agent(kk), credential_handle=handle, blobs=blobs,
        name="storage_put_ok", endpoint=HTTPS,
        transport=_put_transport(calls, (200, {"etag": "etag-abc-001", "checksum": local_hash})))
    args = {"bucket": "reports", "key": "2026/q2.txt", "blob_ref": ref,
            "content_type": "text/plain", "cost": 0}
    # Morta: no approval yet → denied, and NO storage request made.
    denied = kk.invoke(_agent(kk), cap, args)
    assert denied.get("denied") and "approval" in denied["denied"], denied
    assert calls == [], "no storage PUT may be made before Morta approval"
    kk.approve(cap)
    ok = kk.invoke(_agent(kk), cap, args)
    assert ok["status"] == "SUCCEEDED", ok
    rc = kk.weave().get(ok["result_cell"]).content
    assert rc["provider_ref"] == "etag-abc-001" and rc["rail"] == "storage", rc
    assert rc["effect_class"] == "STORAGE", rc
    assert rc["key"] == "2026/q2.txt" and rc["bucket"] == "reports", rc
    assert rc["content_hash"] == local_hash, "the stored content hash must be the local content-address"
    assert isinstance(rc["size"], int) and not isinstance(rc["size"], bool), rc
    assert rc["size"] == len(payload), rc
    assert rc["bytes_on_weft"] is False, "raw object bytes must never land on the Weft"
    assert len(calls) == 1 and calls[0]["url"] == HTTPS + "/reports/2026/q2.txt", calls
    assert calls[0]["headers"]["x-amz-content-checksum"] == local_hash, calls
    # bytes go to the provider, never onto the Weft.
    assert payload.decode() not in "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events")), \
        "the raw object bytes must never be written to the Weft"
    line("  PUT success: Morta-gated (no call pre-approval) → SUCCEEDED receipt with ETag as "
         "provider_ref, byte size (int), verified content hash; bytes never on the Weft ✓")

    # 2. IDEMPOTENT BY OBJECT KEY — same bucket/key/content → prior receipt, NO second PUT.
    before = len(calls)
    again = kk.invoke(_agent(kk), cap, args)
    assert again["status"] == "SUCCEEDED", again
    rc2 = kk.weave().get(again["result_cell"]).content
    assert rc2.get("idempotent_replay") is True, rc2
    assert rc2["provider_ref"] == "etag-abc-001", rc2
    assert rc2["content_hash"] == local_hash, rc2
    assert len(calls) == before, "a replay of the same key+content must not make a second PUT"
    line("  idempotent by object key: same bucket/key/content → prior ETag, no second upload ✓")

    # 3. GET is a READ effect — no approval, effect_class READ, content hash verified. ─
    gcalls = []
    got_blobs = {}
    gcap = storage.install_get_rail(
        kk, broker=broker, agent_cell=_agent(kk), credential_handle=handle, blobs=got_blobs,
        name="storage_get_ok", endpoint=HTTPS,
        transport=_put_transport(gcalls, (200, {"body": payload, "etag": "etag-abc-001"})))
    # No approve() call — a READ needs no Morta gate.
    got = kk.invoke(_agent(kk), gcap, {"bucket": "reports", "key": "2026/q2.txt",
                                       "expected_hash": local_hash, "cost": 0})
    assert got["status"] == "SUCCEEDED", got
    grc = kk.weave().get(got["result_cell"]).content
    assert grc["effect_class"] == "READ", "a GET must be recorded as a READ effect"
    assert grc["content_hash"] == local_hash, "GET must verify the content hash of what it read"
    assert grc["provider_ref"] == "etag-abc-001" and grc["size"] == len(payload), grc
    assert got_blobs.get(local_hash) == payload, "read bytes must be staged off the Weft"
    assert len(gcalls) == 1, gcalls
    line("  GET is READ: no Morta gate, effect_class READ on the receipt, content hash of the "
         "read bytes verified against the expected hash ✓")

    # 4. INTEGRITY — a provider checksum ≠ the local content hash → FAILED (no durable write).
    icalls = []
    icap = storage.install_put_rail(
        kk, cap=100, broker=broker, agent_cell=_agent(kk), credential_handle=handle, blobs=blobs,
        name="storage_put_corrupt", endpoint=HTTPS,
        transport=_put_transport(icalls, (200, {"etag": "etag-x", "checksum": "WRONG_HASH"})))
    kk.approve(icap)
    corrupt = kk.invoke(_agent(kk), icap, {"bucket": "reports", "key": "bad.txt",
                                           "blob_ref": ref, "cost": 0})
    assert corrupt["status"] == "FAILED", corrupt
    assert len(icalls) == 1, "the upload was attempted, but the checksum mismatch fails closed"
    line("  integrity: a provider checksum ≠ the local content hash → FAILED (corruption caught) ✓")

    # 5. 4xx → FAILED (nothing durable was written). ──────────────────────────────────
    fcalls = []
    fcap = storage.install_put_rail(
        kk, cap=100, broker=broker, agent_cell=_agent(kk), credential_handle=handle, blobs=blobs,
        name="storage_put_denied", endpoint=HTTPS,
        transport=_put_transport(fcalls, (403, {"error": "AccessDenied"})))
    kk.approve(fcap)
    bad = kk.invoke(_agent(kk), fcap, {"bucket": "locked", "key": "x.txt",
                                       "blob_ref": ref, "cost": 0})
    assert bad["status"] == "FAILED", bad
    assert len(fcalls) == 1, "the 4xx PUT was attempted once and definitively failed"
    line("  access denied (4xx) → FAILED receipt — a definite no-effect (nothing written) ✓")

    # 6. NETWORK ERROR / TIMEOUT → UNKNOWN (outcome unobservable). ─────────────────────
    def boom():
        raise TimeoutError("connection reset")
    tcap = storage.install_put_rail(
        kk, cap=100, broker=broker, agent_cell=_agent(kk), credential_handle=handle, blobs=blobs,
        name="storage_put_timeout", endpoint=HTTPS, transport=_put_transport([], boom))
    kk.approve(tcap)
    unk = kk.invoke(_agent(kk), tcap, {"bucket": "reports", "key": "maybe.txt",
                                       "blob_ref": ref, "cost": 0})
    assert unk["status"] == "UNKNOWN", unk
    line("  timeout → UNKNOWN receipt — outcome unobservable, never fabricated ✓")

    # 7. HTTPS-ONLY — a non-HTTPS endpoint is refused BEFORE any request. ──────────────
    hcalls = []
    hcap = storage.install_put_rail(
        kk, cap=100, broker=broker, agent_cell=_agent(kk), credential_handle=handle, blobs=blobs,
        name="storage_put_http", endpoint="http://s3.sandbox.example/v1",
        transport=_put_transport(hcalls, (200, {"etag": "etag-y", "checksum": local_hash})))
    kk.approve(hcap)
    refused = kk.invoke(_agent(kk), hcap, {"bucket": "reports", "key": "z.txt",
                                           "blob_ref": ref, "cost": 0})
    assert refused["status"] == "FAILED", refused
    assert hcalls == [], "a non-HTTPS endpoint must be refused before any request/key on the wire"
    line("  HTTPS-only: a non-https endpoint is refused before any request — the key never "
         "travels in cleartext (fail-closed) ✓")

    # 8. SANDBOX-mode — a non-`sandbox-` key is refused BEFORE any request. ────────────
    broker.store("s3_prod", "PRODUCTION_LIVE_KEY", service="s3")
    prod_handle = broker.issue("s3_prod", _agent(kk), "prod attempt")
    scalls = []
    scap = storage.install_put_rail(
        kk, cap=100, broker=broker, agent_cell=_agent(kk), credential_handle=prod_handle, blobs=blobs,
        name="storage_put_prod", endpoint=HTTPS,
        transport=_put_transport(scalls, (200, {"etag": "etag-p", "checksum": local_hash})))
    kk.approve(scap)
    prod = kk.invoke(_agent(kk), scap, {"bucket": "reports", "key": "p.txt",
                                        "blob_ref": ref, "cost": 0})
    assert prod["status"] == "FAILED", prod
    assert scalls == [], "a non-sandbox key must be refused before any request (SANDBOX-ONLY)"
    line("  SANDBOX-mode: a non-sandbox (production) key is refused before any request — the "
         "reference never writes a real bucket ✓")

    # 9. DISPENSE-DON'T-DISCLOSE — the raw provider key never appears anywhere on the Weft.
    all_payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert S3_KEY not in all_payloads, \
        "a raw provider key must never be written to the Weft (dispense-don't-disclose)"
    assert "PRODUCTION_LIVE_KEY" not in all_payloads, "no raw key on the Weft"
    line("  no raw provider key on the Weft — CRED1 applies it inside the broker ✓")

    # 10. DISCOVERABLE MANIFEST — registered and findable in the registry. ─────────────
    mid = storage.register_manifest(kk)
    assert mid, "register_manifest must return a manifest cell id"
    m = manifest.get(kk, "storage")
    assert m is not None and m.content["effect_class"] == "STORAGE", m
    assert m.content["archetype"] == "EFFECT", m.content
    assert m.content["caveats"].get("requires_approval") is True, m.content
    found = manifest.find(kk, query="bucket")
    assert any(c.content["name"] == "storage" for c in found), "storage manifest must be discoverable by query"
    assert any(c.content["name"] == "storage" for c in manifest.registry(kk)), "must be in registry"
    line("  manifest 'storage' (EFFECT, effect_class STORAGE, requires_approval) discoverable in the registry ✓")

    line("  → the real object-storage engine is wrapped over stdlib urllib (zero deps): PUT is "
         "Morta-gated + idempotent by object key, receipts map stored/denied/timeout → "
         "SUCCEEDED/FAILED/UNKNOWN with the ETag as provider_ref; GET is a READ; content is "
         "hash-verified; HTTPS-only + SANDBOX-only; the key and the bytes are never disclosed.")
