"""Model router — pick a model *tier* for a task, vendor-neutrally.

VISION "Advanced model strategy — compose, not replace": Decima must enhance
whatever model is plugged into it and never depend on one vendor. Selection is
driven by the *task*, not the brand — cost, latency, privacy, context, reasoning
need, modality, and whether a deterministic verifier exists. This module is that
selector. It maps a `TaskDescriptor` to one of four strategy lanes:

  local-small        — cheap/small models for candidate generation, extraction,
                       classification, routing, and anything a deterministic
                       verifier can check (the big cost/capability multiplier).
  retrieval-assisted — a small model + retrieval, for context coverage / grounding.
  frontier           — hard reasoning, synthesis, ambiguous planning, multimodal,
                       and high-stakes work.
  judge              — judge / critic models where deterministic verification is
                       unavailable but the output still needs assessing.

Two properties make this safe to hand the brain:

  • Vendor-neutral. Tiers are CONFIG — `(name → engine)` pairs read from env, not
    hardcoded providers. The *policy* (the rule order below) never branches on a
    vendor; swap the engines and the routing is unchanged. The defaults happen to
    be Claude model ids because this is the Claude reference build; override any of
    them with `DECIMA_TIER_*`.
  • ZERO authority. The Router is a pure function from a descriptor to a tier name.
    It holds no keyring, mints no grant, and never touches `authorize`. Choosing
    "frontier" for an outward effect confers no permission to perform it —
    `capability.authorize` still gates every INVOKE exactly as before. A `Routing`
    is advice; it is not a capability.
"""
import os
from dataclasses import dataclass, field, replace

# ── tier names — the four lanes from VISION "compose, not replace" ───────────
LOCAL_SMALL = "local-small"
RETRIEVAL_ASSISTED = "retrieval-assisted"
FRONTIER = "frontier"
JUDGE = "judge"
TIER_ORDER = (LOCAL_SMALL, RETRIEVAL_ASSISTED, FRONTIER, JUDGE)


@dataclass(frozen=True)
class Tier:
    """A strategy lane bound to a concrete engine. `model` is CONFIG — the only
    vendor-specific thing here, and it is swappable. `retrieval` marks the lane
    that grounds a small model with retrieved context."""
    name: str
    model: str
    retrieval: bool = False


@dataclass(frozen=True)
class TaskDescriptor:
    """What the brain knows about a turn, in vendor-neutral terms. Every field has
    a safe default so a caller can specify only what it knows."""
    kind: str = "chat"            # classify|extract|summarize|route|transform|
                                  # generate|qa|plan|synthesize|code|judge|publish|chat
    stakes: str = "low"           # low | medium | high
    latency: str = "interactive"  # realtime | interactive | batch
    cost: str = "normal"          # frugal | normal | generous
    privacy: str = "public"       # public | sensitive | private  (private ⇒ on-device only)
    modality: str = "text"        # text | code | image | audio | multimodal
    deterministic_verification: bool = False  # is there a checker (tests/types/schema/scanner)?
    needs_context: bool = False   # does it need broad retrieved/grounding context?
    context_tokens: int = 0       # estimated context size (token-aware routing; pairs with SH1)


@dataclass(frozen=True)
class Routing:
    """The router's advice. Note what is ABSENT: no capability, no grant, no
    principal — by construction this object carries no authority."""
    tier: str
    model: str
    reason: str
    retrieval: bool = False
    descriptor: TaskDescriptor = field(default_factory=TaskDescriptor)
    factor: str = ""              # the DECIDING factor category (privacy|cost|context|refusal|…)
    fallbacks: tuple = ()         # tiers that refused before this one (the escalation chain)


