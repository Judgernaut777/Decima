//! Milestone-3 adversarial tests (verifier v3 criterion 4): the fold's new
//! trust/decision surfaces must FAIL CLOSED under attack —
//!   - a forged or self-granted `promoter` cell confers NO promotion
//!     authority (only a root-authored anchor does);
//!   - a second PARENTLESS event whose id sorts before the real genesis
//!     (grindable content-addressed id) does NOT hijack the seq-anchored
//!     genesis root;
//!   - a lapsed lease (expires_at reached / max_uses spent) fails closed and
//!     cascades to attenuated children;
//!   - adjudication collapses MV heads exactly per MERGE_SEMANTICS §4 (any
//!     principal — the reference gates nothing; binding only named evidence).

use decima_core::capability;
use decima_core::crypto::Keyring;
use decima_core::hashing::content_id;
use decima_core::reference::MASTER_SEED;
use decima_core::reference_v3::run_v3_script;
use decima_core::weave::{Weave, MERGE_MV};
use decima_core::weft::{Weft, ASSERT, ATTEST, INVOKE};
use serde_json::{json, Value};

fn keyring() -> Keyring {
    Keyring::new(MASTER_SEED)
}

/// Mint the v3 principals in the generator's order and return them by role.
struct Cast {
    root: String,
    tester: String,
    attester: String,
    reckoner: String,
    impostor: String,
    attacker: String,
}

fn cast(kr: &Keyring) -> Cast {
    let mut k = Keyring::new(kr.master);
    Cast {
        root: k.mint("root", "human").id,
        tester: k.mint("tester", "human").id,
        attester: k.mint("attester", "agent").id,
        reckoner: k.mint("reckoner", "agent").id,
        impostor: k.mint("impostor", "agent").id,
        attacker: k.mint("attacker", "agent").id,
    }
}

fn genesis(weft: &mut Weft, root: &str) -> decima_core::weft::Event {
    weft.append(
        root,
        ASSERT,
        json!({"cell": "realm:genesis", "type": "realm",
               "content": {"name": "t", "founded": 1}}),
        None,
    )
    .unwrap()
}

/// A quarantined candidate capability declaring an effect-class tier.
fn candidate(name: &str, tier: &str, by: &str) -> Value {
    let mut c = capability::capability_content(
        name,
        "forge",
        "*",
        json!({"sandbox_only": true}),
        true,
        Value::Null,
        true,
        None,
        Some(by),
        Some(by),
    );
    c["declared_effect_class"] = json!(tier);
    c
}

/// Assert a capability cell and return its id.
fn assert_cap(weft: &mut Weft, by: &str, tag: &str, content: &Value) -> String {
    let cid = content_id(&json!({"cap": tag}), "cell");
    weft.append(
        by,
        ASSERT,
        json!({"cell": cid, "type": "capability", "content": content}),
        None,
    )
    .unwrap();
    cid
}

fn promoter_cell(weft: &mut Weft, by: &str, principal: &str, tiers: &[&str], tag: &str) -> String {
    let cid = content_id(&json!({"promoter": tag}), "cell");
    weft.append(
        by,
        ASSERT,
        json!({"cell": cid, "type": "promoter",
               "content": {"principal": principal, "tiers": tiers}}),
        None,
    )
    .unwrap();
    cid
}

