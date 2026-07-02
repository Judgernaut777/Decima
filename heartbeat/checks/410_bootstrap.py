"""§11 BOOTSTRAP — the First Heartbeat: intent → quarantined build → Reckoner evidence →
trusted attestation → grant → INVOKE (real sandboxed code) → retract → deny → replay.

This is NONA_RECKONER §11 end-to-end, the acceptance target of the forge-real loop. It
exercises Nona, Decima, Morta, Weft, Weave, authorization, receipts, and replay WITHOUT any
unsafe real-world effect — a PURE deterministic text-normalization capability, authored from
intent, built content-addressably in quarantine, evaluated across the verifier hierarchy,
promoted by a TRUSTED attestation, granted to ANOTHER agent, invoked to a REAL result (the
generated code actually runs, sandboxed), then rolled back so the invocation is denied — and
the entire history replays to the SAME state_root().

Deterministic + offline: fresh Kernel, INJECTED fake codegen, SEEDED fuzz, no network/clock/key.
Contract: run(k, line). Fail loud (assert).
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.weft import ASSERT
from decima.weave import Weave
from decima.hashing import content_id, nfc
from decima import candidate as C
from decima import reckoner as R
from decima import promotion as P
from decima import isolation, executor

INTENT = "normalize user text: collapse whitespace and lowercase"


def run(k, line):
    line("\n== §11 BOOTSTRAP (intent → quarantine → evidence → promote → invoke → retract → replay) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    P.install_trust_anchors(kk)

    # 1. An agent ASSERTS a candidate pure capability (deterministic text normalization),
    #    from intent, via an INJECTED deterministic codegen fake. ─────────────────────────
    cand = C.author_candidate(kk, INTENT, C.fake_normalizer_codegen,
                              declared_effect_class="pure", name="normalize_text")
    cand_cell = kk.weave().get(cand["cell"])
    assert cand_cell.type == "candidate" and cand_cell.content["lifecycle"] == "QUARANTINED", \
        "the candidate must be born QUARANTINED"

    # 2. The candidate is built content-addressably in quarantine (§3/§4 Law 4). ──────────
    assert cand["implementation_digest"] == content_id(nfc(cand["source_blobs"])), \
        "implementation_digest must be the content-address of the generated source"
    q = cand_cell.content["quarantine"]
    assert q["sandbox_only"] and q["no_outward_effects"] and q["network_allow"] == [], \
        "the candidate must carry the §3 quarantine baseline"

    # 3. The Reckoner executes tests, property checks, and a hostile-input case — the
    #    generated code runs ONLY through the isolation seam (footprint bound). ────────────
    seen = []
    real_spawn = isolation.spawn_worker

    def spy(argv, **kw):
        seen.append(list(argv))
        return real_spawn(argv, **kw)

    isolation.spawn_worker = spy
    try:
        ev = R.evaluate(kk, cand)
    finally:
        isolation.spawn_worker = real_spawn
    assert ev.promote_eligible is True, f"the pure normalizer must be promote-eligible: {ev.reason}"
    assert ev.metrics["deterministic_pass"] == ev.metrics["deterministic_cases"] >= 1
    assert ev.metrics["hostile_contained"] == ev.metrics["hostile_cases"] >= 1
    assert ev.metrics["property_pass"] == ev.metrics["property_cases"] >= 1
    assert seen, "the Reckoner must have run the generated code in the sandbox (no worker spawned)"
    assert kk.weave().get(ev.result_cell).type == R.EVALUATION_RESULT, "no EvaluationResult on the Weft"
    line("  1–3: authored from intent, born QUARANTINED + content-addressed, Reckoner ran "
         "seeded tests + a property check + a hostile-input case in the sandbox → evidence ✓")

    # 4. An independent, TRUSTED attestation satisfies promotion policy (pure ⇒ Reckoner). ─
    res = P.promote(kk, cand, ev, tier="pure")
    assert res.promoted is True and res.to_state == "PROMOTED", str(res)
    cap = kk.weave().get(res.cap_id)
    assert cap.content.get("quarantined") is False, "a trusted attestation must lift quarantine"
    # The promotion granted an EDGE to the immutable impl digest; it did NOT mutate the code.
    assert cap.content["implementation_digest"] == cand["implementation_digest"]
    assert cap.content["impl"]["source_blobs"] == cand["source_blobs"], "candidate code must be unmutated"
    line("  4: a TRUSTED (Reckoner) attestation satisfies the pure-tier policy and lifts "
         "quarantine — granting an edge to the immutable impl digest, never mutating code ✓")

    # 5. A grant exposes the promoted capability to ANOTHER agent. ────────────────────────
    other = kk.keyring.mint("bootstrap_consumer", "agent")
    other_id = content_id({"agent": "bootstrap_consumer"})
    kk.weft.append(kk.root.id, ASSERT, {"cell": other_id, "type": "agent",
        "content": {"principal": other.id, "objective": "use the promoted organ",
                    "envelope": [], "sandbox": False}})
    P.grant_to(kk, res.cap_id, other_id)
    assert res.cap_id in kk.weave().get(other_id).content["envelope"], "the grant must reach the agent"

    # 6. That agent INVOKEs it successfully — the GENERATED CODE ACTUALLY RUNS (sandboxed)
    #    to a REAL result, not a stub. ─────────────────────────────────────────────────────
    invoked = kk.invoke(kk.weave().get(other_id), res.cap_id, {"text": "  Hello   WORLD  "})
    assert invoked.get("status") == "SUCCEEDED", f"the invoke must succeed: {invoked}"
    assert invoked["ok"]["out"] == "hello world", \
        f"the generated code must produce a REAL normalized result, not a stub: {invoked}"
    assert invoked["ok"]["ran"] is True and invoked["ok"]["isolation"]["no_new_privs"] is True, \
        "the real result must come from code that ran inside the isolation seam"
    assert invoked["signer"] == other.id, "the INVOKE must be signed by the OTHER agent's own key"
    line("  5–6: the grant exposes it to another agent, which INVOKEs it — the generated "
         "code runs sandboxed to a REAL result ('  Hello   WORLD  ' → 'hello world') ✓")

    # 7. Retract the promotion → the invocation is then DENIED; replay to the SAME root. ──
    P.rollback(kk, res.cap_id, reason="§11 rollback")
    denied = kk.invoke(kk.weave().get(other_id), res.cap_id, {"text": "hi"})
    assert "denied" in denied, f"after rollback the invocation must be denied: {denied}"
    # An incident Cell records the rollback (contain + compensate; never claims to undo).
    incidents = [c for c in kk.weave().of_type("incident")
                 if any(e["rel"] == "incident_for" and e["dst"] == res.cap_id for e in c.edges_out)]
    assert incidents, "rollback must assert an incident Cell linking the capability"

    # Replay the ENTIRE history to the SAME state_root — a genesis fold and an independent
    # re-fold of the same Weft must be byte-identical (FOLD §11 #7: deterministic replay).
    root_a = kk.weave().state_root()
    root_b = Weave.fold(kk.weft).state_root()
    assert root_a == root_b, "replay diverged — the history does not fold to the same state root"
    line("  7: after rollback the invocation is DENIED (incident recorded); the entire "
         "history replays to the SAME state_root() ✓")

    line("  → First Heartbeat complete: Nona authored, the Reckoner judged, Morta's tiered "
         "signature promoted, another agent ran real sandboxed code, rollback denied it, and "
         "the whole compounding loop replays deterministically.")
