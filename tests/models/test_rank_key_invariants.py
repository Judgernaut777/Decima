"""Unit invariants for ``RoutingPolicy._rank_key`` (decima/models/routing.py).

The deterministic ranking key is the tuple::

    (local_rank, cap_shortfall, locality_penalty, latency_penalty, cost, model)

and its load-bearing property — documented today only in the ``_rank_key`` /
``_locality_penalty`` / ``_latency_penalty`` docstrings — is that EVERY soft term is
``0`` for EVERY entry when its preference is UNSET, so with no requirements the tuple's
discriminating components collapse to ``(local_rank, cost, model)`` and the order is
byte-identical to capability-unaware routing. These tests pin that key SHAPE directly
(not merely through ``select``): each soft term is ``0`` when unset, and takes its
documented nonzero value when its preference is expressed, so a future refactor that
quietly makes an unset term nonzero (and silently reorders every route) fails here.

``_rank_key`` is a PURE key builder over a single entry; it does not filter, so it may
be evaluated on a remote entry even in a sensitive/realtime lane — the privacy filter
lives in ``_eligible`` and is covered elsewhere.
"""

from __future__ import annotations

from decima.models.providers import EXTERNAL_PAID, LOCAL_ONLY
from decima.models.registry import ModelEntry
from decima.models.routing import RoutingPolicy, TaskSpec

# key-tuple positions, named for readability.
_LOCAL_RANK = 0
_CAP_SHORTFALL = 1
_LOCALITY_PENALTY = 2
_LATENCY_PENALTY = 3


def _entry(model, *, local, cost=0, latency_class="interactive", **caps):
    return ModelEntry(
        provider=("local" if local else "cloud"),
        model=model,
        local=local,
        context_limit=(8192 if local else 200_000),
        modalities=("text", "code"),
        structured_output=True,
        tool_use=not local,
        est_cost_per_1k_microcents=cost,
        privacy_class=(LOCAL_ONLY if local else EXTERNAL_PAID),
        latency_class=latency_class,
        **caps,
    )


def test_rank_key_soft_terms_all_zero_with_no_preferences():
    """With a bare spec (no capability, locality, or latency preference) all three soft
    terms are 0 for every entry and local_rank is 1 for every entry, so the tuple
    collapses to (local_rank, cost, model) — the capability-unaware order."""
    entries = [
        _entry("weak-local", local=True, cost=0, coding=1, reasoning_strength=1),
        _entry("strong-local", local=True, cost=0, coding=5, reasoning_strength=3),
        _entry("remote-batch", local=False, cost=3000, coding=5, latency_class="batch"),
    ]
    key = RoutingPolicy()._rank_key(TaskSpec(), 512)
    for e in entries:
        k = key(e)
        assert k[_CAP_SHORTFALL] == 0
        assert k[_LOCALITY_PENALTY] == 0
        assert k[_LATENCY_PENALTY] == 0
        # not sensitive and not realtime ⇒ no local-preference lane for anyone.
        assert k[_LOCAL_RANK] == 1
        # the two discriminating survivors are exactly cost and model id.
        assert k[4:] == (k[4], e.model)


def test_rank_key_cap_shortfall_zero_unset_nonzero_when_preferred():
    """The capability-shortfall term is 0 for every entry with no preferred capability,
    and the documented summed shortfall below the preferred level once one is set."""
    weak = _entry("weak", local=True, coding=1, reasoning_strength=1)
    strong = _entry("strong", local=True, coding=5, reasoning_strength=3)

    unset = RoutingPolicy()._rank_key(TaskSpec(), 512)
    assert unset(weak)[_CAP_SHORTFALL] == 0
    assert unset(strong)[_CAP_SHORTFALL] == 0

    preferred = RoutingPolicy()._rank_key(TaskSpec(preferred_capabilities=(("coding", 5),)), 512)
    # coding preferred at 5: weak scores 1 ⇒ shortfall 4; strong meets it ⇒ 0.
    assert preferred(weak)[_CAP_SHORTFALL] == 4
    assert preferred(strong)[_CAP_SHORTFALL] == 0


def test_rank_key_locality_penalty_zero_unset_penalizes_remote_when_preferred():
    """The soft locality penalty is 0 for every entry (local AND remote) with no
    prefer_local, and a 0/1 penalty (0 local, 1 remote) once prefer_local is set — the
    documented soft bias that never filters."""
    local = _entry("local", local=True)
    remote = _entry("remote", local=False)

    unset = RoutingPolicy()._rank_key(TaskSpec(prefer_local=False), 512)
    assert unset(local)[_LOCALITY_PENALTY] == 0
    assert unset(remote)[_LOCALITY_PENALTY] == 0

    preferred = RoutingPolicy()._rank_key(TaskSpec(prefer_local=True), 512)
    assert preferred(local)[_LOCALITY_PENALTY] == 0
    assert preferred(remote)[_LOCALITY_PENALTY] == 1


def test_rank_key_latency_penalty_zero_unset_is_class_distance_when_preferred():
    """The soft latency penalty is 0 for every entry (regardless of class) with no
    prefer_latency, and the documented integer class distance (0 if equal-or-faster)
    once prefer_latency is set."""
    realtime = _entry("snappy", local=True, latency_class="realtime")
    interactive = _entry("steady", local=True, latency_class="interactive")
    batch = _entry("batchy", local=True, latency_class="batch")

    unset = RoutingPolicy()._rank_key(TaskSpec(prefer_latency=None), 512)
    for e in (realtime, interactive, batch):
        assert unset(e)[_LATENCY_PENALTY] == 0

    # "at least interactive": realtime/interactive are equal-or-faster ⇒ 0; batch is one
    # class slower ⇒ 1 (pure LATENCY_RANK integer distance).
    preferred = RoutingPolicy()._rank_key(TaskSpec(prefer_latency="interactive"), 512)
    assert preferred(realtime)[_LATENCY_PENALTY] == 0
    assert preferred(interactive)[_LATENCY_PENALTY] == 0
    assert preferred(batch)[_LATENCY_PENALTY] == 1


def test_rank_key_local_rank_lane_is_off_without_sensitivity_or_realtime():
    """local_rank is the FIRST tuple term and only drops to 0 (favouring local) inside
    the sensitive/realtime lane. For a public, non-realtime task it is 1 for every
    entry, so it cannot reorder a plain route."""
    local = _entry("local", local=True)
    remote = _entry("remote", local=False)
    key = RoutingPolicy()._rank_key(TaskSpec(sensitivity="public", latency="interactive"), 512)
    assert key(local)[_LOCAL_RANK] == 1
    assert key(remote)[_LOCAL_RANK] == 1


def test_rank_key_local_rank_zero_only_for_local_in_prefer_lane():
    """Inside the prefer-local lane (sensitive OR realtime forces it), local_rank is 0
    for a local entry and 1 for a remote one — the lane favours local, and it is keyed
    off the FORCED lane, not the soft prefer_local flag."""
    local = _entry("local", local=True)
    remote = _entry("remote", local=False)

    sensitive = RoutingPolicy()._rank_key(TaskSpec(sensitivity="sensitive"), 512)
    assert sensitive(local)[_LOCAL_RANK] == 0
    assert sensitive(remote)[_LOCAL_RANK] == 1

    realtime = RoutingPolicy()._rank_key(TaskSpec(latency="realtime"), 512)
    assert realtime(local)[_LOCAL_RANK] == 0
    assert realtime(remote)[_LOCAL_RANK] == 1