#[test]
fn forged_promoter_cell_ignored_self_grant_refused() {
    let kr = keyring();
    let c = cast(&kr);
    let mut weft = Weft::new(&kr);
    genesis(&mut weft, &c.root);
    // Root trusts ONLY the reckoner for tier "pure".
    promoter_cell(&mut weft, &c.root, &c.reckoner, &["pure"], "root-anchor");
    let cap = assert_cap(
        &mut weft,
        &c.tester,
        "forge-pure",
        &candidate("forge-pure", "pure", &c.tester),
    );

    // An untrusted principal's promote-ATTEST does NOT lift quarantine…
    weft.append(
        &c.impostor,
        ATTEST,
        json!({"target_cell": cap, "promote": true, "tier": "pure"}),
        None,
    )
    .unwrap();
    // …even after SELF-DECLARING a promoter anchor for exactly that tier.
    promoter_cell(
        &mut weft,
        &c.impostor,
        &c.impostor,
        &["pure"],
        "self-anchor",
    );
    weft.append(
        &c.impostor,
        ATTEST,
        json!({"target_cell": cap, "promote": true, "tier": "pure"}),
        None,
    )
    .unwrap();
    let mut w = Weave::fold(&weft);
    let cell = w
        .of_type("capability")
        .into_iter()
        .find(|x| x.id == cap)
        .unwrap();
    assert_eq!(
        cell.content.get("quarantined"),
        Some(&json!(true)),
        "a self-declared promoter anchor must be ignored (fail closed)"
    );
    // …but the failed attempts ARE recorded as attestation evidence.
    assert_eq!(cell.attestations.len(), 2);

    // The root-declared reckoner's promote-ATTEST lifts: quarantined cleared,
    // sandbox_only stripped, resolved head kept in sync.
    weft.append(
        &c.reckoner,
        ATTEST,
        json!({"target_cell": cap, "promote": true, "tier": "pure"}),
        None,
    )
    .unwrap();
    let mut w = Weave::fold(&weft);
    let cell = w
        .of_type("capability")
        .into_iter()
        .find(|x| x.id == cap)
        .unwrap();
    assert_eq!(cell.content.get("quarantined"), Some(&json!(false)));
    assert_eq!(
        cell.content.pointer("/caveats/sandbox_only"),
        None,
        "promotion strips the sandbox_only caveat"
    );
    assert_eq!(cell.content_heads, vec![cell.content.clone()]);

    // Tier discipline: the reckoner is trusted for "pure" ONLY — a promote on
    // a "financial" candidate by the reckoner fails closed.
    let fin = assert_cap(
        &mut weft,
        &c.tester,
        "wire",
        &candidate("wire", "financial", &c.tester),
    );
    weft.append(
        &c.reckoner,
        ATTEST,
        json!({"target_cell": fin, "promote": true, "tier": "financial"}),
        None,
    )
    .unwrap();
    let mut w = Weave::fold(&weft);
    let cell = w
        .of_type("capability")
        .into_iter()
        .find(|x| x.id == fin)
        .unwrap();
    assert_eq!(cell.content.get("quarantined"), Some(&json!(true)));

    // A RETRACTED root promoter anchor no longer confers authority.
    let anchor2 = promoter_cell(&mut weft, &c.root, &c.attester, &["financial"], "anchor2");
    weft.append(&c.root, "RETRACT", json!({"cell": anchor2}), None)
        .unwrap();
    weft.append(
        &c.attester,
        ATTEST,
        json!({"target_cell": fin, "promote": true, "tier": "financial"}),
        None,
    )
    .unwrap();
    let mut w = Weave::fold(&weft);
    let cell = w
        .of_type("capability")
        .into_iter()
        .find(|x| x.id == fin)
        .unwrap();
    assert_eq!(
        cell.content.get("quarantined"),
        Some(&json!(true)),
        "a retracted promoter anchor must not confer authority"
    );
}

