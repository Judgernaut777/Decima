"""PATTERN1 — the nine agentic architecture patterns, as a first-class, recorded,
SELECTABLE decision.

There is no single "best" multi-agent architecture; the right shape is a function
of the *task*. The agentic-design guide organizes the choice along two axes —
**predictability** (are the steps predefined, or do they emerge at runtime?) and
**context-sharing** (does each sub-agent work in an isolated scratchpad, or on
shared state?) — plus parallelism, quality-criticality, and regulatory constraint.
This module names the nine canonical patterns, attaches that metadata to each, and
provides a DETERMINISTIC selector that maps a task's features to exactly one — and
records the choice (and its reason) as a Cell on the Weft.

Decima already USES several of these, implicitly:
  • the sub-agent fleets (a central brain decomposes work, fans it to workers,
    synthesizes the results) == **orchestrator-worker**;
  • AR1, the model router (`router.py`, a classifier→dispatcher to specialized
    tiers) == **router**;
  • the brain's own observe→decide→act turn == a **single-agent-loop**.
PATTERN1 makes that latent choice EXPLICIT, deterministic, overridable, and
auditable — a recorded decision on the Weft rather than an architectural accident.

Laws this module upholds (mirroring `router.py`'s selector discipline):
  - **DETERMINISTIC selection.** `select` is a pure function of task features — no
    model call, no randomness. Re-running it on the same task yields the same
    pattern and the same reason. (The router's policy-as-data style: an ordered
    list of rules, first match wins.)
  - **Manual override is honored AND recorded.** A user can override the selector's
    choice; the override is obeyed and written to the Weft with *who* chose it and
    *why* — provenance, not a silent swap.
  - **Ints, not floats.** Any numeric content (a pattern's relative `cost`, the
    axis enum codes) is an int. No float reaches the signed log (WEFT §4/§7).
  - **The choice + its reason live on the Weft.** Selecting (or overriding) writes a
    `pattern_choice` Cell carrying the chosen pattern, the deciding reason, and the
    task features that drove it — so the decision is auditable and time-travelable.
  - **No authority.** Like the router, a pattern choice is ADVICE: it names an
    architecture, it grants nothing. `capability.authorize` still gates every effect.

Public `model`/`weave`/`hashing` API only — no core edit.
"""
from dataclasses import dataclass, field

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

# ── the two axes from the guide (enums; int-coded for the signed log) ─────────
# Predictability: are the steps known up front, or do they emerge at runtime?
PREDEFINED = "predefined"
EMERGENT = "emergent"
# Context-sharing: does each sub-agent see only its own scratchpad, or shared state?
ISOLATED = "isolated"
SHARED = "shared"

_PREDICTABILITY_CODE = {PREDEFINED: 0, EMERGENT: 1}
_CONTEXT_CODE = {ISOLATED: 0, SHARED: 1}

# Relative cost rank (an int, not a float) — higher = more latency/token/coordination
# spend. Used for ordering/reporting, never as a probability.
LOW, MED, HIGH = 1, 2, 3

# ── the nine pattern names ────────────────────────────────────────────────────
SINGLE_AGENT = "single-agent-loop"
ORCHESTRATOR_WORKER = "orchestrator-worker"
SUPERVISOR = "supervisor"
HIERARCHICAL = "hierarchical-task-decomposition"
SWARM = "swarm"
NETWORK_MESH = "network-mesh"
PIPELINE = "pipeline"
EVALUATOR_OPTIMIZER = "evaluator-optimizer"
ROUTER = "router"

# Declaration order = the canonical catalog order (also the registry's key order).
PATTERN_ORDER = (
    SINGLE_AGENT, ORCHESTRATOR_WORKER, SUPERVISOR, HIERARCHICAL, SWARM,
    NETWORK_MESH, PIPELINE, EVALUATOR_OPTIMIZER, ROUTER,
)


@dataclass(frozen=True)
class Pattern:
    """A descriptor for one agentic architecture pattern. The two axes
    (`predictability`, `context_sharing`) place it on the guide's grid; the rest is
    advisory metadata for selection and for an operator reading WHY a shape fits.
    `cost` is an int rank (LOW/MED/HIGH), never a float."""
    name: str
    predictability: str            # predefined | emergent
    context_sharing: str           # isolated | shared
    parallel: bool                 # can sub-agents run concurrently?
    cost: int                      # relative latency/coordination cost rank (int)
    when_use: str
    when_avoid: str

    def axes(self) -> tuple:
        """(predictability_code, context_code) — the int-coded grid position."""
        return (_PREDICTABILITY_CODE[self.predictability],
                _CONTEXT_CODE[self.context_sharing])


