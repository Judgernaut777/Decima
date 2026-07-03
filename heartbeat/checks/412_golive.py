"""GO-LIVE RAIL — the operator surface for Phase 2's credential-gated half
(heartbeat/decima/golive.py).

Cycle 48 left Phase 2 needing the OPERATOR: real credentials in the broker, a
real egress grant for the brain, an honest status surface. `golive` is that
rail, and this check is its adversarial detector — entirely OFFLINE (injected
fake socket seams; denials raise BEFORE any open; no wall clock, no real key):

  (a) SECRET INTAKE stays off the Weft — a sentinel credential pulled from an
      (injected) environment via `intake_env` lands ONLY in the broker's
      in-memory store; the sentinel's bytes appear NOWHERE in any Weft event or
      folded Cell (the Weft holds a `credential` REFERENCE: name + digest,
      disclosed=False); the intake report and `doctor` are redacted (names +
      "present", never a value); intake is IDEMPOTENT (an unchanged value
      re-lands nothing — the log does not move);
  (b) an UNGRANTED live brain call is refused FAIL CLOSED — a ModelBrain with no
      egress binding cannot construct a transport at all (RuntimeError), and one
      bound to an UNAPPROVED grant raises EgressDenied at the wire gate (Morta)
      with the denial on the Weft and the socket seam never reached; the
      Morta-gated grant ENACTOR itself refuses a direct, unapproved invoke;
  (c) the GRANT FLOW rides the approval-inbox spine — `request_grant` enqueues a
      durable `inbox_item` (nothing auto-approves, the wire stays closed);
      a human `approve` enacts the grant through the SAME authorize/Morta gate,
      after which a live call against an INJECTED fake transport succeeds and
      leaves the `wire_decision` ALLOW provenance (url · host · capability) —
      and a ModelBrain driven through that transport gets a REAL (fake-socket)
      model turn, not a fallback; re-requesting the host is idempotent ("live",
      no duplicate item);
  (d) DOCTOR is honest and redacted — armed wire reported, the granted host
      listed as approved, secret names only, engines honestly "none live"
      (the Lane B `k.live_engines` seam reported as absent), and no secret
      bytes anywhere in its output;
  (e) REVOCATION does not resurrect — after Morta revokes the grant the wire is
      closed ("revoked"), and re-requesting the SAME host retracts the stale
      approval and queues a FRESH human decision (never silently live again);
      boot with an empty environment touches NOTHING (the log does not move).

Contract: run(k, line). Fail loud via assert. Owns fresh, offline Kernels.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import egress, golive, wire
from decima.agent import ModelBrain, RuleBrain
from decima.inbox import ApprovalInbox, ITEM

# Sentinels no legitimate content could contain: if these bytes surface in any
# event, cell, report, or doctor output, custody is broken and we fail loud.
SENTINEL = "sk-golive-SENTINEL-3f9a1c77e2d0b845-stripe"
SENTINEL_KEY = "sk-ant-golive-SENTINEL-8c1b44aa90f7e2d3"


def _world_dump(kk) -> str:
    """EVERYTHING durable: every Weft event's payload and every folded Cell's
    content, repr'd — the haystack the sentinel must never appear in."""
    parts = [repr((ev.verb, ev.author, ev.body)) for ev in kk.weft.events()]
    parts += [repr((c.id, c.type, c.content)) for c in kk.weave().cells.values()]
    return "\n".join(parts)