#[test]
fn genesis_anchored_by_seq_not_grindable_id() {
    let kr = keyring();
    let c = cast(&kr);
    let mut weft = Weft::new(&kr);
    let genesis = genesis(&mut weft, &c.root);

    // Grind a SECOND parentless event whose id sorts BEFORE the real genesis.
    // Under an id-order anchor it folds first and hijacks the root; the
    // seq-anchored root must hold.
    let mut n = 0;
    let body = loop {
        let body = json!({"cell": "attacker:genesis", "type": "realm",
                          "content": {"name": "usurper", "attempt": n}});
        let payload = json!({"parents": [], "author": c.attacker, "authorized": null,
                             "verb": ASSERT, "body": body, "lamport": 1});
        if content_id(&payload, "event") < genesis.id {
            break body;
        }
        n += 1;
    };
    let fake = weft
        .append_with_parents(&c.attacker, ASSERT, body, None, Some(vec![]))
        .unwrap();
    assert!(fake.id < genesis.id && fake.parents.is_empty() && fake.lamport == 1);

    // The attacker self-declares a promoter for its own tier and promotes.
    promoter_cell(&mut weft, &c.attacker, &c.attacker, &["financial"], "usurp");
    let cap = assert_cap(
        &mut weft,
        &c.attacker,
        "wire",
        &candidate("wire", "financial", &c.attacker),
    );
    weft.append(
        &c.attacker,
        ATTEST,
        json!({"target_cell": cap, "promote": true, "tier": "financial"}),
        None,
    )
    .unwrap();

    let w = Weave::fold(&weft);
    assert_eq!(
        w.genesis_author.as_deref(),
        Some(c.root.as_str()),
        "the first-committed (smallest-seq) parentless event is the root — the impostor's \
         smaller-id parentless event must not hijack it"
    );
    let mut w = Weave::fold(&weft);
    let cell = w
        .of_type("capability")
        .into_iter()
        .find(|x| x.id == cap)
        .unwrap();
    assert_eq!(
        cell.content.get("quarantined"),
        Some(&json!(true)),
        "the attacker's self-promoter must be ignored even though its genesis folds first"
    );
}

#[test]
fn no_genesis_anchored_means_no_tiered_lift() {
    // A Weave folded from events with NO parentless genesis (e.g. a window
    // reassembled above a snapshot frontier) filters EVERY promoter: a
    // tiered promote-ATTEST can never lift — fail closed on ambiguity.
    let kr = keyring();
    let c = cast(&kr);
    let mut weft = Weft::new(&kr);
    genesis(&mut weft, &c.root);
    promoter_cell(&mut weft, &c.root, &c.reckoner, &["pure"], "root-anchor");
    let cap = assert_cap(
        &mut weft,
        &c.tester,
        "forge-pure",
        &candidate("forge-pure", "pure", &c.tester),
    );
    weft.append(
        &c.reckoner,
        ATTEST,
        json!({"target_cell": cap, "promote": true, "tier": "pure"}),
        None,
    )
    .unwrap();
    // Fold ONLY the tail (skip the genesis event): no anchor → no lift.
    let tail: Vec<_> = weft.events().iter().skip(1).cloned().collect();
    let mut w = Weave::fold_events(tail.iter());
    assert_eq!(w.genesis_author, None);
    let cell = w
        .of_type("capability")
        .into_iter()
        .find(|x| x.id == cap)
        .unwrap();
    assert_eq!(cell.content.get("quarantined"), Some(&json!(true)));
}

