"""Nona N5: the runnable organ, and the two digest bindings that keep it honest.

The claim this wave makes true is that a promoted candidate can actually RUN — through the
kernel's authorized invoke seam, in the same jail every other untrusted effect uses, with the
code that runs provably the code that was evaluated.

So these tests are mostly about the ways it must NOT run. A quarantined organ never reaches
the executor. A candidate whose source was edited after evaluation is refused before a worker
is spawned. A capability whose worker digest is stale is refused by the jail itself. An
effect with no declared handler is refused rather than defaulted. A `network` organ is
refused for having no executor at all — a different fact from being unapproved, reported
differently, because a prompt for something that cannot run only teaches people to click yes.

Every assertion here is written to go RED if the property breaks: the runner is a spy that
records whether it was called, the digest tests tamper with real Cells, and the refusal tests
check the refusal CODE rather than merely that something went wrong.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

import pytest

from decima.kernel import capability, model
from decima.kernel import invoke as kinvoke
from decima.kernel.crypto import Keyring
from decima.kernel.invoke import EffectOutcome
from decima.kernel.weave import Cell, Weave
from decima.kernel.weft import Weft
from decima.runtime import cells
from decima.services.nona import anchors, candidate, executor, promotion, reckoner
from decima.services.nona.reckoner import Metrics
from decima.workers import PROVIDER, IsolationError, WorkerRequest, compute_digest, run_worker

# A deterministic pure organ: integers in, integer out, no clock, no randomness.
ADD_ONE = "def main(x):\n    return int(x) + 1\n"
RAISES = "def main(x):\n    raise ValueError('the organ blew up')\n"
WRONG_TYPE = "def main(x):\n    return 'not an int'\n"


@dataclass
class World:
    weft: Weft
    keyring: Keyring
    root: str
    reckoner: str
    holder: str
    agent: str
    capability: str
    candidate: str
    promotion: str

    def weave(self) -> Weave:
        return Weave.fold(self.weft)

    def agent_cell(self) -> Cell:
        cell = self.weave().get(self.agent)
        assert cell is not None
        return cell


def _bootstrap(
    source: str = ADD_ONE,
    *,
    tier: str = anchors.PURE,
    output_schema: dict[str, Any] | None = None,
    promote: bool = True,
    sandbox: bool = False,
) -> World:
    """A full loop: anchors → candidate → capability → evaluation → promotion → grant."""
    keyring = Keyring(seed=bytes(32))
    weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), keyring)
    root = keyring.mint("root", "root").id
    reck = keyring.mint(anchors.RECKONER_NAME, "reckoner").id
    holder = keyring.mint("holder", "operator").id
    anchors.install_trust_anchors(weft, root, reckoner=reck)

    proposed = candidate.propose_candidate(
        weft,
        reck,
        intent="add one",
        declared_effect_class=tier,
        source=source,
        output_schema=output_schema if output_schema is not None else {"type": "int"},
    )
    built = executor.build_capability(
        weft,
        Weave.fold(weft),
        reck,
        candidate=proposed["cell"],
        tier=tier,
        name="add_one",
        grantee=holder,
        granter=reck,
    )
    agent = cells.create_agent(
        weft,
        root,
        objective="use the organ",
        principal=holder,
        capability_grant_ids=[built["capability"]],
        sandbox=sandbox,
    )

    promotion_cell = ""
    if promote:
        verdict = reckoner.gate(
            Metrics(
                deterministic_cases=2,
                deterministic_pass=2,
                hostile_cases=1,
                hostile_contained=1,
            )
        )
        evaluation = reckoner.record_result(
            weft,
            reck,
            candidate_cell=proposed["cell"],
            suite_cell="suite:s",
            implementation_digest=proposed["implementation_digest"],
            verdict=verdict,
            containment={
                "no_new_privs": True,
                "network_denied": True,
                "chroot": True,
                "namespaces": True,
                "matrix_version": 1,
            },
        )
        promotion_cell = promotion.promote(
            weft,
            Weave.fold(weft),
            reck,
            capability=built["capability"],
            candidate=proposed["cell"],
            evaluation=evaluation,
            tier=tier,
        )["promotion"]

    return World(
        weft=weft,
        keyring=keyring,
        root=root,
        reckoner=reck,
        holder=holder,
        agent=agent,
        capability=built["capability"],
        candidate=proposed["cell"],
        promotion=promotion_cell,
    )


class _Spy:
    """A runner that records whether the jail was ever asked to spawn anything."""

    def __init__(self) -> None:
        self.calls: list[WorkerRequest] = []

    def __call__(self, request: WorkerRequest, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(request)
        raise AssertionError("the worker must not have been reached on this path")


def _receipt(world: World, result: dict[str, Any]) -> dict[str, Any]:
    cell = world.weave().get(result["result_cell"])
    assert cell is not None, "an authorized invocation must always leave a receipt"
    return dict(cell.content)


# ── the organ actually runs ──────────────────────────────────────────────────
def test_a_promoted_organ_runs_end_to_end_through_the_authorized_seam() -> None:
    world = _bootstrap()
    result = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 41}
    )

    assert result["status"] == "SUCCEEDED"
    assert result["ok"] is True
    assert result["refusal"] == ""

    receipt = _receipt(world, result)
    assert receipt["of"] == result["invoke_event"]
    assert receipt["cap"] == world.capability
    assert receipt["effect"] == executor.GENERATED_CODE
    # The value itself is never recorded — only its content address, which must be the
    # digest of the answer the organ was supposed to give.
    assert receipt["output_digest"] == executor.output_digest(42)
    assert receipt["provenance"]["contract"] == "declared"
    # And the invocation is on the log as an INVOKE, folded against the grant.
    assert [i.cap for i in world.weave().invocations] == [world.capability]


def test_the_seam_dispatches_only_the_effect_the_capability_declares() -> None:
    """No handler ⇒ refused. There is no ambient executor behind the invoke seam."""
    world = _bootstrap()
    result = kinvoke.invoke(
        world.weft,
        world.weave(),
        world.keyring,
        world.agent_cell(),
        world.capability,
        {"x": 1},
        effects={},
    )
    assert result["refusal"] == kinvoke.NO_HANDLER
    assert result["status"] == "FAILED"
    assert result["ok"] is False


# ── quarantine is enforced BEFORE the executor, not inside it ────────────────
def test_a_quarantined_capability_never_reaches_the_executor() -> None:
    world = _bootstrap(promote=False)
    spy = _Spy()

    result = executor.invoke_organ(
        world.weft,
        world.weave(),
        world.keyring,
        world.agent_cell(),
        world.capability,
        {"x": 1},
        run=spy,
    )

    assert result["code"] == capability.DenialCode.QUARANTINED
    assert spy.calls == [], "a quarantined organ must not reach the worker at all"
    # Nothing was written: no INVOKE event, so no receipt and nothing for the canary to fold.
    assert world.weave().invocations == []
    assert world.weave().of_type(kinvoke.RESULT) == []


def test_a_rolled_back_organ_is_denied_again_at_the_seam() -> None:
    """Demotion is enforced at the same door promotion opened — no second mechanism."""
    world = _bootstrap()
    spy = _Spy()
    promotion.rollback(world.weft, world.root, world.promotion, reason="test")

    result = executor.invoke_organ(
        world.weft,
        world.weave(),
        world.keyring,
        world.agent_cell(),
        world.capability,
        {"x": 1},
        run=spy,
    )
    assert result["code"] == capability.DenialCode.QUARANTINED
    assert spy.calls == []


# ── binding one: the code that runs is the code that was evaluated ───────────
def test_tampering_with_the_candidate_source_is_refused_before_any_worker_spawns() -> None:
    """Edit the source after evaluation and the organ stops running, loudly.

    The capability keeps the digest its evaluation cited; the candidate Cell now holds
    different bytes. That mismatch is caught in the blob domain, in this process, before
    anything is handed to a jail.
    """
    world = _bootstrap()
    cell = world.weave().get(world.candidate)
    assert cell is not None
    model.assert_content(
        world.weft,
        world.reckoner,
        world.candidate,
        candidate.CANDIDATE,
        {**cell.content, "source": "def main(x):\n    return 999\n"},
    )
    spy = _Spy()

    result = executor.invoke_organ(
        world.weft,
        world.weave(),
        world.keyring,
        world.agent_cell(),
        world.capability,
        {"x": 1},
        run=spy,
    )

    assert result["refusal"] == executor.IMPL_UNBOUND
    assert result["status"] == "FAILED"
    assert result["ok"] is False
    assert spy.calls == [], "tampered source must never be handed to a worker"
    assert "not the code that was evaluated" in _receipt(world, result)["error"]


def test_a_capability_that_names_no_candidate_cannot_resolve_an_implementation() -> None:
    """The hand-rolled capability shape N4's tests used has no path to source — say so."""
    world = _bootstrap(promote=False)
    model.assert_content(
        world.weft,
        world.root,
        "capability:bare",
        executor.CAPABILITY,
        {"effect": executor.GENERATED_CODE, "declared_effect_class": anchors.PURE},
    )
    with pytest.raises(executor.OrganRefused, match="names no candidate"):
        executor.resolve_implementation(world.weave(), "capability:bare")


