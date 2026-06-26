"""VAULT1 — the sovereign data substrate (capability D6, the "OneDrive equivalent").

Your data IS the Weft, so backup / disaster-recovery / multi-device are three
projections of one fact (a Weave is a deterministic fold of signed events). This
check proves the whole contract on the LIVE Weft, composing snapshot/sync/gossip/
secrets through `decima.vault` (no core edits):

  - BACKUP: a verifiable snapshot + an ENCRYPTED export blob that is OPAQUE — the
    clear application state does not appear in it (encryption keyed by the recovery
    phrase via the secrets broker);
  - RESTORE on a FRESH device (a new, empty Weft): decrypt + replay-to-frontier →
    a Weave whose state_root EQUALS the original, byte-for-byte (FOLD §11.1);
  - a WRONG recovery phrase fails CLOSED (no Weft mutated, no partial state);
  - MULTI-DEVICE: 3 devices each make a concurrent edit, then fold-replicate
    (gossip CRDT merge) → ONE identical state_root, every concurrent add surviving.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import vault, secrets, model, sync
from decima.weft import Weft, ASSERT
from decima.weave import Weave, MERGE_ORSET
from decima.hashing import content_id


def run(k, line):
    line("\n== VAULT (sovereign data substrate · backup · restore · multi-device) ==")
    d = tempfile.mkdtemp()
    broker = secrets.SecretsBroker(k)
    PHRASE = "correct horse battery staple"
    WRONG = "incorrect horse battery staple"

    origin_root = Weave.fold(k.weft).state_root()
    origin_n = k.weft.count()

    # ---- BACKUP: snapshot + encrypted, OPAQUE export ------------------------
    bk = vault.backup(k, recovery_phrase=PHRASE, broker=broker)
    assert bk["state_root"] == origin_root, "backup must commit to the live state_root"
    assert bk["n_events"] == origin_n
    # The export is opaque: clear application state does not appear in the blob.
    # Probe with a real cell payload fragment drawn from the live log.
    sample = next(iter(k.weft.db.execute(
        "SELECT payload FROM events ORDER BY seq LIMIT 1")))[0]
    probe = sample[:24]
    assert vault.export_is_opaque(bk, probe), "export leaked clear state!"
    assert vault.export_is_opaque(bk, "executor"), "export leaked a clear principal label!"
    assert isinstance(bk["export"], (bytes, bytearray)) and len(bk["export"]) > 0
    line(f"  backup: snapshot @root {origin_root[:12]} ({bk['n_events']} events) + "
         f"{len(bk['export'])}B encrypted export — opaque (clear state absent) ✓")

    # ---- RESTORE on a FRESH device → identical state_root -------------------
    dev1 = os.path.join(d, "device1.db")
    w1, weft1 = vault.restore(bk, recovery_phrase=PHRASE, keyring=k.keyring, db_path=dev1)
    assert weft1.count() == origin_n, "fresh device must replay every event"
    assert w1.state_root() == origin_root, "restored state_root != original"
    line(f"  restore on FRESH device → replay-to-frontier; state_root "
         f"{w1.state_root()[:12]} EQUALS origin {origin_root[:12]} ✓  (FOLD §11.1)")

    # ---- a WRONG recovery phrase fails CLOSED -------------------------------
    dev_bad = os.path.join(d, "device_bad.db")
    failed_closed = False
    try:
        vault.restore(bk, recovery_phrase=WRONG, keyring=k.keyring, db_path=dev_bad,
                      broker=broker)
    except vault.VaultError as e:
        failed_closed = True
        why = str(e)
    assert failed_closed, "a WRONG recovery phrase must fail closed"
    # fails closed = no usable state materialized: the bad-device Weft is empty.
    assert not os.path.exists(dev_bad) or Weft(dev_bad, k.keyring).count() == 0, \
        "a failed restore must not materialize partial state"
    line(f"  wrong recovery phrase → fails CLOSED ({why}); no partial state ✓")

    # ---- MULTI-DEVICE: fold-replication → ONE state_root --------------------
    # Three devices, each a fresh restore of the same backup (so each starts at the
    # identical origin state_root). Then each makes a UNIQUE, concurrent edit on a
    # shared OR-set, diverging — and fold-replication (gossip CRDT) reconverges them.
    devices = []
    for i in range(3):
        _, wfi = vault.restore(bk, recovery_phrase=PHRASE, keyring=k.keyring,
                               db_path=os.path.join(d, f"mdev{i}.db"))
        vault.add_device(devices, wfi)
    assert len({Weave.fold(x).state_root() for x in devices}) == 1, \
        "all devices start at one identical state_root"

    # A shared OR-set type, defined on device0 and propagated, then a concurrent add
    # per device (conflict-free CRDT — adds must all survive, none overwritten).
    author = k.root.id
    model.define_type(devices[0], author, "vault_roster", merge_class=MERGE_ORSET)
    roster = content_id({"vault_roster": "members"})
    devices[0].append(author, ASSERT, {"cell": roster, "type": "vault_roster",
                                       "kind": "CONTENT", "content": {"op": "add",
                                       "element": "seed"}})
    for x in devices[1:]:                 # propagate the type+seed so all devices agree
        sync.pull(devices[0], x)
    for i, x in enumerate(devices):       # each device: a unique concurrent add
        x.append(author, ASSERT, {"cell": roster, "type": "vault_roster",
                                  "kind": "CONTENT",
                                  "content": {"op": "add", "element": f"dev{i}"}})

    pre = {Weave.fold(x).state_root() for x in devices}
    assert len(pre) > 1, "devices should diverge after concurrent edits"
    line(f"  multi-device: {len(devices)} devices diverged "
         f"({len(pre)} distinct roots) after concurrent edits")

    rep = vault.sync_devices(devices, keyring=k.keyring)
    assert rep["converged"] and rep["state_root"], rep
    assert len({Weave.fold(x).state_root() for x in devices}) == 1
    members = sorted(Weave.fold(devices[0]).get(roster).content["elements"])
    assert members == ["dev0", "dev1", "dev2", "seed"], members
    line(f"  fold-replication (gossip CRDT, {rep['rounds']} rounds, "
         f"{rep['moved_total']} transfers) → ONE state_root {rep['state_root'][:12]} ✓")
    line(f"  conflict-free: every device's concurrent add survives: {members} ✓")
    line("  → your data IS the Weft: backup=snapshot+encrypted export, "
         "restore=replay-to-frontier, multi-device=CRDT fold-replication ✓")