# ── config: tier → engine (env-overridable, vendor-neutral) ──────────────────
# Defaults are Claude ids because this is the Claude reference build; the policy
# does not depend on them. Point DECIMA_TIER_* at any provider's models.
def default_tiers() -> dict:
    return {
        LOCAL_SMALL: Tier(
            LOCAL_SMALL,
            os.environ.get("DECIMA_TIER_LOCAL", "claude-haiku-4-5-20251001")),
        RETRIEVAL_ASSISTED: Tier(
            RETRIEVAL_ASSISTED,
            os.environ.get("DECIMA_TIER_RETRIEVAL", "claude-haiku-4-5-20251001"),
            retrieval=True),
        FRONTIER: Tier(
            FRONTIER,
            os.environ.get("DECIMA_TIER_FRONTIER", "claude-opus-4-8")),
        JUDGE: Tier(
            JUDGE,
            os.environ.get("DECIMA_TIER_JUDGE", "claude-opus-4-8")),
    }


# ── the routing policy ───────────────────────────────────────────────────────
# An ordered list of rules; the first to fire wins. Each rule is a function of the
# descriptor returning (tier, reason) or None. Keeping the policy as data (not a
# tangle of ifs) makes it auditable and replaceable per deployment.
_EVAL_KINDS = ("judge", "critique", "evaluate", "score", "grade", "review")
_CHEAP_KINDS = ("classify", "extract", "summarize", "route", "tag",
                "format", "transform")
_HARD_KINDS = ("plan", "synthesize", "reason", "decide", "design", "code")
_RICH_MODALITY = ("image", "audio", "video", "multimodal")


# Above this estimated context size, a turn needs the big-context / retrieval lane
# rather than a small model whose window (or cost) the context would blow.
CONTEXT_TOKEN_THRESHOLD = 8000


def _r_private(d):
    if d.privacy == "private":
        return (LOCAL_SMALL, "private data must not leave the device → keep it local")


def _r_context_size(d):
    if d.context_tokens > CONTEXT_TOKEN_THRESHOLD and d.privacy != "private":
        return (RETRIEVAL_ASSISTED,
                f"large context ({d.context_tokens} tok) → retrieval / big-context lane")


def _r_modality(d):
    if d.modality in _RICH_MODALITY:
        return (FRONTIER, f"{d.modality} modality needs a frontier multimodal model")


def _r_judge(d):
    if d.kind in _EVAL_KINDS:
        return (JUDGE, "evaluative task → critic model assesses what no verifier can")


def _r_verifiable(d):
    if d.deterministic_verification and d.stakes != "high":
        return (LOCAL_SMALL, "a deterministic verifier guards the output → cheap model + check")


def _r_context(d):
    if d.needs_context and d.stakes != "high":
        return (RETRIEVAL_ASSISTED, "needs grounding context → retrieval-assisted lane")


def _r_cheap_kind(d):
    if d.kind in _CHEAP_KINDS and d.stakes == "low":
        return (LOCAL_SMALL, f"low-stakes {d.kind} → small model suffices")


def _r_realtime(d):
    if d.latency == "realtime" and d.stakes == "low":
        return (LOCAL_SMALL, "realtime + low-stakes → favor the fast small model")


def _r_hard(d):
    if d.stakes == "high" or d.kind in _HARD_KINDS:
        why = "high-stakes" if d.stakes == "high" else f"hard reasoning ({d.kind})"
        return (FRONTIER, f"{why} → frontier model")


def _r_context_high(d):
    if d.needs_context:
        return (RETRIEVAL_ASSISTED, "needs grounding context → retrieval-assisted lane")


# Each rule names the DECIDING FACTOR it represents, so a routing can report WHY
# it landed where it did (and a custom rule can set its own `.factor`).
_r_private.factor = "privacy"
_r_modality.factor = "modality"
_r_judge.factor = "capability"
_r_context_size.factor = "context"
_r_verifiable.factor = "cost"
_r_context.factor = "context"
_r_cheap_kind.factor = "cost"
_r_realtime.factor = "latency"
_r_hard.factor = "stakes"
_r_context_high.factor = "context"


