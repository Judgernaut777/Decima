"""KEY ROTATION / RECOVERY — the succession chain: identity survives its keys.

THE CRUX. Cycle 46 made identity SELF-CERTIFYING: a keyed principal's pid is
blake2b(public key) (`crypto.Keyring.mint_keyed` / `keyed_pid`). That is exactly right
for introduction — the id is a commitment to the key, so no name coordination and no
registry — but it fuses IDENTITY to ONE key forever: naively, a new key IS a new
principal, so a compromised or lost key would sever an agent from its entire history.
Rotation therefore needs (1) a stable ANCHOR separate from the current key, and (2) an
authenticated SUCCESSION CHAIN from the anchor to the current key. This module is that
layer (a sigchain, Keybase-style), composed OVER crypto.py — crypto.py is untouched.

  • ANCHOR — `principal_ref` = keyed_pid(GENESIS public key). It self-certifies at
    enrollment (ref == blake2b(genesis key), endorsement self-signed by the genesis
    key) and NEVER changes: every later key is reached from it by verified links, so
    the identity is stable across any number of rotations.
  • CHAIN — each `key_rotation` Cell endorses a successor key and is SIGNED BY THE
    CURRENT (soon-to-be-previous) key. `rotate` refuses at the door any endorsement
    that does not verify under the current key (an impostor or a stale/retired key) —
    nothing is recorded, the chain does not advance. Defense in depth: `key_history`
    RE-VERIFIES every link on fold, so a forged cell asserted through any other path
    is simply never woven into the chain (untrusted input is DATA, never a successor).
  • POINT-IN-TIME VERIFICATION — every signed statement carries a caller-supplied
    logical int `point`. `valid_key_at` walks the chain to the key that was current AT
    that point, and `verify_event` checks the signature against THAT key: old events
    keep verifying under the old key (preservation), new events verify under the new
    key (succession), and a NEW event signed by a RETIRED key does not verify.
  • RECOVERY — a key can be LOST, not just rotated. Enrollment may PRE-designate a
    recovery authority's public key (e.g. a key held by Morta / the human's gate);
    `recover` accepts a succession link endorsed by THAT key instead of the (lost)
    current one, and fails closed without it — no designated authority, no recovery,
    and the authority is pinned INSIDE the genesis cell's self-signed statement, so it
    cannot be swapped after the fact.

LAWS KEPT. Law 1: every rotation and every recorded event is an ASSERT Cell on the
Weft via `model.assert_content` (append-only; a rotation supersedes nothing — the old
key's history stays verifiable forever). Law 2: registering or rotating a key confers
ZERO authority — this layer decides only "who signed this, then", never "who may do
what"; no capability is minted or touched here. Law 4: the anchor is a content
commitment (blake2b of the genesis key) and every link is content-addressed over its
full signed statement. Fail closed + deterministic: every numeric is a logical INT
(floats are rejected at the door), verification returns False rather than raising, and
nothing here reads a clock or unseeded randomness.

Heartbeat profile note: events verified here are SIGNED STATEMENTS recorded as Cells
(the multi-party posture — the rotating principal is a keyholder whose statements the
Weft carries as data, like a keybook peer). Raw Weft-event signatures for LOCAL
kernel principals still verify through the Keyring's one-key-per-pid custodian; this
chain is the verification layer a rotating (keyed, possibly foreign) principal uses.
Binding an event's `point` to the recording event's lamport (anti-backdating by a
retired key) is the production step beyond this profile.
"""
import nacl.exceptions
import nacl.signing

from decima import model
from decima.crypto import Keyring
from decima.hashing import canonical, content_id

KEY_ROTATION = "key_rotation"        # a succession-chain link Cell
ROTATION_EVENT = "rotation_event"    # a recorded signed statement by a chained principal

_LINK_DOMAIN = b"decima:rotation:link:v1:"      # domain-separated signing contexts —
_EVENT_DOMAIN = b"decima:rotation:event:v1:"    # a link sig can never pass as an event sig

GENESIS = "genesis"      # link endorsed by the new key itself (self-certifying anchor)
CURRENT = "current"      # link endorsed by the current (soon-to-be-previous) key
RECOVERY = "recovery"    # link endorsed by the pre-designated recovery authority


class RotationError(Exception):
    """A refused rotation/recovery/record — fail closed, nothing was written."""


# ── small, fail-closed primitives ──────────────────────────────────────────────────

