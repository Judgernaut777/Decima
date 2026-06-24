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


@dataclass(frozen=True)
class Routing:
    """The router's advice. Note what is ABSENT: no capability, no grant, no
    principal — by construction this object carries no authority."""
    tier: str
    model: str
    reason: str
    retrieval: bool = False
    descriptor: TaskDescriptor = field(default_factory=TaskDescriptor)


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


def _r_private(d):
    if d.privacy == "private":
        return (LOCAL_SMALL, "private data must not leave the device → keep it local")


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


DEFAULT_POLICY = (
    _r_private,        # privacy is a hard constraint — overrides everything
    _r_modality,       # only frontier handles rich modality
    _r_judge,          # explicit evaluation → critic
    _r_verifiable,     # a checker exists → spend little, verify deterministically
    _r_context,        # grounding needed (non-high-stakes) → retrieval lane
    _r_cheap_kind,     # routine low-stakes work → small model
    _r_realtime,       # speed-sensitive low-stakes → small model
    _r_hard,           # high-stakes / hard reasoning → frontier
    _r_context_high,   # high-stakes but still context-bound → retrieval lane
)


class Router:
    """Pure task→tier selector. Construct once and share; it has no mutable state
    and no authority. `route()` is total — it always returns a Routing (falling
    back to the frontier tier when nothing cheaper fits)."""

    def __init__(self, tiers: dict | None = None, policy=None):
        self.tiers = tiers or default_tiers()
        self.policy = policy or DEFAULT_POLICY

    def select(self, descriptor: TaskDescriptor):
        """Return (tier_name, reason) without resolving the engine. Pure policy."""
        for rule in self.policy:
            hit = rule(descriptor)
            if hit:
                return hit
        return (FRONTIER, "default: nothing cheaper matched → use the frontier")

    def route(self, descriptor: TaskDescriptor) -> Routing:
        """Resolve a descriptor to a Routing (tier + concrete engine + rationale)."""
        tier_name, reason = self.select(descriptor)
        tier = self.tiers[tier_name]
        return Routing(tier=tier.name, model=tier.model, reason=reason,
                       retrieval=tier.retrieval, descriptor=descriptor)


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


def describe_task(utterance: str, held_names=None) -> TaskDescriptor:
    """Infer a vendor-neutral TaskDescriptor from a raw turn. Deterministic."""
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
                          needs_context=needs_context)


def make_router(frontier_model: str | None = None) -> Router:
    """Build the default Router, optionally pinning the frontier engine (so a brain
    configured with an explicit model keeps using it as its frontier tier)."""
    tiers = default_tiers()
    if frontier_model:
        tiers[FRONTIER] = replace(tiers[FRONTIER], model=frontier_model)
    return Router(tiers)