DEFAULT_POLICY = (
    _r_private,        # privacy is a hard constraint — overrides everything
    _r_modality,       # only frontier handles rich modality
    _r_judge,          # explicit evaluation → critic
    _r_context_size,   # context too big for a small window → big-context / retrieval lane
    _r_verifiable,     # a checker exists → spend little, verify deterministically
    _r_context,        # grounding needed (non-high-stakes) → retrieval lane
    _r_cheap_kind,     # routine low-stakes work → small model
    _r_realtime,       # speed-sensitive low-stakes → small model
    _r_hard,           # high-stakes / hard reasoning → frontier
    _r_context_high,   # high-stakes but still context-bound → retrieval lane
)


# ── engines: a tier → a vendor-neutral generation seam ───────────────────────
# A Tier names an engine (its `model`); an Engine is the thing you actually invoke
# to GENERATE a candidate. Engines are offline-safe stubs by default so the oracle
# stays reproducible; the real provider call slots into `Engine.fn` without
# touching routing policy (see agent.live_engine_fn). Invoking an engine produces
# text, never an effect — so engines, like the router, confer ZERO authority.
@dataclass(frozen=True)
class EngineResult:
    tier: str
    model: str
    output: str
    stub: bool = True


# An engine signals a REFUSAL by prefixing its output. A real provider refusal
# (stop_reason "refusal", a policy decline) is detected by the agent layer and
# mapped to this same marker; here the stub uses it so the fallback path is testable.
REFUSAL = "[REFUSAL]"


def is_refusal(result) -> bool:
    """True if an engine declined the task (so the auto-router should escalate)."""
    text = result.output if isinstance(result, EngineResult) else str(result)
    return text.lstrip().startswith(REFUSAL)


def _stub_generate(prompt, descriptor, model, tier):
    """Deterministic offline generator. A real engine calls the provider here; this
    stub just tags the prompt with its tier+model so a test can see which engine ran."""
    return f"[{tier}·{model}] {prompt}"


def refusing_engine_fn(refuse_tiers):
    """A stub generation fn that REFUSES on the given tiers — simulating a small/cheap
    model declining an authorized-but-too-hard task — and succeeds on every other
    tier. Used to exercise the auto-router's refusal fallback."""
    refuse = set(refuse_tiers)

    def fn(prompt, descriptor, model, tier):
        if tier in refuse:
            return f"{REFUSAL} {tier}·{model} declines this task"
        return f"[{tier}·{model}] {prompt}"
    return fn


class Engine:
    """A tier's generation seam. `fn(prompt, descriptor, model, tier) -> str`. The
    default fn is an offline stub; pass a provider-calling fn to go live. Vendor
    neutrality lives here: swap the fn/model, the routing policy is unchanged."""

    def __init__(self, tier: str, model: str, fn=None):
        self.tier = tier
        self.model = model
        self.fn = fn or _stub_generate

    @property
    def stub(self) -> bool:
        return self.fn is _stub_generate

    def generate(self, prompt: str, descriptor: "TaskDescriptor | None" = None) -> EngineResult:
        return EngineResult(self.tier, self.model,
                            self.fn(prompt, descriptor, self.model, self.tier), self.stub)


def default_engines(tiers: dict | None = None, fn=None) -> dict:
    """Build a tier→Engine registry from a tiers config. `fn` overrides the
    generation function for ALL engines (e.g. a live provider call)."""
    tiers = tiers or default_tiers()
    return {name: Engine(name, t.model, fn) for name, t in tiers.items()}


