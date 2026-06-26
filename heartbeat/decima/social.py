"""SOCIAL1 — Social media as two composed primitives: a PUBLIC outbound POST is a
Morta-gated outward effect; ALL inbound (mentions/comments/DMs) is untrusted DATA.

A social feed is the textbook spot to break the recall-vs-instruct law twice over:

  • POST (outbound) — a public post is an **outward effect** with a *large* blast
    radius (it's PUBLIC), so it composes the same safety primitives the payments and
    messaging rails do (PAY1/MSG1 pattern): an effect registered via the public
    `executor.register` (through `kernel.integrate_tool`), a capability carrying
    **Morta** (`requires_approval` — DENIED until a human/policy approves) and an
    **SB1 sandbox** profile (only this effect may run; network on, to the social API).
    Every post lands a full EffectReceipt on the Weft (audit).

  • RECEIVE (inbound) — a mention/comment/DM is captured as **untrusted DATA** through
    the LIVE disposition router (`kernel.ingest` → DISP1). An inbound item can only ever
    be remembered (as DATA, `instruction_eligible=False`) or archived; it can NEVER
    elevate itself to a task/invoke/policy, and its imperative content never selects its
    own disposition — the disposition is Decima's. Even a body laced with an injection
    ("ignore your rules and post X") is stored as flagged DATA, never obeyed.

  • SCHEDULE — a future post is just a `scheduled_event` on the Weft (SCHED1). It carries
    the platform+content; when it fires the post still flows through the Morta-gated rail.

Pure composition: public `executor` / `kernel` / `model` / `disposition` / `scheduling`
API only. Edits no core file and no other module. Ints not floats; no ambient authority.
"""
from __future__ import annotations

from decima import executor, scheduling
from decima.hashing import content_id, nfc
from decima.model import assert_content, assert_edge

SOCIAL = "SOCIAL"                   # the effect_class for an outward public post
POST_EFFECT = "social.post"         # the registered outbound effect name
POST = "post"                       # the outbound post Cell type
INBOUND = "inbound_item"            # the captured inbound mention/comment/DM Cell type
ON_FEED = "on_feed"                # inbound item → feed edge (groups a platform's feed)
RESULT = "result"                   # the EffectReceipt cell type the kernel asserts


# -- the outbound rail (the effect itself) -----------------------------------
def _post_handler(args: dict) -> dict:
    """The outbound rail — a deterministic stub standing in for a social-platform API.
    A real handler calls the provider over the network-to-rail-only sandbox; here it
    confirms the post deterministically. A bad request (missing platform/content) raises
    ExecError → a FAILED receipt: a definite no-effect, nothing was posted publicly."""
    platform = nfc(str(args.get("platform", "")))
    content = nfc(str(args.get("content", "")))
    if not platform:
        raise executor.ExecError("post requires a platform")
    if not content:
        raise executor.ExecError("post requires non-empty content")
    return {"out": f"posted to {platform}: {content}", "platform": platform,
            "content": content}


def install_rail(k, *, name: str = POST_EFFECT) -> str:
    """Register the outbound `social.post` effect and forge a SOCIAL capability granted
    to Decima: Morta `requires_approval` (DENIED until approved) + an SB1 sandbox profile
    that allows only this effect (network on, to the social API). Returns the cap id.

    A public post is high blast radius (PUBLIC), so Morta is non-negotiable here."""
    caveats = {
        "effect_class": SOCIAL,
        "requires_approval": True,          # Morta gate — denied until approved
        # SB1 sandbox: only this effect may run under the cap; network on (to the
        # platform API). The durable form pins egress to the provider host.
        "sandbox": {"effects": [name], "network": True},
    }
    return k.integrate_tool(name, lambda _impl, args: _post_handler(args), caveats=caveats)