# ── binding two: the jail re-computes the digest in its OWN domain ───────────
def test_a_stale_worker_digest_is_refused_by_the_jail_itself() -> None:
    """The two digest domains are independent, and this proves the second one is live.

    A forger who understands only the blob domain can rewrite the candidate's source AND the
    capability's `implementation_digest` so the first binding passes. The capability's
    `worker_digest` is in a different domain (`kind="worker-impl"`, raw bytes, no NFC), so it
    is now stale — and `run_worker` recomputes it and refuses. Deliberately run against the
    REAL worker: the point is that the primitive enforces this, not that we re-check it here.
    """
    world = _bootstrap()
    tampered = "def main(x):\n    return 999\n"
    cell = world.weave().get(world.candidate)
    cap = world.weave().get(world.capability)
    assert cell is not None and cap is not None
    model.assert_content(
        world.weft,
        world.reckoner,
        world.candidate,
        candidate.CANDIDATE,
        {**cell.content, "source": tampered},
    )
    model.assert_content(
        world.weft,
        world.reckoner,
        world.capability,
        executor.CAPABILITY,
        {**cap.content, "implementation_digest": candidate.implementation_digest(tampered)},
    )
    # The first binding now passes — which is exactly why the second one has to exist.
    impl = executor.resolve_implementation(world.weave(), world.capability)
    assert impl.source == tampered
    assert impl.worker_digest != compute_digest(tampered)

    result = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 1}
    )
    assert result["refusal"] == executor.DIGEST_MISMATCH
    assert result["status"] == "FAILED"
    assert result["ok"] is False