#[test]
fn adjudication_collapses_per_reference_rules() {
    let kr = keyring();
    let c = cast(&kr);
    let mut weft = Weft::new(&kr);
    genesis(&mut weft, &c.root);
    // TYPE_DEF with merge_class "mv" (in-memory helper; same body shape).
    decima_core::model::define_type(&mut weft, &c.root, "headline", Some(MERGE_MV), None).unwrap();
    let bel = content_id(&json!({"belief": "x"}), "cell");
    let base = weft.events().last().unwrap().id.clone();
    let pa = weft
        .append_with_parents(
            &c.tester,
            ASSERT,
            json!({"cell": bel, "type": "headline", "kind": "CONTENT",
                   "content": {"text": "A"}}),
            None,
            Some(vec![base.clone()]),
        )
        .unwrap();
    let pb = weft
        .append_with_parents(
            &c.attester,
            ASSERT,
            json!({"cell": bel, "type": "headline", "kind": "CONTENT",
                   "content": {"text": "B"}}),
            None,
            Some(vec![base]),
        )
        .unwrap();
    let w = Weave::fold(&weft);
    let cell = &w.cells[&bel];
    assert!(cell.in_conflict && cell.content_heads.len() == 2);

    // A plain (non-adjudication) ATTEST does NOT collapse the heads.
    weft.append(
        &c.root,
        ATTEST,
        json!({"target_cell": bel, "claim": "noted"}),
        None,
    )
    .unwrap();
    let w = Weave::fold(&weft);
    assert!(w.cells[&bel].in_conflict);

    // An adjudication from ANY principal (the reference gates nothing on the
    // adjudicator) collapses to the named winner, superseding the other head.
    // Here the adjudicator is NOT the root — the collapse must still happen.
    let pc = weft
        .append_with_parents(
            &c.impostor,
            ASSERT,
            json!({"cell": bel, "type": "headline", "kind": "CONTENT",
                   "content": {"text": "C"}}),
            None,
            Some(vec![pa.id.clone()]),
        )
        .unwrap();
    weft.append(
        &c.impostor,
        ATTEST,
        json!({"target_cell": bel, "predicate": "adjudicates", "resolution": "select",
               "winner": pb.id, "evidence": [pa.id, pb.id], "claim": "resolved"}),
        None,
    )
    .unwrap();
    let w = Weave::fold(&weft);
    let cell = &w.cells[&bel];
    // §4.3: the resolution binds only the NAMED evidence — the third head
    // (pc, unobserved by the adjudication) is still live, so the cell is
    // still in conflict between B and C.
    assert!(
        cell.in_conflict,
        "unnamed concurrent head keeps the conflict open"
    );
    assert_eq!(
        cell.content_heads,
        vec![json!({"text": "B"}), json!({"text": "C"})]
    );
    // A full adjudication naming all three heads resolves to the winner.
    weft.append(
        &c.impostor,
        ATTEST,
        json!({"target_cell": bel, "predicate": "adjudicates", "resolution": "select",
               "winner": pb.id, "evidence": [pa.id, pb.id, pc.id], "claim": "final"}),
        None,
    )
    .unwrap();
    let w = Weave::fold(&weft);
    let cell = &w.cells[&bel];
    assert!(!cell.in_conflict);
    assert_eq!(cell.content_heads, vec![json!({"text": "B"})]);
    assert_eq!(cell.content, json!({"text": "B"}));
    // The losing branches stay in history (provenance), not erased.
    assert!(cell.provenance.contains(&pa.id) && cell.provenance.contains(&pc.id));
}

#[test]
fn lapsed_lease_fails_closed_and_cascades() {
    let kr = keyring();
    let c = cast(&kr);
    let mut weft = Weft::new(&kr);
    genesis(&mut weft, &c.root);

    // TIME-LOCK: wallet expires_at four lamports above the grant (so the
    // pre-lapse prefix fold below is still live, and the two note appends
    // push the frontier past it); a budget-only child descends from it.
    let expires_at = weft.events().last().unwrap().lamport + 4;
    let wallet = capability::capability_content(
        "wallet",
        "pay",
        "*",
        json!({"expires_at": expires_at}),
        true,
        Value::Null,
        false,
        None,
        Some(&c.tester),
        Some(&c.tester),
    );
    let wallet_id = assert_cap(&mut weft, &c.tester, "wallet", &wallet);
    let child = capability::capability_content(
        "wallet-view",
        "pay",
        "*",
        json!({"budget": 5}),
        true,
        Value::Null,
        false,
        Some(&wallet_id),
        Some(&c.tester),
        Some(&c.tester),
    );
    let child_id = assert_cap(&mut weft, &c.tester, "wallet-view", &child);
    // SINGLE-USE: card with max_uses 1, spent once.
    let card = capability::capability_content(
        "card",
        "pay",
        "*",
        json!({"max_uses": 1}),
        true,
        Value::Null,
        false,
        None,
        Some(&c.tester),
        Some(&c.tester),
    );
    let card_id = assert_cap(&mut weft, &c.tester, "card", &card);
    weft.append(
        &c.tester,
        INVOKE,
        json!({"cap": card_id, "args": {}, "nonce": "n1"}),
        Some(&card_id),
    )
    .unwrap();

    // Before the frontier reaches expires_at AND before the spend... this is
    // already after; first prove the pre-lapse fold treats both as LIVE by
    // folding only the events up to the wallet assert.
    let live = Weave::fold_events(weft.events().iter().take(3));
    let wallet_cell = &live.cells[&wallet_id];
    assert!(!wallet_cell.lease_expired && !wallet_cell.retracted);

    // Two more events push the logical frontier past expires_at.
    weft.append(
        &c.tester,
        ASSERT,
        json!({"cell": "n:1", "type": "note", "content": {"t": 1}}),
        None,
    )
    .unwrap();
    weft.append(
        &c.tester,
        ASSERT,
        json!({"cell": "n:2", "type": "note", "content": {"t": 2}}),
        None,
    )
    .unwrap();
    let mut w = Weave::fold(&weft);
    assert!(w.frontier_lamport >= expires_at);
    let wallet_cell = &w.cells[&wallet_id];
    assert!(
        wallet_cell.lease_expired && wallet_cell.retracted && wallet_cell.cascade_root,
        "an expired time-locked lease must be a lease_expired cascade root"
    );
    let card_cell = &w.cells[&card_id];
    assert!(
        card_cell.lease_expired && card_cell.retracted && card_cell.cascade_root,
        "an exhausted single-use lease must fail closed exactly like a revoked grant"
    );
    let child_cell = &w.cells[&child_id];
    assert!(
        child_cell.retracted && child_cell.cascaded && !child_cell.lease_expired,
        "the child fails closed PURELY by the cascade (no lease of its own)"
    );
    // The lapsed caps leave the live projection entirely.
    assert!(w.of_type("capability").is_empty());

    // Idempotency: re-deriving the cascade changes nothing (pure pass).
    let root1 = w.state_root();
    w.cascade_retractions();
    w.cascade_retractions();
    assert_eq!(w.state_root(), root1);
}