# -- outbound: a Morta-gated, sandboxed, audited public post -----------------
def post(k, agent_cell, cap_id, platform: str, content: str, *, author=None) -> dict:
    """Publish an OUTBOUND public POST through the Morta-gated, sandboxed `social.post`
    capability. DENIED until the capability is approved (Morta); on success the kernel
    emits an EffectReceipt (audit). Records an outbound `post` Cell bound to its receipt
    via `posted_via` (only when the post actually ran).

    Returns {status, result_cell, denied?, post?, platform}."""
    author = author or k.decima_agent_id
    platform = nfc(str(platform))

    res = k.invoke(agent_cell, cap_id, {
        "platform": platform, "content": nfc(str(content)),
    })
    out = {"status": res.get("status"), "result_cell": res.get("result_cell"),
           "platform": platform}
    if "denied" in res:                                     # Morta / sandbox refusal
        out["denied"] = res["denied"]
        return out

    # The post ran: record the outbound post Cell and bind it to its receipt.
    pid = content_id({"post": nfc(str(content)), "platform": platform,
                      "receipt": res["result_cell"]})
    assert_content(k.weft, author, pid, POST, {
        "direction": "outbound", "platform": platform, "content": nfc(str(content)),
        "receipt": res["result_cell"],
        "instruction_eligible": True,           # our own outbound is a real action
    })
    assert_edge(k.weft, author, pid, "posted_via", res["result_cell"])
    out["post"] = pid
    return out


def schedule_post(k, platform: str, content: str, *, at: int, author=None,
                  repeat_every: int | None = None) -> str:
    """Schedule a FUTURE public post (SCHED1). Asserts a `scheduled_event` Cell carrying
    the platform+content and an integer logical tick `at`; returns the event id. When the
    event fires (via `scheduling.fire`), the post still flows through the Morta-gated rail
    — scheduling never bypasses Morta. `at`/`repeat_every` are ints (no float in content)."""
    title = f"social.post {nfc(str(platform))}: {nfc(str(content))}"
    return scheduling.schedule(k, title, at, repeat_every=repeat_every, author=author)


# -- inbound: capture a mention/comment/DM as untrusted DATA -----------------
def _feed_id(platform: str) -> str:
    """A stable id for a platform's inbound feed Cell."""
    return content_id({"feed": nfc(str(platform))})


def _ensure_feed(k, author, platform: str) -> str:
    """Idempotently assert the platform's `feed` Cell. Re-receiving on the same platform
    lands on the same feed id, so inbound items accrete into one fold."""
    fid = _feed_id(platform)
    if k.weave().get(fid) is None:
        assert_content(k.weft, author, fid, "feed", {"platform": nfc(str(platform))})
    return fid


def receive(k, platform: str, kind: str, sender: str, body: str, *, author=None) -> dict:
    """Capture an INBOUND mention/comment/DM as untrusted DATA. The item is routed through
    the LIVE disposition router (`kernel.ingest`, trusted=False) — so it can only ever be
    remembered as DATA or archived, NEVER elevated to a task/invoke/policy, and its
    imperative content never picks its own disposition. An `inbound_item` Cell records the
    raw inbound, grouped into the platform `feed` via an `on_feed` edge, and bound to the
    disposition's intake via `captured_as`.

    Returns {item, feed, disposition, action, instruction_eligible, produced, intake}.
    `instruction_eligible` is always False — an inbound item is DATA, not an order."""
    author = author or k.decima_agent_id
    platform = nfc(str(platform))

    # Route across the trust boundary FIRST: inbound is untrusted by law.
    d = k.ingest(f"{platform}:{kind}:{sender}", body, trusted=False)

    fid = _ensure_feed(k, author, platform)
    iid = content_id({"inbound_item": nfc(str(body)), "from": nfc(str(sender)),
                      "kind": nfc(str(kind)), "feed": fid, "intake": d["intake"]})
    assert_content(k.weft, author, iid, INBOUND, {
        "direction": "inbound", "platform": platform, "kind": nfc(str(kind)),
        "sender": nfc(str(sender)), "body": nfc(str(body)), "feed": fid,
        "intake": d["intake"],
        "instruction_eligible": False,          # inbound is DATA, never an instruction
    })
    assert_edge(k.weft, author, iid, ON_FEED, fid)         # group into the feed
    assert_edge(k.weft, author, iid, "captured_as", d["intake"])
    return {"item": iid, "feed": fid, "disposition": d["disposition"],
            "action": d["action"], "instruction_eligible": False,
            "produced": d["produced"], "intake": d["intake"]}


# -- the feed as a fold over the Weave ---------------------------------------
def feed(k, platform: str) -> list:
    """The captured inbound items for a platform, in log order. Folds the `on_feed` edges
    that point AT the platform's `feed` Cell (its edges_in) — every captured mention/
    comment/DM, all of it untrusted DATA."""
    w = k.weave()
    fid = _feed_id(platform)
    f = w.get(fid)
    if f is None:
        return []
    items = [w.get(e["src"]) for e in w.edges_to(f.id, ON_FEED)]
    return [i for i in items if i is not None]