# ── the network tier has no executor, and that is not a permission ───────────
def test_the_network_tier_is_refused_for_having_no_executor_not_for_lacking_approval() -> None:
    world = _bootstrap(tier=anchors.NETWORK, promote=False, sandbox=True)

    # A sandbox principal may reach a quarantined capability — so this invocation gets all
    # the way to the executor and is refused THERE, structurally.
    result = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 1}
    )
    assert "denied" not in result, "the refusal must come from the executor, not the ocap gate"
    assert result["refusal"] == executor.NO_EXECUTOR
    assert result["refusal"] != capability.DenialCode.APPROVAL_REQUIRED
    assert "nothing to approve" in result["error"]

    # The absence is structural at three levels, none of which is a policy toggle.
    assert anchors.NETWORK not in executor.TIER_PROFILES
    assert anchors.NETWORK not in anchors.SIGNABLE_TIERS
    assert promotion.signer_policy(anchors.NETWORK) == promotion.NOT_EXECUTABLE


def test_the_workspace_write_tier_is_refused_rather_than_run_as_something_weaker() -> None:
    """WORKSPACE "behaves as PURE" today, so promoting to it would run an organ that cannot do
    the thing it was evaluated to do — while telling the operator it can. Refuse instead."""
    world = _bootstrap(tier="workspace_write", promote=False, sandbox=True)
    result = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 1}
    )
    assert result["refusal"] == executor.NO_EXECUTOR
    assert "workspace_write" not in executor.TIER_PROFILES


def test_the_only_network_permitted_profile_is_refused_at_the_primitive() -> None:
    """`run_worker` refuses PROVIDER for EVERY caller — the fact the tier table reflects."""
    assert PROVIDER.network is True
    source = "def main():\n    return 1\n"
    request = WorkerRequest(
        invocation_id="i",
        job_id="j",
        effect=executor.GENERATED_CODE,
        implementation_digest=compute_digest(source),
        arguments={},
        lease={
            "step_id": "i",
            "worker": "w",
            "issued_frontier": 1,
            "expiry": 100,
            "attempt": 0,
            "idempotency_key": "k",
        },
        capability_proof={"holder": "p"},
    )
    with pytest.raises(IsolationError, match="egress mediation"):
        run_worker(request, source, "main", now=5, profile=PROVIDER)


# ── determinism: nothing host-variable, nothing random ───────────────────────
_ALLOWED_PROVENANCE = frozenset(
    {
        "tier",
        "profile",
        "contract",
        "isolation_reported",
        "no_new_privs",
        "namespaces",
        "fs_jail",
        "net_isolated",
    }
)


def _scalars_only(value: object) -> bool:
    if isinstance(value, bool) or isinstance(value, (int, str)) or value is None:
        return True
    if isinstance(value, dict):
        return all(isinstance(k, str) and _scalars_only(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_scalars_only(v) for v in value)
    return False


def test_the_receipt_carries_no_host_variable_provenance_and_no_floats() -> None:
    world = _bootstrap()
    result = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 41}
    )
    receipt = _receipt(world, result)

    assert set(receipt["provenance"]) <= _ALLOWED_PROVENANCE, (
        "the isolation manifest carries a mkdtemp path, a live fd list, env keys, rlimit "
        "read-backs and an inner pid; none of it may enter hashed content"
    )
    assert _scalars_only(receipt), "signed content is ints/strings/bools only — no floats"
    blob = repr(receipt)
    for poison in ("decima-worker-", "open_fds", "env_keys", "inner_pid", "rlimits", "cwd_jail"):
        assert poison not in blob