# ── the registry: all nine patterns, with the guide's metadata ────────────────
def default_patterns() -> dict:
    """The nine canonical agentic architecture patterns, keyed by name in catalog
    order. Pure data — a deployment can extend this without touching the selector."""
    return {p.name: p for p in (
        Pattern(
            SINGLE_AGENT, PREDEFINED, ISOLATED, parallel=False, cost=LOW,
            when_use="simple, bounded tasks — start here; one agent, one loop, no "
                     "coordination overhead (Decima's brain turn is this)",
            when_avoid="the work has many independent sub-tasks, or needs parallelism "
                       "or specialized sub-agents",
        ),
        Pattern(
            ORCHESTRATOR_WORKER, EMERGENT, ISOLATED, parallel=True, cost=MED,
            when_use="sub-tasks are not known up front and EMERGE at runtime — a "
                     "central orchestrator decomposes, fans work to workers, and "
                     "synthesizes (Decima's sub-agent fleets are this)",
            when_avoid="the steps are fixed and predefined — a pipeline is simpler and "
                       "more predictable",
        ),
        Pattern(
            SUPERVISOR, PREDEFINED, ISOLATED, parallel=True, cost=MED,
            when_use="centralized delegation with CONTROLLED context — a supervisor "
                     "calls sub-agents as tools, each with an isolated scratchpad, so "
                     "context stays clean and bounded",
            when_avoid="agents must freely share intermediate state, or you want "
                       "peer-to-peer hand-off without a central caller",
        ),
        Pattern(
            HIERARCHICAL, PREDEFINED, SHARED, parallel=True, cost=HIGH,
            when_use="complex / ambiguous / COMPLIANCE-sensitive work — a tree of "
                     "managers delegates and adds review/approval GATES at each level "
                     "(accepts higher latency/cost for control)",
            when_avoid="simple or latency-sensitive tasks — the review tree's cost and "
                       "latency aren't justified",
        ),
        Pattern(
            SWARM, EMERGENT, SHARED, parallel=True, cost=HIGH,
            when_use="open-ended, exploratory problems where decentralized peers hand "
                     "off to whichever is most fit, with no central controller",
            when_avoid="DETERMINISTIC, resource-constrained, simple-sequential, or "
                       "REGULATORY tasks — decentralized hand-off defeats predictability "
                       "and auditability",
        ),
        Pattern(
            NETWORK_MESH, EMERGENT, SHARED, parallel=True, cost=HIGH,
            when_use="peer-to-peer specialist collaboration — agents share information "
                     "across a mesh, trading central observability for flexible "
                     "distributed hand-off",
            when_avoid="you need a single point of observability/audit, or the task is "
                       "regulated — distributed hand-off scatters the trail",
        ),
        Pattern(
            PIPELINE, PREDEFINED, SHARED, parallel=False, cost=LOW,
            when_use="a fixed sequence of PREDEFINED stages, each consuming the last — "
                     "rigid but maximally predictable and easy to audit",
            when_avoid="the sub-tasks are dynamic / emergent, or stages need to run out "
                       "of order or in parallel",
        ),
        Pattern(
            EVALUATOR_OPTIMIZER, PREDEFINED, SHARED, parallel=False, cost=HIGH,
            when_use="QUALITY-critical output — a generator and an evaluator iterate "
                     "(writer↔editor) until the result clears a bar; spend justified "
                     "when review quality matters",
            when_avoid="low-stakes or throwaway output — the iterate-and-critique loop "
                       "is wasted cost",
        ),
        Pattern(
            ROUTER, PREDEFINED, ISOLATED, parallel=True, cost=LOW,
            when_use="a classifier dispatches each request to one of several "
                     "specialized handlers — DOMAIN parallelism with clean isolation "
                     "(Decima's AR1 model router is this)",
            when_avoid="a single agent already handles the whole domain, or the "
                       "sub-tasks must collaborate rather than be dispatched",
        ),
    )}