class Router:
    """Pure task→tier selector with an attached engine registry. Construct once and
    share; selection has no mutable state and no authority. `route()` is total — it
    always returns a Routing (falling back to the frontier tier when nothing cheaper
    fits). `engine_for()` resolves the tier to the engine you invoke to generate."""

    def __init__(self, tiers: dict | None = None, policy=None, engines: dict | None = None):
        self.tiers = tiers or default_tiers()
        self.policy = policy or DEFAULT_POLICY
        self.engines = engines or default_engines(self.tiers)
        self.decisions: list[Routing] = []   # audit log (auto_route/auto_generate only)

    def engine_for(self, routing: "Routing") -> Engine:
        """The engine bound to a routing's tier. Selection only — invoking it
        produces a candidate (text), never an effect; authority is unchanged."""
        return self.engines[routing.tier]

    def _decide(self, descriptor: TaskDescriptor):
        """(tier, reason, deciding-factor). The first rule to fire wins; the factor
        is the category that rule represents (privacy/cost/context/…)."""
        for rule in self.policy:
            hit = rule(descriptor)
            if hit:
                return hit[0], hit[1], getattr(rule, "factor", "policy")
        return (FRONTIER, "default: nothing cheaper matched → use the frontier", "default")

    def select(self, descriptor: TaskDescriptor):
        """Return (tier_name, reason) without resolving the engine. Pure policy.
        Back-compatible 2-tuple; use `_decide` for the deciding factor too."""
        tier, reason, _ = self._decide(descriptor)
        return tier, reason

    def route(self, descriptor: TaskDescriptor) -> Routing:
        """Resolve a descriptor to a Routing (tier + concrete engine + rationale +
        deciding factor). Pure — no logging, no engine call — so the brain's call
        site is unchanged from C1/C2."""
        tier_name, reason, factor = self._decide(descriptor)
        tier = self.tiers[tier_name]
        return Routing(tier=tier.name, model=tier.model, reason=reason,
                       retrieval=tier.retrieval, descriptor=descriptor, factor=factor)

    # ── the auto-router: logging + refusal fallback ──────────────────────────
    # Escalation order by capability for hard tasks — a refused task climbs it.
    ESCALATION = (LOCAL_SMALL, RETRIEVAL_ASSISTED, FRONTIER)

    def escalate(self, tier_name: str) -> str | None:
        """The next-more-capable tier for a refused task, or None at the top."""
        if tier_name in self.ESCALATION:
            i = self.ESCALATION.index(tier_name)
            return self.ESCALATION[i + 1] if i + 1 < len(self.ESCALATION) else None
        if tier_name == JUDGE:                 # a critic that declines → frontier
            return FRONTIER
        return None

    def auto_route(self, descriptor: TaskDescriptor) -> Routing:
        """Like `route`, but records the choice in the audit log. The deciding
        factor travels on the Routing (`.factor`)."""
        routing = self.route(descriptor)
        self.decisions.append(routing)
        return routing

    def auto_generate(self, prompt: str, descriptor: TaskDescriptor, max_hops: int = 3):
        """Automatic, intelligent model switching. Route the task, run the engine,
        and if it REFUSES an authorized task, escalate to the next-more-capable tier
        and retry — up to `max_hops`. Returns (EngineResult, Routing) where the
        Routing names the engine that answered and the refusal chain it climbed.

        The router still confers ZERO authority: refusal here means a *model*
        declined to GENERATE, not that an effect was permitted. `authorize` is
        untouched."""
        routing = self.auto_route(descriptor)
        tried: list[str] = []
        result = self.engine_for(routing).generate(prompt, descriptor)
        while is_refusal(result) and len(tried) < max_hops:
            tried.append(routing.tier)
            nxt = self.escalate(routing.tier)
            if nxt is None:
                break                           # nothing more capable to try
            tier = self.tiers[nxt]
            routing = Routing(tier=tier.name, model=tier.model,
                              reason=f"{tried[-1]} refused → escalate to {nxt}",
                              retrieval=tier.retrieval, descriptor=descriptor,
                              factor="refusal", fallbacks=tuple(tried))
            self.decisions.append(routing)
            result = self.engine_for(routing).generate(prompt, descriptor)
        return result, routing


