"""PRIVACY REDACTION + classification (Cycle 50) — the fail-closed scrubber UPSTREAM
of the router.

Decima has a privacy=private tier RULE (`router._r_private`) but no redaction: a rule
picks a tier, it never inspects the bytes, so a raw live secret could still ride a task
into an external provider. `decima/redact.py` closes that gap — a deterministic secret/PII
scrubber that runs BEFORE any task text can reach a non-local provider, plus a
classification that FAILS CLOSED on a raw high-value secret. It COMPOSES over `router.py`
(via `to_router_privacy`) and never edits it.

This check proves, offline + deterministically:

  (a) SCRUB — a text carrying an API key / JWT / DB-URL / SSH private key / bearer token is
      scrubbed so the secret BYTES are ABSENT from the output that would reach an external
      tier; each secret becomes a typed `<REDACTED:kind:n>` placeholder;
  (b) CLASSIFY — such a raw high-value secret classifies as `secret_sensitive`;
  (c) FAIL CLOSED — `secret_sensitive` (and repo_sensitive / restricted) is BLOCKED from an
      external provider: `admit(...)` RAISES, so the external path is actually prevented,
      while a public task is admitted — assert the block, not just the label;
  (d) DETERMINISM — scrubbing the same input twice yields byte-identical output;
  (e) CLEAN PASS-THROUGH — an innocuous public text is unchanged and classified `public`;
  (f) PROVENANCE — the redaction Cell on the Weft records CLASSES + COUNTS (ints) and NEVER
      the secret value;
  (g) COMPOSITION — the class maps onto `router`'s privacy field so a sensitive task is
      pinned to the on-device (local-small) lane by the EXISTING router rule.

Contract: run(k, line). Fail loud (assert / expected RedactionBlocked).
"""
from decima import redact
from decima import router


# Synthetic secrets — NOT real credentials; shaped to match the detectors. Kept as distinct
# locals so the check can assert each one's exact bytes vanish from the scrubbed output.
_API_KEY = "sk-livedeadbeef0123456789ABCDEFghij"
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
_DB_URL = "postgres://admin:hunter2@db.prod:5432/customers"
_PRIVATE_KEY = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
                "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAADEADBEEFsecretkeymaterialHERE\n"
                "-----END OPENSSH PRIVATE KEY-----")
_BEARER = "Bearer abc123.DEF456-ghi789_JKL0mnop"
_SECRET_BYTES = (_API_KEY, _JWT, _DB_URL, _PRIVATE_KEY, _BEARER,
                 "hunter2", "livedeadbeef0123456789ABCDEFghij")  # incl. inner substrings