def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _contains_float(obj) -> bool:
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(_contains_float(k) or _contains_float(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(_contains_float(v) for v in obj)
    return False


def _no_floats(obj, where: str):
    if _contains_float(obj):
        raise RotationError(f"float in {where} — ints-not-floats: signed content is ints only")


def _key_hex(key) -> str:
    """Normalize a public key (VerifyKey | 32 raw bytes | hex str) to lowercase hex.
    Anything malformed is refused loud — a key that cannot be pinned cannot endorse."""
    if isinstance(key, nacl.signing.VerifyKey):
        return key.encode().hex()
    if isinstance(key, (bytes, bytearray)):
        raw = bytes(key)
    elif isinstance(key, str):
        try:
            raw = bytes.fromhex(key)
        except ValueError:
            raise RotationError("public key must be hex")
    else:
        raise RotationError("public key must be a VerifyKey, 32 bytes, or hex")
    if len(raw) != 32:
        raise RotationError("an Ed25519 public key is exactly 32 bytes")
    return raw.hex()


def _sign_with(signer, message: bytes) -> str:
    """Produce a signature hex with the caller's signer: a nacl SigningKey, a 32-byte
    seed, or a callable(bytes)->signature (the custody seam — e.g. a keystore's sign).
    The signer only ever PRODUCES bytes here; whether they count is decided by
    verification against the chain, never by who called."""
    if isinstance(signer, nacl.signing.SigningKey):
        return signer.sign(message).signature.hex()
    if isinstance(signer, (bytes, bytearray)) and len(signer) == 32:
        return nacl.signing.SigningKey(bytes(signer)).sign(message).signature.hex()
    if callable(signer):
        out = signer(message)
        return bytes(out).hex() if isinstance(out, (bytes, bytearray)) else str(out)
    raise RotationError("signer must be a SigningKey, a 32-byte seed, or a callable")


def _verify_sig(key_hex: str, message: bytes, sig_hex) -> bool:
    """Ed25519 verify — any malformed/forged input is False, never a raise."""
    try:
        vk = nacl.signing.VerifyKey(bytes.fromhex(key_hex))
        vk.verify(message, bytes.fromhex(sig_hex))
        return True
    except (nacl.exceptions.BadSignatureError, ValueError, TypeError, AttributeError):
        return False


def _link_statement(content: dict) -> bytes:
    """The canonical signed bytes of a chain link: EVERY field except the signature,
    domain-separated. The recovery authority is inside the genesis statement, so it is
    pinned by the genesis self-signature and cannot be swapped after enrollment."""
    return _LINK_DOMAIN + canonical({k: v for k, v in content.items() if k != "sig"})


def _event_statement(principal_ref: str, point: int, body) -> bytes:
    return _EVENT_DOMAIN + canonical(
        {"principal": principal_ref, "point": point, "body": body})


# ── the fold: the succession chain, re-verified link by link ───────────────────────

def _valid_link(content, ref, expect_seq, cur_key, cur_fp, recovery_key) -> bool:
    """Is this key_rotation content a VALID next link of the chain? Fail closed on
    anything: wrong principal/seq, non-int or non-advancing from_point, wrong
    prev_key, and above all an endorsement signature that does not verify under the
    key the chain says may endorse (genesis: the new key itself, self-certifying
    against the anchor; current: the previous key; recovery: the designated
    authority)."""
    try:
        if not isinstance(content, dict):
            return False
        if content.get("principal") != ref or content.get("seq") != expect_seq:
            return False
        fp, nk = content.get("from_point"), content.get("new_key")
        mode, sig = content.get("endorsed_by"), content.get("sig")
        if not _is_int(fp) or not isinstance(nk, str):
            return False
        stmt = _link_statement(content)
        if expect_seq == 0:
            # Genesis: self-certifying — the anchor commits to this exact key,
            # and the key endorses its own enrollment (proof of possession).
            if mode != GENESIS or content.get("prev_key") is not None:
                return False
            if Keyring.keyed_pid(nk) != ref:
                return False
            return _verify_sig(nk, stmt, sig)
        if fp <= cur_fp or content.get("prev_key") != cur_key or nk == cur_key:
            return False
        if mode == CURRENT:
            return _verify_sig(cur_key, stmt, sig)
        if mode == RECOVERY:
            return recovery_key is not None and _verify_sig(recovery_key, stmt, sig)
        return False
    except (TypeError, ValueError, AttributeError):
        return False


def _fold_chain(weave, principal_ref):
    """Fold the succession chain from the key_rotation Cells: returns
    (chain=[(key_hex, from_point), ...], recovery_key). Candidates are walked in
    deterministic content-id order; at each seq the FIRST link that VERIFIES is woven
    in and everything else is ignored — a forged cell never advances the chain, it
    just sits on the log as data."""
    cells = sorted(
        (c for c in weave.of_type(KEY_ROTATION)
         if isinstance(c.content, dict) and c.content.get("principal") == principal_ref),
        key=lambda c: c.id)
    chain, used = [], set()
    cur_key, cur_fp, recovery_key = None, None, None
    seq = 0
    while True:
        nxt = None
        for c in cells:
            if c.id not in used and _valid_link(c.content, principal_ref, seq,
                                                cur_key, cur_fp, recovery_key):
                nxt = c
                break
        if nxt is None:
            return chain, recovery_key
        used.add(nxt.id)
        cur_key, cur_fp = nxt.content["new_key"], nxt.content["from_point"]
        if seq == 0:
            recovery_key = nxt.content.get("recovery_key")
        chain.append((cur_key, cur_fp))
        seq += 1


def key_history(weave, principal_ref) -> list:
    """The verified succession chain for `principal_ref`, oldest first, as an ordered
    list of (public_key_hex, from_point). Entry 0 is the genesis key; the last entry
    is the CURRENT key. A pure projection — consults only the folded Weave."""
    return _fold_chain(weave, principal_ref)[0]


def valid_key_at(weave, principal_ref, point):
    """The public key (hex) that was valid for `principal_ref` AT logical `point`:
    the chain entry with the greatest from_point <= point. None (fail closed) for an
    unknown principal, a non-int point, or a point before the genesis enrollment."""
    if not _is_int(point):
        return None
    key = None
    for kh, fp in key_history(weave, principal_ref):
        if fp <= point:
            key = kh
        else:
            break
    return key


# ── enrollment, rotation, recovery — the append paths (all fail closed) ────────────

def enroll(k, genesis_public_key, *, signer, recovery_public_key=None, from_point=0):
    """Enroll a rotating principal: mint the stable anchor and the genesis link.
    Returns (principal_ref, cell_id) where principal_ref = keyed_pid(genesis key) —
    the identity that stays FIXED across every later rotation. The genesis link is
    self-endorsed (signed by the genesis key itself — proof of possession) and may
    PRE-designate a recovery authority's public key, pinned inside the signed
    statement. Refused loud if the endorsement does not verify or the ref is already
    enrolled — nothing recorded."""
    g = _key_hex(genesis_public_key)
    if not _is_int(from_point):
        raise RotationError("from_point must be a logical int (ints-not-floats)")
    rk = _key_hex(recovery_public_key) if recovery_public_key is not None else None
    ref = Keyring.keyed_pid(g)
    if _fold_chain(k.weave(), ref)[0]:
        raise RotationError(f"principal {ref} is already enrolled")
    content = {"principal": ref, "seq": 0, "prev_key": None, "new_key": g,
               "from_point": from_point, "endorsed_by": GENESIS, "recovery_key": rk}
    stmt = _link_statement(content)
    sig = _sign_with(signer, stmt)
    if not _verify_sig(g, stmt, sig):
        raise RotationError("genesis endorsement is not signed by the genesis key — "
                            "refused, nothing recorded (fail closed)")
    content["sig"] = sig
    cid = content_id({"key_rotation": content})
    model.assert_content(k.weft, k.decima.id, cid, KEY_ROTATION, content)
    return ref, cid


def _append_link(k, principal_ref, new_public_key, *, from_point, mode,
                 endorser_key, endorse_with, refusal):
    """Shared tail of rotate/recover: build the link, sign it, VERIFY the endorsement
    against the key the chain requires, and only then record. Refusal records
    NOTHING."""
    chain, _rk = _fold_chain(k.weave(), principal_ref)
    if not chain:
        raise RotationError(f"unknown principal {principal_ref} — enroll first (fail closed)")
    if not _is_int(from_point):
        raise RotationError("from_point must be a logical int (ints-not-floats)")
    cur_key, cur_fp = chain[-1]
    if from_point <= cur_fp:
        raise RotationError("from_point must advance past the current key's from_point")
    nk = _key_hex(new_public_key)
    if nk == cur_key:
        raise RotationError("the successor key must differ from the current key")
    content = {"principal": principal_ref, "seq": len(chain), "prev_key": cur_key,
               "new_key": nk, "from_point": from_point, "endorsed_by": mode,
               "recovery_key": None}
    stmt = _link_statement(content)
    sig = _sign_with(endorse_with, stmt)
    if not _verify_sig(endorser_key, stmt, sig):
        raise RotationError(refusal)
    content["sig"] = sig
    cid = content_id({"key_rotation": content})
    model.assert_content(k.weft, k.decima.id, cid, KEY_ROTATION, content)
    return cid


def rotate(k, principal_ref, new_public_key, *, signer, from_point):
    """Rotate the principal's signing key: append a key_rotation Cell endorsing
    `new_public_key`, authenticated by the CURRENT key. From `from_point` on, only
    the new key signs valid events; every event before it keeps verifying under the
    key that was current then. An endorsement NOT signed by the current key — an
    impostor, or a stale/retired key — is REFUSED and NOTHING is recorded."""
    chain, _rk = _fold_chain(k.weave(), principal_ref)
    cur_key = chain[-1][0] if chain else None
    return _append_link(
        k, principal_ref, new_public_key, from_point=from_point, mode=CURRENT,
        endorser_key=cur_key, endorse_with=signer,
        refusal="rotation endorsement is not signed by the CURRENT key — "
                "refused, nothing recorded (fail closed)")


def recover(k, principal_ref, new_public_key, *, authority, from_point):
    """Gated recovery for a LOST key: append a succession link endorsed not by the
    (lost) current key but by the recovery authority PRE-designated at enrollment
    (e.g. a Morta-held key). Fails closed without it: no designated authority means
    no recovery path at all, and an endorsement that does not verify under the
    designated authority's key records nothing."""
    _chain, recovery_key = _fold_chain(k.weave(), principal_ref)
    if _chain and recovery_key is None:
        raise RotationError("no recovery authority was pre-designated at enrollment — "
                            "recovery is closed for this principal")
    return _append_link(
        k, principal_ref, new_public_key, from_point=from_point, mode=RECOVERY,
        endorser_key=recovery_key, endorse_with=authority,
        refusal="recovery endorsement is not signed by the pre-designated recovery "
                "authority — refused, nothing recorded (fail closed)")


# ── signed events against the chain ────────────────────────────────────────────────

def sign_event(signer, principal_ref, point: int, body: dict) -> dict:
    """Produce a signed statement by a chained principal at logical `point` (an int
    the caller supplies — never a clock). The signature covers the canonical
    (principal, point, body) under the event domain. Floats are refused at the door."""
    if not _is_int(point):
        raise RotationError("an event's point must be a logical int (ints-not-floats)")
    _no_floats(body, "event body")
    sig = _sign_with(signer, _event_statement(principal_ref, point, body))
    return {"principal": principal_ref, "point": point, "body": body, "sig": sig}


def verify_event(weave, principal_ref, event) -> bool:
    """Verify a signed event against the succession chain: TRUE iff its signature
    verifies under the key that was valid for `principal_ref` AT the event's point —
    old events under the old key, new events under the new key, and a new event
    signed by a RETIRED key is False. Accepts the dict from `sign_event` or a
    recorded rotation_event Cell. Fail closed: anything malformed, a float point, an
    unknown principal, or a point before enrollment is False, never a raise."""
    try:
        content = getattr(event, "content", event)
        if not isinstance(content, dict) or content.get("principal") != principal_ref:
            return False
        point = content.get("point")
        if not _is_int(point) or _contains_float(content.get("body")):
            return False
        key = valid_key_at(weave, principal_ref, point)
        if key is None:
            return False
        stmt = _event_statement(principal_ref, point, content.get("body"))
        return _verify_sig(key, stmt, content.get("sig"))
    except (TypeError, ValueError, AttributeError):
        return False


def record_event(k, event: dict) -> str:
    """Record a signed event as a rotation_event Cell on the Weft (Law 1) — but only
    an event that VERIFIES against the chain at its own point; an unverifiable
    statement is refused (fail closed), it stays outside the log."""
    if not verify_event(k.weave(), event.get("principal"), event):
        raise RotationError("event does not verify against the succession chain at its "
                            "point — refused, nothing recorded (fail closed)")
    content = {"principal": event["principal"], "point": event["point"],
               "body": event["body"], "sig": event["sig"]}
    cid = content_id({"rotation_event": content})
    model.assert_content(k.weft, k.decima.id, cid, ROTATION_EVENT, content)
    return cid
