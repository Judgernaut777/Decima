//! The v3 vector script (port milestone 3) — a Rust re-run of the exact event
//! script in rust/vectors/generate.py's `build_v3`, so both the unit tests
//! and decima-verify re-derive every value in
//! rust/vectors/extended_vectors_v3.json from first principles: ATTEST
//! adjudication-collapse over forked MV heads, trusted tiered promotion
//! (seq-anchored genesis root, forged/self-granted promoter cells ignored),
//! EffectReceipt projections, and LEASE1 lease-expiry derivation.

use serde_json::{json, Value};

use crate::capability;
use crate::crypto::Keyring;
use crate::hashing::content_id;
use crate::model;
use crate::weave::{Weave, MERGE_MV};
use crate::weft::{Event, ASSERT, ATTEST, INVOKE};
use crate::weft_db::WeftDb;

#[derive(Debug)]
pub struct V3Result {
    pub root_pid: String,
    pub tester_pid: String,
    pub attester_pid: String,
    pub reckoner_pid: String,
    pub impostor_pid: String,
    pub attacker_pid: String,
    pub genesis_event_id: String,
    pub grinding_attempts: usize,
    pub attacker_genesis_event_id: String,
    pub type_headline_id: String,
    pub belief_cell: String,
    pub promoter_cell_id: String,
    pub forged_promoter_cell_id: String,
    pub attacker_promoter_cell_id: String,
    pub cap_pure_id: String,
    pub cap_fin_id: String,
    pub wallet_id: String,
    pub subwallet_id: String,
    pub wallet_view_id: String,
    pub card_id: String,
    pub expires_at: i64,
    pub receipt_key: String,
    pub receipt_unknown_id: String,
    pub receipt_definite_id: String,
    pub receipt_key_unknown_only: String,
    pub receipt_unknown_only_id: String,
    /// {id, verb, lamport, authorized, body} per RECORDED event, append order.
    pub events: Vec<Value>,
    /// Folded projections of single cells at log prefixes (fold_cell shape).
    pub mv_before: Value,
    pub mv_after: Value,
    pub promotion: Value,
    pub anti_grinding: Value,
    pub receipts: Value,
    pub leases: Value,
    pub genesis_author: String,
    pub state_root: String,
    pub head_after: String,
    pub lamport_after: i64,
    pub event_count: usize,
}

/// A unique temp DB path per invocation (uniqueness only — the path never
/// enters the pinned bytes).
fn temp_db_path() -> std::path::PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    std::env::temp_dir().join(format!(
        "decima-v3-{}-{}-{}.db",
        std::process::id(),
        n,
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0)
    ))
}

fn record(events: &mut Vec<Value>, ev: &Event) {
    events.push(json!({
        "id": ev.id,
        "verb": ev.verb,
        "lamport": ev.lamport,
        "authorized": ev.authorized,
        "body": ev.body,
    }));
}

/// The folded projection of ONE cell at a log prefix (generate.py's
/// `fold_cell`): content/heads/conflict verbatim, plus the quarantined flag
/// (null when the content is not an object).
fn fold_cell(events: &[Event], upto_seq: i64, cid: &str) -> Value {
    let w = Weave::fold_events(events.iter().filter(|e| e.seq <= upto_seq));
    let c = &w.cells[cid];
    json!({
        "content": c.content,
        "content_heads": c.content_heads,
        "in_conflict": c.in_conflict,
        "quarantined": c.content.get("quarantined").cloned().unwrap_or(Value::Null),
    })
}