# ── the deciding factors a task can carry ─────────────────────────────────────
@dataclass(frozen=True)
class Task:
    """What's known about a task, in the selector's vocabulary — every field has a
    safe default so a caller specifies only what it knows. Deterministic input: the
    same Task always selects the same Pattern."""
    name: str = "task"
    predictability: str = PREDEFINED   # predefined | emergent
    context_sharing: str = ISOLATED    # isolated | shared
    parallel: bool = False             # are there independent sub-tasks to run at once?
    fixed_stages: bool = False         # a known, ordered sequence of stages?
    quality_critical: bool = False     # does output quality justify a review loop?
    domain_dispatch: bool = False      # route to one of several specialized handlers?
    regulatory: bool = False           # compliance/audit-bound? (NEVER swarm/mesh)
    complex: bool = False              # complex/ambiguous enough to need a review tree?
    emergent_subtasks: bool = False    # sub-tasks discovered at runtime?

    def features(self) -> dict:
        """The task's deciding features as plain data (int-coded axes; bools as ints)
        — the exact record stored on the Weft so a choice is reproducible/auditable."""
        return {
            "name": self.name,
            "predictability": self.predictability,
            "context_sharing": self.context_sharing,
            "predictability_code": int(_PREDICTABILITY_CODE[self.predictability]),
            "context_code": int(_CONTEXT_CODE[self.context_sharing]),
            "parallel": bool(self.parallel),
            "fixed_stages": bool(self.fixed_stages),
            "quality_critical": bool(self.quality_critical),
            "domain_dispatch": bool(self.domain_dispatch),
            "regulatory": bool(self.regulatory),
            "complex": bool(self.complex),
            "emergent_subtasks": bool(self.emergent_subtasks),
        }


# ── the selection policy ──────────────────────────────────────────────────────
# An ordered list of rules; the FIRST to fire wins (router.py's policy-as-data
# style). Each rule is a pure function of the Task returning (pattern, reason) or
# None. Order encodes priority: hard constraints and the strongest specific signals
# first, the two-axis grid as the general fallback last.
def _r_quality(t):
    if t.quality_critical:
        return (EVALUATOR_OPTIMIZER,
                "quality-critical output → generator+evaluator iterate until it clears the bar")


def _r_fixed_stages(t):
    if t.fixed_stages:
        return (PIPELINE, "a fixed, ordered sequence of stages → pipeline (predictable, auditable)")


def _r_domain_dispatch(t):
    if t.domain_dispatch:
        return (ROUTER, "dispatch each request to a specialized handler → router (domain parallelism)")


def _r_regulatory(t):
    # Regulatory/compliance work demands central observability + approval gates and
    # must NEVER use a decentralized hand-off (swarm/mesh). A hierarchical tree with
    # review gates is the auditable shape.
    if t.regulatory:
        return (HIERARCHICAL,
                "regulatory/compliance task → hierarchical tree with review/approval "
                "gates (NEVER a decentralized swarm/mesh — auditability requires central control)")


def _r_complex(t):
    if t.complex:
        return (HIERARCHICAL,
                "complex/ambiguous work → hierarchical decomposition with manager review gates")


def _r_emergent(t):
    # Sub-tasks discovered at runtime → a central orchestrator decomposes + synthesizes.
    if t.emergent_subtasks or t.predictability == EMERGENT:
        return (ORCHESTRATOR_WORKER,
                "unpredictable: sub-tasks emerge at runtime → orchestrator decomposes, "
                "fans to workers, and synthesizes")


def _r_grid(t):
    # The two-axis fallback (the guide's grid): predictability × context-sharing.
    #   predefined + isolated → supervisor (controlled delegation)
    #   predefined + shared   → pipeline (fixed stages over shared state)
    #   emergent  + isolated  → orchestrator-worker (covered above; here for completeness)
    #   emergent  + shared    → network-mesh (peer collaboration over shared state)
    grid = {
        (PREDEFINED, ISOLATED): (SUPERVISOR,
            "predefined steps + isolated scratchpads → supervisor (controlled central delegation)"),
        (PREDEFINED, SHARED): (PIPELINE,
            "predefined steps over shared state → pipeline"),
        (EMERGENT, ISOLATED): (ORCHESTRATOR_WORKER,
            "emergent sub-tasks + isolated workers → orchestrator-worker"),
        (EMERGENT, SHARED): (NETWORK_MESH,
            "emergent + shared state → network-mesh (peer-to-peer specialist collaboration)"),
    }
    return grid[(t.predictability, t.context_sharing)]


def _r_simple(t):
    # The default floor: a simple, bounded, single-context task → start with one loop.
    if not t.parallel:
        return (SINGLE_AGENT, "simple, bounded, single-context task → single-agent loop (start here)")


DEFAULT_POLICY = (
    _r_quality,          # quality is a strong, specific signal → evaluator-optimizer
    _r_fixed_stages,     # a known stage sequence → pipeline
    _r_domain_dispatch,  # classify-and-dispatch → router
    _r_regulatory,       # regulated → gated hierarchy, NEVER a swarm/mesh
    _r_complex,          # complex/ambiguous → gated hierarchy
    _r_emergent,         # runtime-emergent sub-tasks → orchestrator-worker
    _r_simple,           # bounded single-context → single-agent loop
    _r_grid,             # the two-axis grid — total fallback (always fires)
)


# ── the Selector ──────────────────────────────────────────────────────────────
PATTERN_CHOICE = "pattern_choice"   # the Cell type recording a choice on the Weft
CHOSE = "chose_pattern"             # edge: choice → chose_pattern → (pattern name as a tag)


