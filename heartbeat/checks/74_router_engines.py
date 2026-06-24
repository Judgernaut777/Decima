"""C2 — Router → engines: a routed tier selects an engine to generate a candidate,
which is then verified deterministically (when a checker exists) or judged by a
critic fallback. Offline-safe (stub engines + stub judge); the router still confers
ZERO authority."""
import os
import tempfile

from decima import router as R
from decima import verifier as V
from decima.agent import Task, TaskRun, run_task
from decima.kernel import Kernel


def run(k, line):
    line("\n== ROUTER → ENGINES (tier picks an engine · verify-or-judge · ZERO authority) ==")

    # A 'competent small model' stub for the demo: it actually performs transform
    # tasks (what a real local model behind the engine seam would do); otherwise it
    # tags the prompt so we can see which tier/engine ran. Deterministic + offline.
    def competent(prompt, descriptor, model, tier):
        if descriptor and descriptor.kind == "transform":
            op, _, inp = prompt.partition(":")
            fn = V._TRANSFORMS.get(op.strip())
            if fn:
                return fn(inp.strip())
        return f"[{tier}·{model}] {prompt}"

    rt = R.Router(engines=R.default_engines(fn=competent))

    # 1) Tasks route to engines — each tier resolves to a concrete engine+model.
    routed = []
    for kind, desc in [
        ("transform", R.describe_task("upper: hello")),
        ("qa",        R.TaskDescriptor(kind="qa", needs_context=True)),
        ("plan",      R.TaskDescriptor(kind="plan", stakes="high")),
    ]:
        routing = rt.route(desc)
        eng = rt.engine_for(routing)
        routed.append((kind, routing.tier, eng.model))
        line(f"  {kind:10s} → tier {routing.tier:18s} → engine {eng.model}")
    assert len({t for _, t, _ in routed}) >= 2, "expected distinct tiers/engines"

    # 2) A verifiable task runs its DETERMINISTIC verifier (recompute + compare).
    okrun = run_task(Task(R.describe_task("upper: hello"), prompt="upper: hello",
                          verifier="transform", spec={"op": "upper", "input": "hello"}), rt)
    assert okrun.output == "HELLO"
    assert okrun.verdict.deterministic and okrun.verdict.ok, okrun.verdict
    assert okrun.tier == R.LOCAL_SMALL, okrun.tier      # cheap lane + a checker
    line(f"  verifiable PASS: engine→{okrun.output!r}; verdict={okrun.verdict.method} "
         f"ok={okrun.verdict.ok} ({okrun.verdict.detail})")

    # 3) The verifier actually GUARDS: a wrong engine output is caught, not trusted.
    badrt = R.Router(engines=R.default_engines(fn=lambda p, d, m, t: "WRONG"))
    badrun = run_task(Task(R.describe_task("upper: hello"), prompt="upper: hello",
                           verifier="transform", spec={"op": "upper", "input": "hello"}), badrt)
    assert badrun.verdict.deterministic and not badrun.verdict.ok, badrun.verdict
    line(f"  verifiable FAIL caught: engine→{badrun.output!r}; verdict ok={badrun.verdict.ok} "
         f"— a wrong candidate is rejected, not believed")

    # 4) A NON-verifiable task (no deterministic checker) falls back to judge/critic.
    judged = run_task(Task(R.TaskDescriptor(kind="summarize", stakes="medium"),
                           prompt="summarize: the loom weaves cloth from many threads"), rt)
    assert judged.verdict.method == "judge", judged.verdict
    assert judged.verdict.ok and judged.verdict.score is not None
    line(f"  non-verifiable → judge fallback: method={judged.verdict.method} "
         f"ok={judged.verdict.ok} score={judged.verdict.score}")

    # 5) ZERO authority — structural: a TaskRun/Routing carries no capability/grant.
    for attr in ("cap", "grant", "principal", "authority"):
        assert not hasattr(okrun, attr) and not hasattr(okrun.routing, attr), attr

    # 6) ZERO authority — operational: routing+generating+verifying an OUTWARD task
    #    grants nothing. On a fresh kernel the publish INVOKE is still Morta-denied.
    pub = run_task(Task(R.describe_task("publish: the loom holds"),
                        prompt="publish: the loom holds"), rt)
    k2 = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    out = k2.say("publish: routed, engined, judged — but unapproved")
    denied = any("denied" in ln for ln in out)
    assert denied, "engine pipeline conferred authority — publish must stay Morta-gated"
    line(f"  zero authority: ran the engine+judge pipeline (tier={pub.tier}), yet the "
         f"publish INVOKE is DENIED on a fresh kernel — authorize() gates, not the router")