#[test]
fn receipts_reconcile_unknown_to_latest_definite() {
    let kr = keyring();
    let c = cast(&kr);
    let mut weft = Weft::new(&kr);
    genesis(&mut weft, &c.root);
    let key = "logical-op-t";
    let unk = content_id(&json!({"unknown": key}), "cell");
    decima_core::model::assert_content(
        &mut weft,
        &c.tester,
        &unk,
        "result",
        json!({"status": "UNKNOWN", "idempotency": key, "attempt": 0}),
    )
    .unwrap();
    let mut w = Weave::fold(&weft);
    assert!(
        w.canonical_for_idempotency(key).is_none(),
        "all-UNKNOWN folds to None"
    );
    let definite = content_id(&json!({"definite": key}), "cell");
    decima_core::model::assert_content(
        &mut weft,
        &c.tester,
        &definite,
        "result",
        json!({"status": "SUCCEEDED", "idempotency": key, "attempt": 1, "supersedes": unk}),
    )
    .unwrap();
    let mut w = Weave::fold(&weft);
    let order: Vec<&str> = w
        .receipts_for_idempotency(key)
        .iter()
        .map(|c| c.id.as_str())
        .collect();
    assert_eq!(order, vec![unk.as_str(), definite.as_str()]);
    let canon = w.canonical_for_idempotency(key).unwrap();
    assert_eq!(canon.id, definite);
    assert_eq!(canon.content["status"], json!("SUCCEEDED"));
    // The earlier UNKNOWN is still present (additive — not retracted).
    assert!(w.cells.contains_key(&unk) && !w.cells[&unk].retracted);
}

#[test]
fn v3_script_reruns_deterministically() {
    // The Rust re-run of generate.py's build_v3 is itself deterministic and
    // its anti-grinding search is a fixed point (grinds 0 for this script).
    let kr = keyring();
    let a = run_v3_script(&kr);
    let b = run_v3_script(&kr);
    assert_eq!(a.state_root, b.state_root);
    assert_eq!(a.events, b.events);
    assert_eq!(a.grinding_attempts, 0);
    assert!(a.anti_grinding["attacker_genesis_folds_first"]
        .as_bool()
        .unwrap());
    assert_eq!(a.genesis_author, a.root_pid);
    assert_eq!(a.promotion["after_forged_self_grant"], json!(true));
    assert_eq!(a.promotion["after_reckoner"], json!(false));
}
