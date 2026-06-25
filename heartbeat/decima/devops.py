"""DEVOPS1 — a CI/CD pipeline as a PLAN1 DAG whose DEPLOY is a Morta-gated effect.

A `pipeline` is nothing new: it is a PLAN1 acyclic step DAG (`build → test → deploy`)
authored by Decima on the Weft. Running it *structures* the same safety the kernel
already enforces — this module invents no authority, it COMPOSES two existing ones:

  • **planning (PLAN1)** shapes the stages as an acyclic `depends_on` DAG. A cyclic
    pipeline is REJECTED at `define_pipeline` time (planning's `plan()` fails closed),
    never half-committed as a runnable graph.
  • **the Morta-gated, sandboxed executor effect (PAY1's pattern)** runs the stages.
    `build` and `test` are sandboxed *stub* steps — pure compute, no network, no
    process, no real build/test runner. `deploy` is the outward, possibly-irreversible
    effect, so it is the `devops.deploy` capability: `requires_approval` (Morta) +
    a sandbox profile allowing ONLY that effect. It is DENIED until a human/policy
    approves, then runs as a deterministic stub (no real deploy) and lands a full
    EffectReceipt on the Weft (audit).

Laws this module upholds:
  - **A DEPLOY is an outward, possibly-irreversible effect.** Morta-gated
    (`requires_approval`) + sandboxed + audited as an EffectReceipt. Build/test never
    cross the box boundary (sandboxed compute stubs).
  - **No ambient authority.** The deploy effect is a forged capability granted to
    Decima; `authorize` + Morta gate every invoke. Nothing here grants itself power.
  - **Ints, not floats.** Stage ordinals/counts are ints. No floats reach the log.
  - **Fail closed.** A cyclic stage DAG raises before anything runs; an unapproved
    deploy is denied (no effect), recorded, and the stage stays `pending`.
  - **Provenance on the Weft.** The pipeline, its stages, the membership/dependency
    edges, every stage result, and the deploy receipt are all Decima's own assertions.

Pure composition: uses `planning` + the public kernel/executor APIs
(`integrate_tool`, `invoke`, `approve`) and `model`/`hashing`. No core edit, no
edit to planning or the executor.
"""
from __future__ import annotations

from decima import executor, planning
from decima.hashing import nfc
from decima.model import assert_content

# Effect class for an outward deploy: it leaves the box and is possibly
# irreversible, so it is gated like FINANCIAL — Morta + sandbox + audit.
DEPLOY = "DEPLOY"
DEPLOY_EFFECT = "devops.deploy"
RESULT = "result"                 # the EffectReceipt cell type the kernel asserts

PIPELINE = "pipeline"             # a `plan` whose scope marks it a CI/CD pipeline
STAGE_RESULT = "stage_result"     # per-stage outcome cell on the Weft

# The canonical stages of the pipeline DAG, in order.
BUILD = "build"
TEST = "test"
DEPLOY_STAGE = "deploy"

# Stage outcomes recorded on the Weft.
PASSED = "passed"
DENIED = "denied"
DEPLOYED = "deployed"
PENDING = "pending"

# build/test run sandboxed as these *pure-compute* stub effects — no network, no
# process, no fs. Their sandbox profile pins the allowlist to themselves only.
_STUB_EFFECTS = {BUILD: "devops.build", TEST: "devops.test"}


def _build_handler(_impl, args: dict) -> dict:
    """The build stage — a deterministic, sandboxed stub. A real handler would invoke
    a build runner inside the sandbox; here it confirms a reproducible artifact id so
    the receipt carries it for audit. Pure compute: no network/process/fs."""
    name = nfc(str(args.get("pipeline", "")))
    return {"out": f"built {name}", "artifact": f"artifact:{name}", "stage": BUILD}


def _test_handler(_impl, args: dict) -> dict:
    """The test stage — a deterministic, sandboxed stub standing in for a test runner.
    Returns a pass with a fixed count so the receipt is auditable. Pure compute."""
    name = nfc(str(args.get("pipeline", "")))
    return {"out": f"tested {name}", "tests": 1, "stage": TEST}


