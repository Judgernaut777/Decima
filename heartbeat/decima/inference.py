"""Self-hosted / private inference — rent a GPU or run open weights, data never leaves.

`CAPABILITY_MAP` D3.3. AR1's Router already sends a private task to the local tier;
INF1 is the engine layer that *proves the data never leaves*. Two engines behind
one `router.Engine`-compatible contract:

  - `LocalInferenceEngine` — on-host inference (the seam where llama.cpp / vLLM on
    your own GPU slots in). It runs through the executor under an SB1 sandbox
    profile with **`network=False`**, so a network attempt by the engine — an
    exfiltration, a call to a hosted API — is **refused by the sandbox before it
    runs** (no egress). ocap says the principal MAY infer; the sandbox says it may
    NOT touch the network while doing so (defense-in-depth under possession).
  - `RemoteInferenceEngine` — a hosted API (network allowed), for non-sensitive work.

Both plug into AR1's `Router` via its public engines seam (`Router(engines=…)`) —
no `router.py` edit. `private_infer(k, prompt, sensitive=True)` routes a sensitive
prompt to the local engine and records the run on the Weft (audit: which engine,
egress yes/no). The engines run inference through the **public** `executor.execute`
— the exact boundary SB1 hardens — so the proof is the real enforcement, not a mock.
"""
from decima import executor, model
from decima.hashing import content_id, nfc
from decima.router import (Engine, EngineResult, Router, TaskDescriptor,
                           LOCAL_SMALL, RETRIEVAL_ASSISTED, FRONTIER, JUDGE)

# Effects the inference engines run through the executor.
LOCAL = "infer.local"
REMOTE = "infer.remote"
EGRESS = "infer.egress"            # a network effect the local engine must NOT be able to use

# SB1 sandbox profiles (a capability's caveats["sandbox"], enforced pre-dispatch).
LOCAL_PROFILE = {"network": False}   # on-host only — the data never leaves
REMOTE_PROFILE = {"network": True}   # hosted API — egress allowed


# -- effect handlers (deterministic stubs; real engines slot in behind these) --
def _local_handler(impl, args):
    return {"out": f"[local·{(impl or {}).get('model', 'on-host')}] {args.get('prompt', '')}"}


def _remote_handler(impl, args):
    return {"out": f"[remote·{(impl or {}).get('model', 'api')}] {args.get('prompt', '')}"}


def _egress_handler(impl, args):
    # Never reached under LOCAL_PROFILE — enforce_sandbox refuses it first. If a
    # remote/unsandboxed caller ran it, it would "send" the payload off-host.
    return {"out": f"exfiltrated: {args.get('payload', '')}"}


for _effect, _handler in {LOCAL: _local_handler, REMOTE: _remote_handler,
                          EGRESS: _egress_handler}.items():
    executor.register(_effect, _handler)


# -- engines (router.Engine-compatible: a .generate(prompt, descriptor) seam) --
class LocalInferenceEngine(Engine):
    """On-host inference under a `network=False` sandbox. The data never leaves; a
    network attempt by the engine is refused by the executor's sandbox (SB1)."""

    def __init__(self, tier: str = LOCAL_SMALL, model: str = "on-host-7b"):
        super().__init__(tier, model)
        self.profile = LOCAL_PROFILE
        self.local = True

    def generate(self, prompt: str, descriptor=None) -> EngineResult:
        res = executor.execute(LOCAL, {"model": self.model}, {"prompt": prompt},
                               sandbox=self.profile)
        return EngineResult(self.tier, self.model, res["out"], stub=True)

    def attempt_egress(self, payload: str = "the user's private prompt"):
        """The engine tries to reach the network (exfiltrate / call a remote API).
        Returns the executor's verdict; under `network=False` it never runs —
        `executor.execute` raises SandboxViolation before any byte leaves."""
        return executor.execute(EGRESS, {"requires": ["network"]},
                                {"payload": payload}, sandbox=self.profile)


class RemoteInferenceEngine(Engine):
    """Hosted-API inference (network allowed) — for non-sensitive work."""

    def __init__(self, tier: str = FRONTIER, model: str = "hosted-api"):
        super().__init__(tier, model)
        self.profile = REMOTE_PROFILE
        self.local = False

    def generate(self, prompt: str, descriptor=None) -> EngineResult:
        res = executor.execute(REMOTE, {"model": self.model, "requires": ["network"]},
                               {"prompt": prompt}, sandbox=self.profile)
        return EngineResult(self.tier, self.model, res["out"], stub=True)


def inference_engines() -> dict:
    """Tier → engine: on-host for the cheap/local + retrieval tiers, hosted for the
    frontier/judge tiers. Pass to AR1's Router via its public seam."""
    return {
        LOCAL_SMALL: LocalInferenceEngine(LOCAL_SMALL),
        RETRIEVAL_ASSISTED: LocalInferenceEngine(RETRIEVAL_ASSISTED),
        FRONTIER: RemoteInferenceEngine(FRONTIER),
        JUDGE: RemoteInferenceEngine(JUDGE),
    }


def inference_router() -> Router:
    """AR1's Router wired to the local/remote inference engines (public seam)."""
    return Router(engines=inference_engines())


def private_infer(k, prompt: str, sensitive: bool = True, router: Router | None = None) -> dict:
    """Route an inference by sensitivity: a sensitive prompt goes to the LOCAL
    engine (no egress); a non-sensitive prompt may use the REMOTE engine. Records
    the run on the Weft (audit: tier, engine, egress). The router confers no
    authority — it only selects the engine; the sandbox is what bounds egress."""
    rt = router or inference_router()
    desc = TaskDescriptor(kind="generate",
                          privacy="private" if sensitive else "public",
                          stakes="low" if sensitive else "medium")
    routing = rt.route(desc)
    engine = rt.engine_for(routing)
    result = engine.generate(prompt, desc)
    local = isinstance(engine, LocalInferenceEngine)
    rid = content_id({"inference": nfc(prompt), "tier": routing.tier, "n": k.weft.lamport})
    model.assert_content(k.weft, k.executor.id, rid, "inference", {
        "tier": routing.tier, "engine": "local" if local else "remote",
        "egress": not local, "sensitive": bool(sensitive),
        "output": result.output, "reason": routing.reason,
    })
    return {"record": rid, "tier": routing.tier,
            "engine": "local" if local else "remote", "egress": not local,
            "output": result.output, "routing": routing}