def log_line(routing: "Routing") -> str:
    """A one-line audit record of a routing choice + its deciding factor (and the
    refusal chain, if any). What 'choices are logged with the deciding factor' means."""
    chain = (" after " + "→".join(routing.fallbacks) + "↯") if routing.fallbacks else ""
    return f"[{routing.factor or 'policy'}] {routing.tier} ({routing.model}){chain} — {routing.reason}"


def estimate_tokens(text: str) -> int:
    """A crude, deterministic token estimate (~¾ word/token) for token-aware routing.
    A real tokenizer slots in here without touching policy."""
    words = len((text or "").split())
    return (words * 4 + 2) // 3


# ── descriptor inference — a deterministic, vendor-neutral heuristic ─────────
# The brain turns a raw utterance into a TaskDescriptor before routing. This is a
# transparent keyword classifier (no network, fully reproducible). It is a SEAM:
# a deployment can replace it with a learned classifier without touching policy.
_KEYWORDS = {
    "classify":   ("classify", "categorize", "categorise", "label this", "what kind"),
    "extract":    ("extract", "parse", "pull out", "scrape"),
    "summarize":  ("summarize", "summarise", "summary", "tl;dr", "tldr"),
    "judge":      ("judge", "critique", "evaluate", "review", "is this correct",
                   "which is better", "grade"),
    "code":       ("code", "bug", "compile", "unit test", "refactor", "stack trace"),
    "qa":         ("according to", "from the docs", "look up", "cite", "research"),
    "plan":       ("plan", "strategy", "design a", "roadmap", "orchestrate"),
    "transform":  ("upper", "lower", "reverse", "translate", "rot13", "encode"),
    "publish":    ("publish", "send email", "post to", "deploy", "tweet"),
}
_PRIVATE_HINTS = ("confidential", "private", "secret", "ssn", "password", "do not share")
_RICH_HINTS = {"image": ("image", "photo", "picture", "screenshot", "diagram"),
               "audio": ("audio", "transcribe", "voice", "speech recording")}


def describe_task(utterance: str, held_names=None, context_tokens: int = 0) -> TaskDescriptor:
    """Infer a vendor-neutral TaskDescriptor from a raw turn. Deterministic.

    `context_tokens` (optional) lets a caller pass the size of attached context for
    token-aware routing; 0 (the default) preserves the historic behavior exactly."""
    text = (utterance or "").strip().lower()
    held = set(held_names or ())

    kind = "chat"
    for k, needles in _KEYWORDS.items():
        if any(n in text for n in needles):
            kind = k
            break
    if text.startswith("delegate "):          # orchestration is a planning act
        kind = "plan"

    modality = "text"
    for m, needles in _RICH_HINTS.items():
        if any(n in text for n in needles):
            modality = m
            break

    privacy = "private" if any(h in text for h in _PRIVATE_HINTS) else "public"

    # A held `transform` capability comes with a NONA verifier, so its output is
    # deterministically checkable — a strong signal for the cheap lane.
    deterministic = kind in ("transform", "code") or "transform" in held

    # Outward effects and explicit judgement raise the stakes.
    if kind in ("publish",) or any(h in text for h in ("urgent", "production", "irreversible")):
        stakes = "high"
    elif kind in ("plan", "judge", "code"):
        stakes = "medium"
    else:
        stakes = "low"

    needs_context = kind == "qa"

    return TaskDescriptor(kind=kind, stakes=stakes, privacy=privacy,
                          modality=modality,
                          deterministic_verification=deterministic,
                          needs_context=needs_context,
                          context_tokens=int(context_tokens))


def make_router(frontier_model: str | None = None) -> Router:
    """Build the default Router, optionally pinning the frontier engine (so a brain
    configured with an explicit model keeps using it as its frontier tier)."""
    tiers = default_tiers()
    if frontier_model:
        tiers[FRONTIER] = replace(tiers[FRONTIER], model=frontier_model)
    return Router(tiers)