def _deploy_handler(target: str, args: dict) -> dict:
    """The deploy effect — the outward, possibly-irreversible action, here a
    DETERMINISTIC STUB (no real deploy). A real handler ships the approved artifact to
    `target` over the deploy-only sandbox; this confirms it deterministically and
    echoes the artifact + target into its output so the EffectReceipt carries them for
    audit. A bad request (missing artifact) raises ExecError → a FAILED receipt: a
    definite no-effect, nothing shipped."""
    artifact = nfc(str(args.get("artifact", "")))
    if not artifact:
        raise executor.ExecError("deploy requires an artifact to ship")
    return {"out": f"deployed {artifact} to {target}", "artifact": artifact,
            "target": target, "stage": DEPLOY_STAGE}


def install_pipeline_effects(k, *, target: str = "stub-target") -> dict:
    """Register the three pipeline effects and forge their capabilities, granted to
    Decima. build/test are sandboxed compute stubs (no Morta — they never cross the
    box boundary). deploy is the outward effect: Morta `requires_approval` + a sandbox
    profile allowing ONLY the deploy effect. Idempotent across stages of one kernel.
    Returns {build, test, deploy} → capability ids."""
    # build + test: sandboxed pure-compute stubs. The allowlist pins each to its own
    # effect; network off (a build/test that reaches the network is out of profile).
    build_cap = k.integrate_tool(
        _STUB_EFFECTS[BUILD], lambda _impl, args: _build_handler(_impl, args),
        caveats={"effect_class": "COMPUTE",
                 "sandbox": {"effects": [_STUB_EFFECTS[BUILD]], "network": False}})
    test_cap = k.integrate_tool(
        _STUB_EFFECTS[TEST], lambda _impl, args: _test_handler(_impl, args),
        caveats={"effect_class": "COMPUTE",
                 "sandbox": {"effects": [_STUB_EFFECTS[TEST]], "network": False}})
    # deploy: the Morta-gated, sandboxed outward effect. requires_approval denies it
    # until approved; the sandbox allows ONLY this effect under the capability.
    deploy_cap = k.integrate_tool(
        DEPLOY_EFFECT, lambda _impl, args: _deploy_handler(target, args),
        caveats={"effect_class": DEPLOY,
                 "requires_approval": True,                       # Morta gate
                 "sandbox": {"effects": [DEPLOY_EFFECT], "network": True}})
    return {BUILD: build_cap, TEST: test_cap, DEPLOY_STAGE: deploy_cap}


def define_pipeline(k, name: str, steps=None, *, author: str | None = None) -> dict:
    """Define a `pipeline` — a PLAN1 acyclic DAG of stages — on the Weft.

    Default shape is the canonical `build → test → deploy` chain (test depends on
    build, deploy depends on test), each stage naming the executor effect it runs as
    its capability hint. A caller may pass their own `steps` (planning step specs);
    they are validated ACYCLIC by `planning.plan` before anything is committed — a
    cyclic pipeline raises ValueError and writes no stage cells (fail closed).

    The pipeline is a `plan` scoped `pipeline`; its stages are `plan_step` cells. All
    are Decima's own trusted assertions, so they carry full provenance. No effect runs
    here — defining a pipeline only *structures* it.

    Returns planning's {plan, steps:{key:id}, topo, objective}.
    """
    name = nfc(str(name))
    if steps is None:
        steps = [
            {"key": BUILD, "objective": f"build {name}",
             "capability": _STUB_EFFECTS[BUILD]},
            {"key": TEST, "objective": f"test {name}", "depends_on": [BUILD],
             "capability": _STUB_EFFECTS[TEST]},
            {"key": DEPLOY_STAGE, "objective": f"deploy {name}", "depends_on": [TEST],
             "capability": DEPLOY_EFFECT},
        ]
    # planning.plan validates the DAG acyclic and fails closed on a cycle.
    return planning.plan(k, name, steps, author=author, scope=PIPELINE)


def _stage_result_id(pipeline: dict, stage: str):
    from decima.hashing import content_id
    return content_id({"stage_result": nfc(stage), "of": pipeline["plan"]})


def _record_stage(k, pipeline: dict, stage: str, status: str, detail: dict,
                  *, author: str | None = None) -> str:
    """Record a stage's outcome as a `stage_result` cell on the Weft (LWW per stage),
    edged from its plan step for provenance. Decima's own assertion."""
    from decima.model import assert_edge
    author = author or k.decima_agent_id
    sid = _stage_result_id(pipeline, stage)
    content = {"pipeline": pipeline["plan"], "stage": nfc(stage), "status": status,
               **{kk: vv for kk, vv in detail.items()}}
    assert_content(k.weft, author, sid, STAGE_RESULT, content)
    step_id = pipeline["steps"].get(stage)
    if step_id is not None:
        assert_edge(k.weft, author, step_id, "result_of_stage", sid)
    return sid