def test_a_handler_cannot_smuggle_a_manifest_into_the_receipt() -> None:
    """A misbehaving handler produces an honest refusal, not a poisoned (or missing) receipt."""
    world = _bootstrap()

    def rogue(_request: Any) -> EffectOutcome:
        return EffectOutcome(
            status="SUCCEEDED",
            ok=True,
            provenance={"isolation": {"cwd_jail": "/tmp/decima-worker-abc"}},
        )

    result = kinvoke.invoke(
        world.weft,
        world.weave(),
        world.keyring,
        world.agent_cell(),
        world.capability,
        {"x": 1},
        effects={executor.GENERATED_CODE: rogue},
    )
    assert result["refusal"] == kinvoke.HANDLER_CONTRACT
    assert result["ok"] is False
    receipt = _receipt(world, result)
    assert receipt["provenance"] == {}
    assert "decima-worker-" not in repr(receipt)


def test_a_handler_that_raises_still_leaves_an_honest_receipt() -> None:
    world = _bootstrap()

    def explodes(_request: Any) -> EffectOutcome:
        raise RuntimeError("kaboom")

    result = kinvoke.invoke(
        world.weft,
        world.weave(),
        world.keyring,
        world.agent_cell(),
        world.capability,
        {"x": 1},
        effects={executor.GENERATED_CODE: explodes},
    )
    assert result["refusal"] == kinvoke.HANDLER_RAISED
    assert "kaboom" in _receipt(world, result)["error"]


def test_two_identical_invocations_do_not_collapse_onto_the_same_bind() -> None:
    """The derived nonce carries a monotonic attempt drawn from the fold.

    Without it, invoking the same organ twice with the same arguments would produce the same
    nonce, the same invocation bind, and a captured proof that replays. This is the test that
    fails if the attempt counter is ever dropped.
    """
    world = _bootstrap()
    first = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 1}
    )
    second = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 1}
    )

    assert first["attempt"] == 0 and second["attempt"] == 1
    assert first["nonce"] != second["nonce"]
    assert first["invoke_event"] != second["invoke_event"]
    assert first["result_cell"] != second["result_cell"]
    assert len(world.weave().invocations) == 2


def test_a_float_cost_is_refused_before_anything_is_written() -> None:
    world = _bootstrap()
    before = len(list(world.weft.events()))
    with pytest.raises(ValueError, match="non-negative int"):
        executor.invoke_organ(
            world.weft,
            world.weave(),
            world.keyring,
            world.agent_cell(),
            world.capability,
            {"x": 1, "cost": 1.5},
        )
    assert len(list(world.weft.events())) == before, "a malformed cost must land no events"


# ── what the canary can and cannot see, stated as a test ─────────────────────
def test_an_organ_that_raises_is_a_definite_failure() -> None:
    world = _bootstrap(source=RAISES)
    result = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 1}
    )
    assert result["status"] == "FAILED"
    assert result["ok"] is False


def test_a_wrongly_typed_answer_is_a_semantic_failure_even_though_it_succeeded() -> None:
    """`ok` is computed against the declared contract, which is why it is not just `status`."""
    world = _bootstrap(source=WRONG_TYPE, output_schema={"type": "int"})
    result = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 1}
    )
    assert result["status"] == "SUCCEEDED", "the organ returned normally"
    assert result["ok"] is False, "but it returned the wrong shape"
    assert _receipt(world, result)["provenance"]["contract"] == "declared"


def test_an_organ_with_no_declared_contract_says_so_instead_of_implying_a_verdict() -> None:
    """The canary's honest blind spot, recorded on the receipt rather than left to inference."""
    world = _bootstrap(source=WRONG_TYPE, output_schema={})
    result = executor.invoke_organ(
        world.weft, world.weave(), world.keyring, world.agent_cell(), world.capability, {"x": 1}
    )
    assert result["ok"] is True
    assert _receipt(world, result)["provenance"]["contract"] == "none"


def test_the_contract_check_distinguishes_a_bool_from_an_int() -> None:
    """A bool IS an int in Python; `type: int` never meant `True`."""
    assert executor.conforms(True, {"type": "int"}) is False
    assert executor.conforms(1, {"type": "int"}) is True
    assert executor.conforms("x", {"type": "any"}) is None
    assert executor.conforms("x", {}) is None