/// Re-run generate.py's build_v3 script. See that function for the narrative;
/// every step below mirrors it event-for-event.
pub fn run_v3_script(keyring: &Keyring) -> V3Result {
    let mut kr = Keyring::new(keyring.master);
    let root = kr.mint("root", "human").id;
    let tester = kr.mint("tester", "human").id;
    let attester = kr.mint("attester", "agent").id;
    let reckoner = kr.mint("reckoner", "agent").id;
    let impostor = kr.mint("impostor", "agent").id;
    let attacker = kr.mint("attacker", "agent").id;

    let db_path = temp_db_path();
    let mut weft = WeftDb::open(&db_path, keyring).expect("open weft db");
    let mut events: Vec<Value> = Vec::new();

    // (1) GENESIS: parentless, root-authored — the constitutional root (seq 1).
    let genesis = weft
        .append(
            &root,
            ASSERT,
            json!({"cell": "realm:genesis", "type": "realm",
                   "content": {"name": "decima-v3", "founded": 1}}),
            None,
        )
        .unwrap();
    record(&mut events, &genesis);

    // (2) ATTEST adjudication-collapse (MERGE_SEMANTICS §4): an MV type, two
    //     concurrent heads, then a resolving ATTEST from a NON-root attester
    //     (the reference gates nothing on the adjudicator).
    let cid_headline = model::define_type_db(&mut weft, &root, "headline", Some(MERGE_MV)).unwrap();
    let bel = content_id(&json!({"belief": "ownership"}), "cell");
    let base = weft.head().unwrap().to_string();
    let pa = weft
        .append_with_parents(
            &tester,
            ASSERT,
            json!({"cell": bel, "type": "headline", "kind": "CONTENT",
                   "content": {"text": "Alice owns it"}}),
            None,
            Some(vec![base.clone()]),
        )
        .unwrap();
    record(&mut events, &pa);
    let pb = weft
        .append_with_parents(
            &attester,
            ASSERT,
            json!({"cell": bel, "type": "headline", "kind": "CONTENT",
                   "content": {"text": "Bob owns it"}}),
            None,
            Some(vec![base]),
        )
        .unwrap();
    record(&mut events, &pb);
    let all = weft.events().expect("verified read");
    let mv_before = fold_cell(&all, pb.seq, &bel);
    drop(all);
    let adj = weft
        .append(
            &attester,
            ATTEST,
            json!({"target_cell": bel, "predicate": "adjudicates", "resolution": "select",
                   "winner": pb.id, "evidence": [pa.id, pb.id], "claim": "owner resolved"}),
            None,
        )
        .unwrap();
    record(&mut events, &adj);
    let all = weft.events().expect("verified read");
    let mv_after = fold_cell(&all, adj.seq, &bel);
    drop(all);

    // (3) Trusted promotion (NONA_RECKONER §7): root declares the reckoner a
    //     "pure" promoter; impostor promote-ATTESTs (plain, then after a
    //     self-declared forged anchor) fail closed; the reckoner's lifts.
    let promoter_cell = content_id(&json!({"promoter": reckoner, "role": "root"}), "cell");
    let ev = weft
        .append(
            &root,
            ASSERT,
            json!({"cell": promoter_cell, "type": "promoter",
                   "content": {"principal": reckoner, "tiers": ["pure"]}}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let cap_pure = content_id(&json!({"cap": "forge-pure", "v": 1}), "cell");
    let mut pure_content = capability::capability_content(
        "forge-pure",
        "forge",
        "*",
        json!({"sandbox_only": true}),
        true,
        Value::Null,
        true,
        None,
        Some(&tester),
        Some(&tester),
    );
    pure_content["declared_effect_class"] = json!("pure");
    let pure_ev = weft
        .append(
            &tester,
            ASSERT,
            json!({"cell": cap_pure, "type": "capability", "content": pure_content}),
            None,
        )
        .unwrap();
    record(&mut events, &pure_ev);
    let all = weft.events().expect("verified read");
    let at_grant = fold_cell(&all, pure_ev.seq, &cap_pure)["quarantined"].clone();
    drop(all);
    let imp1 = weft
        .append(
            &impostor,
            ATTEST,
            json!({"target_cell": cap_pure, "promote": true, "tier": "pure"}),
            None,
        )
        .unwrap();
    record(&mut events, &imp1);
    let all = weft.events().expect("verified read");
    let after_impostor = fold_cell(&all, imp1.seq, &cap_pure)["quarantined"].clone();
    drop(all);
    let forged_promoter = content_id(&json!({"promoter": impostor, "role": "self"}), "cell");
    let ev = weft
        .append(
            &impostor,
            ASSERT,
            json!({"cell": forged_promoter, "type": "promoter",
                   "content": {"principal": impostor, "tiers": ["pure", "financial"]}}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let imp2 = weft
        .append(
            &impostor,
            ATTEST,
            json!({"target_cell": cap_pure, "promote": true, "tier": "pure"}),
            None,
        )
        .unwrap();
    record(&mut events, &imp2);
    let all = weft.events().expect("verified read");
    let after_forged = fold_cell(&all, imp2.seq, &cap_pure)["quarantined"].clone();
    drop(all);
    let rec1 = weft
        .append(
            &reckoner,
            ATTEST,
            json!({"target_cell": cap_pure, "promote": true, "tier": "pure"}),
            None,
        )
        .unwrap();
    record(&mut events, &rec1);
    let all = weft.events().expect("verified read");
    let after_reckoner = fold_cell(&all, rec1.seq, &cap_pure);
    drop(all);
    let promotion = json!({
        "at_grant": at_grant,
        "after_impostor": after_impostor,
        "after_forged_self_grant": after_forged,
        "after_reckoner": after_reckoner["quarantined"],
        "final_content": after_reckoner["content"],
    });

    // (4) Anti-grinding: grind (offline, deterministic) a second parentless
    //     event whose id sorts BEFORE the real genesis; the seq-anchored root
    //     still holds, so the attacker's self-declared "financial" promoter is
    //     ignored and its promote-ATTEST fails closed.
    let mut n = 0usize;
    let body = loop {
        let body = json!({"cell": "attacker:genesis", "type": "realm",
                          "content": {"name": "usurper", "attempt": n}});
        let payload = json!({"parents": [], "author": attacker, "authorized": null,
                             "verb": ASSERT, "body": body, "lamport": 1});
        if content_id(&payload, "event") < genesis.id {
            break body;
        }
        n += 1;
    };
    let grinding_attempts = n;
    let attacker_genesis = weft
        .append_with_parents(&attacker, ASSERT, body, None, Some(vec![]))
        .unwrap();
    record(&mut events, &attacker_genesis);
    let attacker_promoter = content_id(&json!({"promoter": attacker, "role": "usurp"}), "cell");
    let ev = weft
        .append(
            &attacker,
            ASSERT,
            json!({"cell": attacker_promoter, "type": "promoter",
                   "content": {"principal": attacker, "tiers": ["financial"]}}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let cap_fin = content_id(&json!({"cap": "wire", "v": 1}), "cell");
    let mut fin_content = capability::capability_content(
        "wire",
        "pay",
        "*",
        json!({"sandbox_only": true}),
        true,
        Value::Null,
        true,
        None,
        Some(&attacker),
        Some(&attacker),
    );
    fin_content["declared_effect_class"] = json!("financial");
    let ev = weft
        .append(
            &attacker,
            ASSERT,
            json!({"cell": cap_fin, "type": "capability", "content": fin_content}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let atk1 = weft
        .append(
            &attacker,
            ATTEST,
            json!({"target_cell": cap_fin, "promote": true, "tier": "financial"}),
            None,
        )
        .unwrap();
    record(&mut events, &atk1);
    let all = weft.events().expect("verified read");
    let anti_grinding = json!({
        "attacker_genesis_folds_first": attacker_genesis.id < genesis.id,
        "cap_fin_quarantined_after_attack": fold_cell(&all, atk1.seq, &cap_fin)["quarantined"],
    });
    drop(all);

    // (5) EffectReceipt projections (WEFT §8): UNKNOWN reconciled by a later
    //     definite receipt; an all-UNKNOWN key folds to None.
    let key = "logical-op-v3-1".to_string();
    let unk_id = content_id(&json!({"unknown_attempt": key}), "cell");
    let ev = weft
        .append(
            &tester,
            ASSERT,
            json!({"cell": unk_id, "type": "result", "kind": "CONTENT",
                   "content": {"of": null, "cap": "forge-pure", "status": "UNKNOWN",
                               "attempt": 0, "idempotency": key, "out": null,
                               "error": {"code": "ambiguous", "retryable": true,
                                         "message": "timeout"}}}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let def_id = content_id(&json!({"definite_attempt": key}), "cell");
    let ev = weft
        .append(
            &tester,
            ASSERT,
            json!({"cell": def_id, "type": "result", "kind": "CONTENT",
                   "content": {"of": null, "cap": "forge-pure", "status": "SUCCEEDED",
                               "attempt": 1, "idempotency": key, "supersedes": unk_id,
                               "out": {"sha": "abc"}, "cost": 3}}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let key_unknown_only = "logical-op-v3-2".to_string();
    let unk2_id = content_id(&json!({"unknown_attempt": key_unknown_only}), "cell");
    let ev = weft
        .append(
            &tester,
            ASSERT,
            json!({"cell": unk2_id, "type": "result", "kind": "CONTENT",
                   "content": {"of": null, "cap": "forge-pure", "status": "UNKNOWN",
                               "attempt": 0, "idempotency": key_unknown_only, "out": null}}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);

    // (6) LEASE1: a time-locked wallet (expires_at two lamports above the
    //     grant — the fixed tail pushes the frontier past it), a downhill
    //     sub-wallet, a budget-only view child (pure cascade victim), and a
    //     single-use card spent by one INVOKE.
    let expires_at = weft.lamport() + 2;
    let wallet = capability::capability_content(
        "wallet",
        "pay",
        "*",
        json!({"expires_at": expires_at}),
        true,
        Value::Null,
        false,
        None,
        Some(&tester),
        Some(&tester),
    );
    let wallet_id = content_id(&json!({"cap": "wallet", "v": 1}), "cell");
    let ev = weft
        .append(
            &tester,
            ASSERT,
            json!({"cell": wallet_id, "type": "capability", "content": wallet}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let subwallet = capability::attenuate(
        &wallet,
        &json!({"budget": 10}),
        &wallet_id,
        &tester,
        &tester,
    );
    let subwallet_id = content_id(&json!({"cap": "wallet", "v": 1, "att": 1}), "cell");
    let ev = weft
        .append(
            &tester,
            ASSERT,
            json!({"cell": subwallet_id, "type": "capability", "content": subwallet}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let view = capability::capability_content(
        "wallet-view",
        "pay",
        "*",
        json!({"budget": 5}),
        true,
        Value::Null,
        false,
        Some(&wallet_id),
        Some(&tester),
        Some(&tester),
    );
    let view_id = content_id(&json!({"cap": "wallet-view", "v": 1}), "cell");
    let ev = weft
        .append(
            &tester,
            ASSERT,
            json!({"cell": view_id, "type": "capability", "content": view}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let card = capability::capability_content(
        "card",
        "pay",
        "*",
        json!({"max_uses": 1}),
        true,
        Value::Null,
        false,
        None,
        Some(&tester),
        Some(&tester),
    );
    let card_id = content_id(&json!({"cap": "card", "v": 1}), "cell");
    let ev = weft
        .append(
            &tester,
            ASSERT,
            json!({"cell": card_id, "type": "capability", "content": card}),
            None,
        )
        .unwrap();
    record(&mut events, &ev);
    let ev = weft
        .append(
            &tester,
            INVOKE,
            json!({"cap": card_id, "args": {"amount": 1, "cost": 1},
                   "nonce": "decima-v3-nonce-1"}),
            Some(&card_id),
        )
        .unwrap();
    record(&mut events, &ev);

    let head_after = weft.head().unwrap().to_string();
    let lamport_after = weft.lamport();
    let event_count = weft.count().unwrap();
    let all = weft.events().expect("verified read");
    let mut final_w = Weave::fold_events(all.iter());

    let order: Vec<String> = final_w
        .receipts_for_idempotency(&key)
        .iter()
        .map(|c| c.id.clone())
        .collect();
    let canonical = final_w
        .canonical_for_idempotency(&key)
        .map(|c| c.id.clone());
    let unknown_only_order: Vec<String> = final_w
        .receipts_for_idempotency(&key_unknown_only)
        .iter()
        .map(|c| c.id.clone())
        .collect();
    let unknown_only_canonical = final_w
        .canonical_for_idempotency(&key_unknown_only)
        .map(|c| c.id.clone());
    let receipts = json!({
        "order": order,
        "canonical": canonical,
        "unknown_only_order": unknown_only_order,
        "unknown_only_canonical": unknown_only_canonical,
    });

    let mut outcomes = serde_json::Map::new();
    for cid in [&wallet_id, &subwallet_id, &view_id, &card_id] {
        let c = &final_w.cells[cid.as_str()];
        outcomes.insert(
            cid.clone(),
            json!({
                "lease_expired": c.lease_expired,
                "retracted": c.retracted,
                "cascade_root": c.cascade_root,
                "cascaded": c.cascaded,
            }),
        );
    }
    let mut invoke_counts: Vec<(String, i64)> = final_w
        .invoke_counts
        .iter()
        .map(|(k, v)| (k.clone(), *v))
        .collect();
    invoke_counts.sort();
    let leases = json!({
        "frontier_lamport": final_w.frontier_lamport,
        "invoke_counts": Value::Object(
            invoke_counts.into_iter().map(|(k, v)| (k, json!(v))).collect()
        ),
        "outcomes": Value::Object(outcomes),
    });

    let genesis_author = final_w.genesis_author.clone().unwrap_or_default();
    let state_root = final_w.state_root();
    drop(final_w);
    drop(all);
    drop(weft);
    let _ = std::fs::remove_file(&db_path);

    V3Result {
        root_pid: root,
        tester_pid: tester,
        attester_pid: attester,
        reckoner_pid: reckoner,
        impostor_pid: impostor,
        attacker_pid: attacker,
        genesis_event_id: genesis.id,
        grinding_attempts,
        attacker_genesis_event_id: attacker_genesis.id,
        type_headline_id: cid_headline,
        belief_cell: bel,
        promoter_cell_id: promoter_cell,
        forged_promoter_cell_id: forged_promoter,
        attacker_promoter_cell_id: attacker_promoter,
        cap_pure_id: cap_pure,
        cap_fin_id: cap_fin,
        wallet_id,
        subwallet_id,
        wallet_view_id: view_id,
        card_id,
        expires_at,
        receipt_key: key,
        receipt_unknown_id: unk_id,
        receipt_definite_id: def_id,
        receipt_key_unknown_only: key_unknown_only,
        receipt_unknown_only_id: unk2_id,
        events,
        mv_before,
        mv_after,
        promotion,
        anti_grinding,
        receipts,
        leases,
        genesis_author,
        state_root,
        head_after,
        lamport_after,
        event_count,
    }
}
