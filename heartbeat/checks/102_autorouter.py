"""AR1 — auto-router: automatic, intelligent per-task model switching.

Proves the router now selects on cost / privacy / context size / refusal, logs the
deciding factor for each choice, and keeps `route()` back-compatible:
  - a private/sensitive task routes to a LOCAL engine (no egress);
  - a low-stakes routine task picks a CHEAP (small) model;
  - a large-context task takes the big-context / retrieval lane (token-aware);
  - a small model that REFUSES an authorized task auto-escalates to a capable engine;
  - every choice carries its deciding factor (the audit log).

The router confers ZERO authority throughout — refusal means a model declined to
GENERATE, not that any effect was permitted. Real engines are deferred (offline
stubs here). Contract: run(k, line). Fail loud.
"""
from decima import router as R


def run(k, line):
    line("\n== AUTO-ROUTER (per-task switching: privacy · cost · context · refusal) ==")
    rt = R.Router()

    # 1. private/sensitive → LOCAL engine (no egress).
    rp = rt.auto_route(R.TaskDescriptor(kind="summarize", privacy="private", stakes="low"))
    line(f"  private    → {R.log_line(rp)}")
    assert rp.tier == R.LOCAL_SMALL and rp.factor == "privacy", rp

    # 2. low-stakes routine → CHEAP (small) model.
    rc = rt.auto_route(R.TaskDescriptor(kind="classify", stakes="low"))
    line(f"  low-stakes → {R.log_line(rc)}")
    assert rc.tier == R.LOCAL_SMALL and rc.factor == "cost", rc

    # 3. large context → retrieval / big-context lane (token-aware). Back-compat:
    #    context_tokens=0 (the default) would route as before — only a big one shifts.
    big = R.TaskDescriptor(kind="qa", stakes="low", context_tokens=50_000)
    rb = rt.auto_route(big)
    line(f"  big-context ({big.context_tokens} tok) → {R.log_line(rb)}")
    assert rb.tier == R.RETRIEVAL_ASSISTED and rb.factor == "context", rb

    # 4. refusal fallback: a small/cheap engine declines an AUTHORIZED task → the
    #    auto-router climbs to a capable engine and retries, logging the chain.
    refusing = R.default_engines(fn=R.refusing_engine_fn([R.LOCAL_SMALL, R.RETRIEVAL_ASSISTED]))
    rt2 = R.Router(engines=refusing)
    task = R.TaskDescriptor(kind="extract", stakes="low", deterministic_verification=True)
    started = rt2.route(task)                       # would have started cheap
    result, routing = rt2.auto_generate("pull the dates from this", task)
    line(f"  refused    → started {started.tier}; {R.log_line(routing)}")
    assert started.tier == R.LOCAL_SMALL, started
    assert routing.tier == R.FRONTIER and routing.factor == "refusal", routing
    assert routing.fallbacks == (R.LOCAL_SMALL, R.RETRIEVAL_ASSISTED), routing.fallbacks
    assert not R.is_refusal(result) and result.tier == R.FRONTIER, result

    # 5. every choice is logged with its deciding factor (the audit trail).
    factors = [d.factor for d in rt2.decisions]
    line(f"  audit log  → {len(rt2.decisions)} choices, factors {factors}")
    assert "refusal" in factors and rt2.decisions[0].factor == "cost", factors

    # 6. back-compat: route() unchanged for legacy descriptors (no factor needed).
    legacy = rt.route(R.TaskDescriptor(kind="plan", stakes="high"))
    assert legacy.tier == R.FRONTIER, legacy
    line("  → routes on privacy/cost/context, auto-switches on refusal, logs the "
         "deciding factor; route() stays back-compatible. Real engines: deferred.")