def run_stage(k, agent_cell, pipeline: dict, stage: str, caps: dict,
              *, author: str | None = None) -> dict:
    """Run one pipeline stage and record its outcome on the Weft.

    build/test: invoke the sandboxed compute stub effect, mark the plan step done, and
    record a `passed` stage_result. deploy: invoke the Morta-gated deploy effect — it
    is DENIED until the capability is approved (the step stays pending, a `denied`
    stage_result is recorded, NO effect happened); once approved it runs the stub,
    lands an EffectReceipt, marks the step done, and records a `deployed` result.

    A stage's prerequisites (the `depends_on` DAG) must be `done` first — a deploy
    cannot jump ahead of build/test. Returns a dict with {stage, status, ...}.
    """
    stage = nfc(str(stage))
    author = author or k.decima_agent_id
    step_id = pipeline["steps"].get(stage)
    if step_id is None:
        raise ValueError(f"unknown pipeline stage: {stage!r}")

    # Enforce the DAG: every prerequisite stage must be done before this one runs.
    ready = {b["key"] for b in planning.ready_steps(k, pipeline["plan"])}
    if stage not in ready:
        raise ValueError(
            f"stage {stage!r} is not ready — its prerequisites are not yet done")

    if stage in (BUILD, TEST):
        res = k.invoke(agent_cell, caps[stage], {"pipeline": pipeline["objective"]})
        if "denied" in res:
            raise RuntimeError(f"sandboxed {stage} stub denied: {res['denied']}")
        out = res["ok"]
        planning.mark_done(k, step_id, author=author, result=out.get("out"))
        detail = {"receipt": res["result_cell"]}
        if stage == BUILD:
            detail["artifact"] = out.get("artifact")
        _record_stage(k, pipeline, stage, PASSED, detail, author=author)
        return {"stage": stage, "status": PASSED, "receipt": res["result_cell"],
                "out": out.get("out"), "artifact": out.get("artifact")}

    if stage == DEPLOY_STAGE:
        # The artifact to ship comes from the recorded build result (provenance).
        build_res = k.weave().get(_stage_result_id(pipeline, BUILD))
        artifact = build_res.content.get("artifact") if build_res else None
        res = k.invoke(agent_cell, caps[DEPLOY_STAGE],
                       {"artifact": artifact, "pipeline": pipeline["objective"]})
        if "denied" in res:                                   # Morta: not yet approved
            _record_stage(k, pipeline, DEPLOY_STAGE, DENIED,
                          {"reason": res["denied"]}, author=author)
            return {"stage": DEPLOY_STAGE, "status": DENIED, "denied": res["denied"]}
        out = res["ok"]
        planning.mark_done(k, step_id, author=author, result=out.get("out"))
        _record_stage(k, pipeline, DEPLOY_STAGE, DEPLOYED,
                      {"receipt": res["result_cell"], "artifact": out.get("artifact"),
                       "target": out.get("target")}, author=author)
        return {"stage": DEPLOY_STAGE, "status": DEPLOYED,
                "receipt": res["result_cell"], "out": out.get("out"),
                "target": out.get("target")}

    raise ValueError(f"unrunnable stage: {stage!r}")


def status(k, pipeline: dict) -> dict:
    """Fold the pipeline's per-stage status from the Weft.

    For each stage (in DAG topological order) returns its recorded `stage_result`
    status (`passed` / `denied` / `deployed`) or `pending` if it has not run. Derived
    from the log, so it is deterministic and time-travelable. Also folds planning's
    plan_status for the overall done/pending counts.
    """
    w = k.weave()
    stages = []
    for sid in planning.topological_order(k, pipeline["plan"]):
        step = w.get(sid)
        key = step.content.get("key")
        rc = w.get(_stage_result_id(pipeline, key))
        stages.append({
            "stage": key,
            "status": rc.content.get("status") if rc is not None else PENDING,
            "step_status": step.content.get("status", PENDING),
        })
    plan = planning.plan_status(k, pipeline["plan"])
    deployed = any(s["stage"] == DEPLOY_STAGE and s["status"] == DEPLOYED
                   for s in stages)
    return {"pipeline": pipeline["plan"], "objective": pipeline["objective"],
            "stages": stages, "complete": bool(plan["complete"]),
            "deployed": deployed}