def run(k, line):
    line("\n== GO-LIVE RAIL (secret custody · inbox-gated egress grants · honest doctor) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # ── (a) SECRET INTAKE: the sentinel never touches the Weft ──────────────
    env = {"DECIMA_SECRET_STRIPE_TEST": SENTINEL, "ANTHROPIC_API_KEY": SENTINEL_KEY,
           "PATH": "/usr/bin", "DECIMA_SECRET_": "no-name-no-store"}
    rep = golive.intake_env(kk, environ=env)
    assert [r["name"] for r in rep] == ["anthropic", "stripe_test"], rep
    assert all(r["status"] == "stored" for r in rep), rep
    assert SENTINEL not in repr(rep) and SENTINEL_KEY not in repr(rep), \
        "the intake report must be redacted"
    # the CRED1 reference IS on the Weft (name + digest, disclosed=False) …
    creds = {c.content["name"]: c for c in kk.weave().of_type("credential")}
    assert set(creds) == {"anthropic", "stripe_test"}, sorted(creds)
    assert all(c.content["disclosed"] is False and c.content["digest"]
               for c in creds.values()), "the reference commits, never discloses"
    # … but the sentinel's bytes are NOWHERE in any event or folded cell.
    dump = _world_dump(kk)
    assert SENTINEL not in dump and SENTINEL_KEY not in dump, \
        "a raw secret value must NEVER land on the Weft"
    # idempotent: an unchanged value re-lands NOTHING (the log does not move).
    lam = kk.weft.lamport
    rep2 = golive.intake_env(kk, environ=env)
    assert all(r["status"] == "unchanged" for r in rep2), rep2
    assert kk.weft.lamport == lam, "an unchanged intake must append no event"
    line("  (a) intake: sentinel held ONLY in the broker — Weft carries a "
         "name+digest reference, zero secret bytes in any event/cell; report "
         "redacted; re-intake appends nothing ✓")

    # ── (b) UNGRANTED live brain call: refused fail closed, no socket ───────
    mb = ModelBrain(SENTINEL_KEY)
    try:
        mb._post({"model": "m", "messages": []})
        raise AssertionError("a live call with no egress binding must refuse")
    except RuntimeError as e:
        assert "egress" in str(e), e
    res = golive.request_grant(kk, "api.anthropic.com")
    assert res["status"] == "pending", res
    ecap, item = res["capability"], res["item"]
    assert ecap not in kk.approvals, "nothing may auto-approve an egress grant"
    # bound to the (real, but UNAPPROVED) grant: EgressDenied at the Morta gate,
    # the denial is on the Weft, and no socket layer is ever reached.
    agent = kk.weave().get(kk.decima_agent_id)
    mb.bind_egress(kk, agent, ecap)
    try:
        mb._post({"model": "m", "messages": []})
        raise AssertionError("an UNAPPROVED grant must be refused at the wire")
    except wire.EgressDenied as e:
        assert "Morta" in str(e), e
    assert any(c.content.get("decision") == wire.DENY
               and c.content.get("host") == "api.anthropic.com"
               for c in kk.weave().of_type(wire.WIRE_DECISION)), \
        "the ungated brain denial must be recorded on the Weft"
    # the grant ENACTOR is itself Morta-gated: a direct invoke (no human) is denied.
    direct = kk.invoke(agent, res["grant_capability"], {"host": "api.anthropic.com",
                                                        "egress_capability": ecap})
    assert "denied" in direct and "approval" in direct["denied"], direct
    assert ecap not in kk.approvals, "a denied enactor invoke must enact nothing"
    line("  (b) fail closed: no binding → no transport; unapproved grant → "
         "EgressDenied (Morta) with the denial on the Weft; the enactor itself "
         "refuses a direct unapproved invoke ✓")

    # ── (c) THE GRANT RIDES THE INBOX: enqueue → human approve → live ───────
    ib = ApprovalInbox(kk)
    cell = kk.weave().get(item)
    assert cell is not None and cell.type == ITEM, "a durable inbox_item must exist"
    assert "api.anthropic.com" in cell.content["description"], cell.content
    assert item in [c.id for c in ib.pending()], "the grant awaits a HUMAN"
    # idempotent while pending: no duplicate item is queued.
    again = golive.request_grant(kk, "api.anthropic.com")
    assert again["status"] == "pending" and again["item"] == item, again
    # the human approves — the enactment runs through the SAME authorize/Morta gate.
    approved = ib.approve(item)
    assert "ok" in approved, approved
    assert ecap in kk.approvals, "approval must land the capability-scoped grant"
    assert golive.request_grant(kk, "api.anthropic.com")["status"] == "live"
    # a live call against an INJECTED fake transport now succeeds …
    calls = []

    def fake_open(url, headers, body, method, timeout):
        calls.append(url)
        return 200, {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "name": "act",
             "input": {"action": "respond", "text": "live and wire-gated",
                       "reasoning": "golive check"}}]}

    agent = kk.weave().get(kk.decima_agent_id)          # re-read post-grant
    t = egress.live_transport(kk, agent, ecap, _open=fake_open)
    mb_live = ModelBrain(SENTINEL_KEY, transport=t)
    action = mb_live.decide("hello out there", kk.weave(), agent)
    assert action.kind == "respond" and action.text == "live and wire-gated", \
        f"the LIVE model path (not a fallback) must have answered: {action}"
    assert calls == ["https://api.anthropic.com/v1/messages"], calls
    # … and left the wire_decision ALLOW provenance.
    allows = [c for c in kk.weave().of_type(wire.WIRE_DECISION)
              if c.content.get("decision") == wire.ALLOW]
    assert len(allows) == 1 and allows[0].content["host"] == "api.anthropic.com" \
        and allows[0].content["capability"] == ecap, allows
    # the api key crossed only as an in-process header — still zero bytes durable.
    assert SENTINEL_KEY not in _world_dump(kk), \
        "a live call must leave NO secret bytes on the Weft"
    line("  (c) grant flow: durable inbox_item → human approve (same Morta spine) "
         "→ live call over an injected transport succeeds, wire_decision ALLOW "
         "provenance recorded, still zero secret bytes durable ✓")

    # ── (d) DOCTOR: honest and redacted ─────────────────────────────────────
    d = golive.doctor(kk)
    assert d["wire_armed"] is True, "the armed wire must be reported"
    g = next(g for g in d["egress"] if g["hosts"] == ["api.anthropic.com"])
    assert g["approved"] is True and g["held"] is True, g
    assert d["pending_grants"] == [], d["pending_grants"]
    assert {s["name"] for s in d["secrets"]} == {"anthropic", "stripe_test"}
    assert d["engines"]["live"] == [], "no live-engine registry today → honest []"
    blob = repr(d) + "\n".join(golive.doctor_lines(kk))
    assert SENTINEL not in blob and SENTINEL_KEY not in blob, \
        "doctor must never surface a secret value"
    assert d["brain"]["key"] in ("present", "absent"), d["brain"]
    line("  (d) doctor: wire ARMED, granted host listed approved, secret NAMES "
         "only, engines honestly none-live — and not one secret byte ✓")

    # ── (e) REVOCATION does not resurrect; empty boot touches nothing ───────
    kk.revoke(ecap)
    try:
        egress.live_transport(kk, agent, ecap, _open=fake_open)(
            "https://api.anthropic.com/v1/messages", {}, "{}")
        raise AssertionError("a revoked grant must close the wire")
    except wire.EgressDenied as e:
        assert "revoked" in str(e), e
    re_req = golive.request_grant(kk, "api.anthropic.com")
    assert re_req["status"] == "pending", \
        f"re-requesting a REVOKED host must demand a FRESH human decision: {re_req}"
    assert ecap not in kk.approvals, \
        "the stale approval must be retracted — no silent resurrection"
    kb = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    lam0 = kb.weft.lamport
    assert golive.boot(kb, environ={"PATH": "/usr/bin"}) == [] \
        and kb.weft.lamport == lam0, "no key → boot must touch NOTHING"
    # the real run.py path: key exported BEFORE the kernel exists → the factory
    # picks ModelBrain, and boot must say the grant is what's missing.
    saved = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = SENTINEL_KEY
    try:
        km = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
        assert isinstance(km.brain, ModelBrain), "the existing factory picks the brain"
        boot_lines = golive.boot(km, environ={"ANTHROPIC_API_KEY": SENTINEL_KEY})
    finally:
        if saved is None:
            del os.environ["ANTHROPIC_API_KEY"]
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved
    assert boot_lines and SENTINEL_KEY not in repr(boot_lines), boot_lines
    assert any("NOT live" in ln for ln in boot_lines), \
        "boot without an approved grant must say exactly what is missing"
    assert SENTINEL_KEY not in _world_dump(km), \
        "boot intake must leave zero secret bytes durable"
    assert isinstance(RuleBrain(), RuleBrain)   # the offline default stays the floor
    line("  (e) revoke closes the wire; a re-request queues a fresh human "
         "decision (stale approval retracted); keyless boot appends nothing; "
         "keyed boot reports redacted + names what's missing ✓")

    line("  → the go-live rail is operator-shaped and fail-closed: secrets are "
         "APPLIED in the broker (never on the Weft), an egress grant exists only "
         "as a human-approved inbox decision, every live byte passes the wire "
         "gate with provenance, and the doctor never lies and never leaks.")