@dataclass(frozen=True)
class Choice:
    """The selector's decision. Like router.Routing it carries NO authority — it
    names an architecture, it grants nothing."""
    pattern: str
    reason: str
    features: dict
    manual: bool = False               # was this a user override of the selector?
    overridden_from: str = ""          # the pattern the selector would have picked
    who: str = ""                      # principal who overrode (manual only)
    why: str = ""                      # the override's stated reason (manual only)


class Selector:
    """Pure Task→Pattern selector with the nine-pattern registry attached. Construct
    once and share; selection has no mutable state and no authority. Mirrors
    `router.Router`: `select()` is a pure function; `select_k()`/`override()` also
    RECORD the choice as a Cell on the Weft."""

    def __init__(self, patterns: dict | None = None, policy=None):
        self.patterns = patterns or default_patterns()
        self.policy = policy or DEFAULT_POLICY

    def get(self, name: str) -> Pattern:
        return self.patterns[name]

    def _decide(self, task: Task):
        """(pattern_name, reason). The first rule to fire wins; `_r_grid` is total so
        a decision always results. Pure — no log, no model call → deterministic."""
        for rule in self.policy:
            hit = rule(task)
            if hit:
                return hit[0], hit[1]
        return _r_grid(task)            # belt-and-suspenders: the grid is total

    def select(self, task: Task) -> Choice:
        """The deterministic choice for a task — pure policy, no Weft write. Re-running
        on the same Task yields an identical Choice (same pattern, same reason)."""
        name, reason = self._decide(task)
        if name not in self.patterns:
            raise ValueError(f"selector picked an unregistered pattern: {name!r}")
        return Choice(pattern=name, reason=reason, features=task.features())

    # ── recording on the Weft ────────────────────────────────────────────────
    def _record(self, k, task: Task, choice: Choice, author: str | None = None) -> str:
        """Write a `pattern_choice` Cell carrying the chosen pattern, the reason, and
        the deciding features — plus a `chose_pattern` edge to the pattern's catalog
        Cell. Returns the choice Cell id. The choice is now auditable + time-travelable."""
        author = author or k.decima_agent_id
        cid = content_id({"pattern_choice": task.name, "pattern": choice.pattern,
                          "at": k.weft.head})
        assert_content(k.weft, author, cid, PATTERN_CHOICE, {
            "task": task.name,
            "pattern": choice.pattern,
            "reason": choice.reason,
            "features": choice.features,
            "manual": bool(choice.manual),
            "overridden_from": choice.overridden_from,
            "who": choice.who,
            "why": choice.why,
        })
        # A stable per-pattern tag cell id so the edge points at a catalog anchor.
        assert_edge(k.weft, author, cid, CHOSE, _pattern_tag(choice.pattern))
        return cid

    def select_k(self, k, task: Task, author: str | None = None):
        """Select AND record. Returns (choice, cell_id). The recorded choice is the
        deterministic selection — recompute `select(task)` and it matches."""
        choice = self.select(task)
        cid = self._record(k, task, choice, author)
        return choice, cid

    def override(self, k, task: Task, pattern: str, *, who: str, why: str,
                 author: str | None = None):
        """A MANUAL user override: honor `pattern` regardless of what the selector
        would pick, and RECORD it with who/why and what it overrode. Returns
        (choice, cell_id). Fails loud on an unknown pattern (you can't override to a
        pattern that isn't in the catalog)."""
        if pattern not in self.patterns:
            raise ValueError(f"cannot override to unknown pattern: {pattern!r}")
        if not who or not str(who).strip():
            raise ValueError("a manual override must name WHO chose it")
        would = self.select(task)          # what the deterministic selector wanted
        choice = Choice(
            pattern=pattern,
            reason=f"manual override by {who}: {why}",
            features=task.features(),
            manual=True,
            overridden_from=would.pattern,
            who=nfc(str(who)),
            why=nfc(str(why)),
        )
        cid = self._record(k, task, choice, author)
        return choice, cid


def _pattern_tag(name: str) -> str:
    """A stable content-addressed anchor for a pattern name, so `chose_pattern` edges
    from many choices all converge on one catalog node per pattern."""
    return content_id({"pattern_tag": nfc(name)})


def choices_on(k, task_name: str | None = None) -> list:
    """Fold the recorded `pattern_choice` Cells (optionally for one task), newest by
    appearance last. A pure read over the Weave — the audit trail of every choice."""
    out = [c for c in k.weave().of_type(PATTERN_CHOICE)
           if not c.retracted and (task_name is None or c.content.get("task") == task_name)]
    return out


def make_selector() -> Selector:
    """Build the default nine-pattern Selector."""
    return Selector()