def run(k, line):
    line("\n== PRIVACY REDACTION + classification — the fail-closed scrubber upstream of the router ==")

    external = {"id": "vendor-frontier", "privacy_tier": "external_paid"}
    local = {"id": "on-device", "privacy_tier": "local_only"}

    # (a)+(b) SCRUB + CLASSIFY — a task loaded with high-value secrets. ────────────────
    dirty = (f"Debug prod: api key {_API_KEY}, session {_JWT}, "
             f"db {_DB_URL}, and key:\n{_PRIVATE_KEY}\nAuthorization: {_BEARER}")
    scrubbed, findings = redact.scrub(dirty)
    for raw in _SECRET_BYTES:
        assert raw not in scrubbed, f"secret bytes survived scrubbing: {raw!r}"
    kinds = {f["kind"] for f in findings}
    assert redact.HIGH_VALUE_KINDS <= kinds, \
        f"every high-value secret must be detected; missed {redact.HIGH_VALUE_KINDS - kinds}"
    assert "<REDACTED:api_key:" in scrubbed and "<REDACTED:jwt:" in scrubbed
    cls = redact.classify_privacy(dirty, findings)
    assert cls == redact.SECRET_SENSITIVE, f"raw high-value secret must be secret_sensitive, got {cls}"
    line(f"  scrub: {len(findings)} secrets → typed placeholders; bytes absent; "
         f"classes={sorted(kinds)}; classify={cls} ✓")

    # (c) FAIL CLOSED — admit() to an EXTERNAL provider RAISES; the external path is
    #     actually prevented. This is the load-bearing guarantee.
    assert redact.external_permitted(cls) is False, "secret_sensitive must not be external-permitted"
    reached_external = {"sent": False}

    def _external_send(payload):          # a stand-in for a real external provider call
        reached_external["sent"] = True   # if this ever runs on a secret task, the gate failed
        return payload

    try:
        safe, _ = redact.admit(dirty, external, k)
        _external_send(safe)              # only reached if admit did NOT block
        raise AssertionError("admit() let a secret_sensitive task reach an EXTERNAL provider")
    except redact.RedactionBlocked as e:
        assert e.classification == redact.SECRET_SENSITIVE
    assert reached_external["sent"] is False, "the external send must NEVER run on a blocked task"
    # sanity: the SAME text on a LOCAL provider is admitted (scrubbed) — the gate is about
    # the destination, not a blanket refusal; and it still scrubs on the way.
    local_text, s_local = redact.admit(dirty, local, k)
    for raw in _SECRET_BYTES:
        assert raw not in local_text, "even the local path must receive scrubbed text"
    assert s_local.classification == redact.SECRET_SENSITIVE
    line("  fail closed: admit(secret task, EXTERNAL) RAISES RedactionBlocked — the external "
         "send never runs; the local path is admitted with scrubbed text ✓")

    # (d) DETERMINISM — same input twice → byte-identical output. ──────────────────────
    again, findings2 = redact.scrub(dirty)
    assert again == scrubbed, "scrub must be deterministic (identical output for identical input)"
    assert [f["kind"] for f in findings2] == [f["kind"] for f in findings]
    line("  determinism: scrub(x) == scrub(x) — byte-identical, order-stable ✓")

    # (e) CLEAN PASS-THROUGH — an innocuous public text is unchanged and classified public.
    clean = "Please summarize the quarterly product roadmap in three bullet points."
    c_scrubbed, c_findings = redact.scrub(clean)
    assert c_scrubbed == clean and c_findings == [], "a clean text must pass through unchanged"
    assert redact.classify_privacy(clean, c_findings) == redact.PUBLIC
    ok_text, ok_s = redact.admit(clean, external, k)   # public → external is permitted
    assert ok_text == clean and ok_s.external_permitted is True
    line("  clean pass-through: innocuous text unchanged, classified public, admitted to "
         "the external provider ✓")

    # (f) PROVENANCE — the redaction Cell records CLASSES + COUNTS (ints), never the value.
    rec_id = redact.record_redaction(k, findings, cls)
    cell = k.weave().get(rec_id)
    assert cell is not None and cell.type == redact.REDACTION, "a redaction Cell must land on the Weft"
    body = cell.content
    assert body["classification"] == redact.SECRET_SENSITIVE
    assert sorted(body["counts"]) == sorted(kinds) and all(isinstance(v, int) for v in body["counts"].values())
    assert body["counts"]["db_url"] >= 1 and isinstance(body["total"], int)
    import json
    blob = json.dumps(body)
    for raw in _SECRET_BYTES:
        assert raw not in blob, f"the redaction record leaked a secret value: {raw!r}"
    line(f"  provenance: redaction Cell {rec_id[:8]} records classes+counts "
         f"(total={body['total']}, ints) with NO secret bytes in the record ✓")

    # (g) COMPOSITION over router.py — a sensitive class pins the EXISTING router rule to the
    #     on-device lane (repo_sensitive here); a public task is free to use any tier.
    repo_text = "logs on host worker-7.internal, dumped to /var/lib/decima/state.db"
    _, repo_findings = redact.scrub(repo_text)
    repo_cls = redact.classify_privacy(repo_text, repo_findings)
    assert repo_cls == redact.REPO_SENSITIVE, f"infra markers → repo_sensitive, got {repo_cls}"
    priv = redact.to_router_privacy(repo_cls)
    assert priv == "private", "repo_sensitive must map to router privacy 'private'"
    rt = router.Router()
    routing = rt.route(router.TaskDescriptor(kind="summarize", privacy=priv))
    assert routing.tier == router.LOCAL_SMALL and routing.factor == "privacy", \
        "the EXISTING router _r_private rule must pin a private task to local-small"
    # repo_sensitive is likewise refused the external path (fail closed on destination).
    try:
        redact.admit(repo_text, external, k)
        raise AssertionError("repo_sensitive reached an external provider")
    except redact.RedactionBlocked:
        pass
    line(f"  composition: {repo_cls} → router privacy={priv!r} → existing router pins "
         f"tier={routing.tier} (factor={routing.factor}); external path refused ✓")

    line("  → redaction is the fail-closed gate upstream of the router: secrets are scrubbed "
         "to typed placeholders, a raw high-value secret is secret_sensitive and BLOCKED from "
         "every external provider, scrubbing is deterministic, and the Weft records only "
         "classes+counts — never the secret.")
