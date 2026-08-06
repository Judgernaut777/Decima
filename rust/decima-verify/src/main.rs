//! decima-verify — conformance gate for the Rust port.
//!
//! Loads heartbeat/protocol/reference_vectors.json and re-derives EVERY value
//! in it from first principles via decima-core: canonical bytes, cell/event
//! content ids, blob ids, principal pids and public keys, Ed25519 signatures,
//! and the full fold (event ids/bodies/lamports, capability ids, state_root,
//! type_counts, event_count). Prints a per-section report; exits 0 iff every
//! section matches.

#![forbid(unsafe_code)]

use std::path::PathBuf;
use std::process::ExitCode;

use decima_core::crypto::Keyring;
use decima_core::hashing::{blob_id, canonical, content_id, python_dumps_sorted};
use decima_core::reference::{run_fold_script, MASTER_SEED};
use decima_core::reference_ext::run_extended_script;
use decima_core::reference_v3::run_v3_script;
use serde_json::Value;

struct Report {
    checks: usize,
    failures: Vec<String>,
}

impl Report {
    fn new() -> Self {
        Report {
            checks: 0,
            failures: Vec::new(),
        }
    }

    fn expect_eq(&mut self, what: &str, got: &Value, want: &Value) {
        self.checks += 1;
        if got != want {
            self.failures
                .push(format!("{what}\n  want: {want}\n  got:  {got}"));
        }
    }

    fn expect_str(&mut self, what: &str, got: &str, want: &str) {
        self.expect_eq(what, &Value::from(got), &Value::from(want));
    }

    fn expect_true(&mut self, what: &str, ok: bool) {
        self.checks += 1;
        if !ok {
            self.failures
                .push(format!("{what}: expected true, got false"));
        }
    }
}

fn golden_path() -> PathBuf {
    if let Ok(p) = std::env::var("DECIMA_GOLDEN") {
        return PathBuf::from(p);
    }
    // rust/decima-verify/src/main.rs → decima repo root is ../../..
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .join("../..")
        .join("heartbeat/protocol/reference_vectors.json")
}

fn load_json(path: &std::path::Path) -> Option<Value> {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("FATAL: cannot read {}: {e}", path.display());
            return None;
        }
    };
    match serde_json::from_str(&text) {
        Ok(v) => Some(v),
        Err(e) => {
            eprintln!("FATAL: cannot parse {}: {e}", path.display());
            None
        }
    }
}

fn extended_path() -> PathBuf {
    if let Ok(p) = std::env::var("DECIMA_GOLDEN_EXT") {
        return PathBuf::from(p);
    }
    // rust/decima-verify → rust/vectors/extended_vectors.json
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest.join("../vectors/extended_vectors.json")
}

fn extended_v3_path() -> PathBuf {
    if let Ok(p) = std::env::var("DECIMA_GOLDEN_EXT_V3") {
        return PathBuf::from(p);
    }
    // rust/decima-verify → rust/vectors/extended_vectors_v3.json
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest.join("../vectors/extended_vectors_v3.json")
}

fn main() -> ExitCode {
    let path = golden_path();
    let golden: Value = match load_json(&path) {
        Some(v) => v,
        None => return ExitCode::from(2),
    };
    let ext_path = extended_path();
    let extended: Value = match load_json(&ext_path) {
        Some(v) => v,
        None => return ExitCode::from(2),
    };
    let v3_path = extended_v3_path();
    let v3: Value = match load_json(&v3_path) {
        Some(v) => v,
        None => return ExitCode::from(2),
    };

    let sections: Vec<(&str, Report)> = vec![
        ("canonical", check_canonical(&golden)),
        ("blobs", check_blobs(&golden)),
        ("principals", check_principals(&golden)),
        ("signatures", check_signatures(&golden)),
        ("fold", check_fold(&golden)),
        ("extended", check_extended(&extended)),
        ("extended_v3", check_v3(&v3)),
    ];

    let mut total_fail = 0usize;
    let mut total_checks = 0usize;
    println!("decima-verify — {}", path.display());
    println!("extended vectors — {}", ext_path.display());
    println!(
        "golden profile: {}",
        golden["profile"].as_str().unwrap_or("?")
    );
    for (name, rep) in &sections {
        total_fail += rep.failures.len();
        total_checks += rep.checks;
        if rep.failures.is_empty() {
            println!("PASS  {name:<12} ({} checks)", rep.checks);
        } else {
            println!(
                "FAIL  {name:<12} ({} checks, {} failures)",
                rep.checks,
                rep.failures.len()
            );
            for f in &rep.failures {
                println!("  - {f}");
            }
        }
    }
    println!(
        "summary: {total_checks} checks, {total_fail} failures — {}",
        if total_fail == 0 { "MATCH" } else { "MISMATCH" }
    );
    if total_fail == 0 {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    }
}

/// canonical: re-derive canonical_hex, content_id_cell, content_id_event from
/// each payload.
fn check_canonical(golden: &Value) -> Report {
    let mut rep = Report::new();
    for (i, v) in golden["canonical"].as_array().unwrap().iter().enumerate() {
        let payload = &v["payload"];
        let got_hex = hex::encode(canonical(payload));
        rep.expect_str(
            &format!("canonical[{i}].canonical_hex"),
            &got_hex,
            v["canonical_hex"].as_str().unwrap(),
        );
        rep.expect_str(
            &format!("canonical[{i}].content_id_cell"),
            &content_id(payload, "cell"),
            v["content_id_cell"].as_str().unwrap(),
        );
        rep.expect_str(
            &format!("canonical[{i}].content_id_event"),
            &content_id(payload, "event"),
            v["content_id_event"].as_str().unwrap(),
        );
    }
    rep
}

/// blobs: re-derive blob_id from the raw bytes.
fn check_blobs(golden: &Value) -> Report {
    let mut rep = Report::new();
    for (i, v) in golden["blobs"].as_array().unwrap().iter().enumerate() {
        let data = hex::decode(v["data_hex"].as_str().unwrap()).unwrap();
        rep.expect_str(
            &format!("blobs[{i}].blob_id"),
            &blob_id(&data, "blob"),
            v["blob_id"].as_str().unwrap(),
        );
    }
    rep
}

/// principals: from the golden master seed, re-derive every named and keyed
/// pid + public key, and confirm keyed pids self-certify.
fn check_principals(golden: &Value) -> Report {
    let mut rep = Report::new();
    let p = &golden["principals"];
    let seed: [u8; 32] = hex::decode(p["master_seed_hex"].as_str().unwrap())
        .unwrap()
        .try_into()
        .unwrap();
    assert_eq!(
        seed, MASTER_SEED,
        "golden master seed must be the fixed all-zero seed"
    );
    let mut kr = Keyring::new(seed);

    for v in p["named"].as_array().unwrap() {
        let name = v["name"].as_str().unwrap();
        let kind = v["kind"].as_str().unwrap();
        let pr = kr.mint(name, kind);
        rep.expect_str(
            &format!("principals.named[{name}].pid"),
            &pr.id,
            v["pid"].as_str().unwrap(),
        );
        rep.expect_str(&format!("principals.named[{name}].kind"), &pr.kind, kind);
        rep.expect_str(
            &format!("principals.named[{name}].public_key"),
            &kr.public_key(&pr.id),
            v["public_key"].as_str().unwrap(),
        );
    }
    for v in p["keyed"].as_array().unwrap() {
        let name = v["name"].as_str().unwrap();
        let pr = kr.mint_keyed(name, "agent");
        rep.expect_str(
            &format!("principals.keyed[{name}].pid"),
            &pr.id,
            v["pid"].as_str().unwrap(),
        );
        let pub_hex = kr.public_key(&pr.id);
        rep.expect_str(
            &format!("principals.keyed[{name}].public_key"),
            &pub_hex,
            v["public_key"].as_str().unwrap(),
        );
        let raw: [u8; 32] = hex::decode(&pub_hex).unwrap().try_into().unwrap();
        rep.expect_true(
            &format!("principals.keyed[{name}].self_certifies"),
            Keyring::keyed_pid(&raw) == pr.id,
        );
        rep.expect_true(
            &format!("principals.keyed[{name}].golden_self_certifies"),
            v["self_certifies"].as_bool().unwrap(),
        );
    }
    rep
}

/// signatures: Ed25519 is deterministic, so re-signing the same message under
/// the same derived key must reproduce the golden signature; each must also
/// verify, and a one-byte tamper must fail closed.
fn check_signatures(golden: &Value) -> Report {
    let mut rep = Report::new();
    let seed: [u8; 32] = hex::decode(golden["principals"]["master_seed_hex"].as_str().unwrap())
        .unwrap()
        .try_into()
        .unwrap();
    let kr = Keyring::new(seed);
    for (i, v) in golden["signatures"].as_array().unwrap().iter().enumerate() {
        let pid = v["signer_pid"].as_str().unwrap();
        let msg = v["message"].as_str().unwrap();
        let want = v["sig"].as_str().unwrap();
        let got = kr.sign(pid, msg);
        rep.expect_str(&format!("signatures[{i}].sig"), &got, want);
        let ok = kr.verify(pid, msg, &got);
        rep.expect_true(&format!("signatures[{i}].verifies"), ok);
        rep.expect_true(
            &format!("signatures[{i}].golden_verifies"),
            v["verifies"].as_bool().unwrap(),
        );
        // One-byte tamper must fail closed.
        let mut tampered = hex::decode(&got).unwrap();
        tampered[0] ^= 0x01;
        rep.expect_true(
            &format!("signatures[{i}].tamper_fails_closed"),
            !kr.verify(pid, msg, &hex::encode(tampered)),
        );
    }
    rep
}

/// fold: re-run the exact reference event script from first principles and
/// compare every projection against the golden fold section.
fn check_fold(golden: &Value) -> Report {
    let mut rep = Report::new();
    let f = &golden["fold"];
    let seed: [u8; 32] = hex::decode(f["master_seed_hex"].as_str().unwrap())
        .unwrap()
        .try_into()
        .unwrap();
    let kr = Keyring::new(seed);
    let got = run_fold_script(&kr);

    rep.expect_str(
        "fold.author_pid",
        &got.author_pid,
        f["author_pid"].as_str().unwrap(),
    );
    rep.expect_str(
        "fold.type_cell_id",
        &got.type_cell_id,
        f["type_cell_id"].as_str().unwrap(),
    );
    rep.expect_str(
        "fold.parent_cap_id",
        &got.parent_cap_id,
        f["parent_cap_id"].as_str().unwrap(),
    );
    rep.expect_str(
        "fold.child_cap_id",
        &got.child_cap_id,
        f["child_cap_id"].as_str().unwrap(),
    );
    rep.expect_str(
        "fold.event_count",
        &got.event_count.to_string(),
        &f["event_count"].as_i64().unwrap().to_string(),
    );

    let want_events = f["events"].as_array().unwrap();
    rep.expect_str(
        "fold.events.len",
        &got.events.len().to_string(),
        &want_events.len().to_string(),
    );
    for (i, want) in want_events.iter().enumerate() {
        let g = got.events.get(i);
        rep.expect_true(&format!("fold.events[{i}].present"), g.is_some());
        if let Some(g) = g {
            for field in ["id", "verb", "lamport", "body"] {
                rep.expect_eq(
                    &format!("fold.events[{i}].{field}"),
                    &g[field],
                    &want[field],
                );
            }
        }
    }

    rep.expect_str(
        "fold.state_root",
        &got.state_root,
        f["state_root"].as_str().unwrap(),
    );
    let want_counts = f["type_counts"].as_object().unwrap();
    for (t, n) in &got.type_counts {
        let want_n = want_counts.get(t).map(|v| v.as_i64().unwrap() as usize);
        rep.expect_str(
            &format!("fold.type_counts.{t}"),
            &n.to_string(),
            &want_n.unwrap_or(usize::MAX).to_string(),
        );
    }
    rep.expect_str(
        "fold.type_counts.cardinality",
        &got.type_counts.len().to_string(),
        &want_counts.len().to_string(),
    );
    rep
}

/// extended (milestone 2): re-run the SQLite/INVOKE/ATTEST script from first
/// principles and re-derive EVERY value in rust/vectors/extended_vectors.json.
fn check_extended(golden: &Value) -> Report {
    let mut rep = Report::new();
    let seed: [u8; 32] = hex::decode(golden["master_seed_hex"].as_str().unwrap())
        .unwrap()
        .try_into()
        .unwrap();
    assert_eq!(seed, MASTER_SEED, "extended master seed must be all-zero");
    let kr = Keyring::new(seed);
    let got = run_extended_script(&kr);

    for (field, g) in [
        ("author_pid", &got.author_pid),
        ("attester_pid", &got.attester_pid),
        ("type_cell_id", &got.type_cell_id),
        ("parent_cap_id", &got.parent_cap_id),
        ("child_cap_id", &got.child_cap_id),
    ] {
        rep.expect_str(
            &format!("extended.{field}"),
            g,
            golden[field].as_str().unwrap(),
        );
    }

    // Events: id / verb / lamport / authorized / body, per event.
    let want_events = golden["events"].as_array().unwrap();
    rep.expect_str(
        "extended.events.len",
        &got.events.len().to_string(),
        &want_events.len().to_string(),
    );
    for (i, want) in want_events.iter().enumerate() {
        let g = got.events.get(i);
        rep.expect_true(&format!("extended.events[{i}].present"), g.is_some());
        if let Some(g) = g {
            for field in ["id", "verb", "lamport", "authorized", "body"] {
                rep.expect_eq(
                    &format!("extended.events[{i}].{field}"),
                    &g[field],
                    &want[field],
                );
            }
        }
    }

    // Stored payload bytes: re-derive from each event's full hashed payload.
    let want_stored = golden["stored_payloads"].as_array().unwrap();
    rep.expect_str(
        "extended.stored_payloads.len",
        &got.stored_payloads.len().to_string(),
        &want_stored.len().to_string(),
    );
    for (i, want) in want_stored.iter().enumerate() {
        let (seq, bytes) = &got.stored_payloads[i];
        rep.expect_str(
            &format!("extended.stored_payloads[{i}].seq"),
            &seq.to_string(),
            &want["seq"].as_i64().unwrap().to_string(),
        );
        rep.expect_str(
            &format!("extended.stored_payloads[{i}].payload"),
            bytes,
            want["payload"].as_str().unwrap(),
        );
        // Independently: python_dumps_sorted over the parsed golden payload
        // round-trips to the same bytes (canonical-shape independence).
        let parsed: Value = serde_json::from_str(want["payload"].as_str().unwrap()).unwrap();
        rep.expect_str(
            &format!("extended.stored_payloads[{i}].python_dumps"),
            &python_dumps_sorted(&parsed),
            want["payload"].as_str().unwrap(),
        );
    }

    // Log frontier + counts.
    rep.expect_str(
        "extended.head_after",
        &got.head_after,
        golden["head_after"].as_str().unwrap(),
    );
    rep.expect_str(
        "extended.lamport_after",
        &got.lamport_after.to_string(),
        &golden["lamport_after"].as_i64().unwrap().to_string(),
    );
    rep.expect_str(
        "extended.event_count",
        &got.event_count.to_string(),
        &golden["event_count"].as_i64().unwrap().to_string(),
    );

    // Folded INVOKEs and the per-capability tally.
    let want_inv = golden["invocations"].as_array().unwrap();
    rep.expect_str(
        "extended.invocations.len",
        &got.invocations.len().to_string(),
        &want_inv.len().to_string(),
    );
    for (i, want) in want_inv.iter().enumerate() {
        rep.expect_eq(
            &format!("extended.invocations[{i}]"),
            &got.invocations[i],
            want,
        );
    }
    let want_counts = golden["invoke_counts"].as_object().unwrap();
    rep.expect_str(
        "extended.invoke_counts.cardinality",
        &got.invoke_counts.len().to_string(),
        &want_counts.len().to_string(),
    );
    for (cap, n) in &got.invoke_counts {
        let want_n = want_counts.get(cap).and_then(Value::as_i64);
        rep.expect_str(
            &format!("extended.invoke_counts[{cap}]"),
            &n.to_string(),
            &want_n.map(|x| x.to_string()).unwrap_or_default(),
        );
    }

    // Folded attestations (cell id -> [{by, claim, event}]).
    let want_att = golden["attestations"].as_object().unwrap();
    rep.expect_str(
        "extended.attestations.cardinality",
        &got.attestations.len().to_string(),
        &want_att.len().to_string(),
    );
    for (cid, want_list) in want_att {
        let got_list = got.attestations.get(cid);
        rep.expect_true(
            &format!("extended.attestations[{cid}].present"),
            got_list.is_some(),
        );
        if let Some(got_list) = got_list {
            rep.expect_eq(
                &format!("extended.attestations[{cid}]"),
                &Value::from(got_list.clone()),
                want_list,
            );
        }
    }

    // state_root + warm-start equality.
    rep.expect_str(
        "extended.state_root",
        &got.state_root,
        golden["state_root"].as_str().unwrap(),
    );
    rep.expect_str(
        "extended.warm_head",
        &got.warm_head,
        golden["warm_head"].as_str().unwrap(),
    );
    rep.expect_str(
        "extended.warm_lamport",
        &got.warm_lamport.to_string(),
        &golden["warm_lamport"].as_i64().unwrap().to_string(),
    );
    rep.expect_str(
        "extended.warm_state_root",
        &got.warm_state_root,
        golden["warm_state_root"].as_str().unwrap(),
    );
    rep.expect_true(
        "extended.warm_equals_first",
        got.warm_state_root == got.state_root && golden["warm_equals_first"].as_bool().unwrap(),
    );
    rep
}

/// extended_v3 (milestone 3): re-run the adjudication / trusted-promotion /
/// EffectReceipt / lease-expiry script from first principles and re-derive
/// EVERY value in rust/vectors/extended_vectors_v3.json.
fn check_v3(golden: &Value) -> Report {
    let mut rep = Report::new();
    let seed: [u8; 32] = hex::decode(golden["master_seed_hex"].as_str().unwrap())
        .unwrap()
        .try_into()
        .unwrap();
    assert_eq!(seed, MASTER_SEED, "v3 master seed must be all-zero");
    let kr = Keyring::new(seed);
    let got = run_v3_script(&kr);

    // Principals + pinned cell/event ids.
    let want_p = &golden["principals"];
    for (field, g, w) in [
        ("root", &got.root_pid, &want_p["root"]),
        ("tester", &got.tester_pid, &want_p["tester"]),
        ("attester", &got.attester_pid, &want_p["attester"]),
        ("reckoner", &got.reckoner_pid, &want_p["reckoner"]),
        ("impostor", &got.impostor_pid, &want_p["impostor"]),
        ("attacker", &got.attacker_pid, &want_p["attacker"]),
    ] {
        rep.expect_str(&format!("v3.principals.{field}"), g, w.as_str().unwrap());
    }
    for (field, g) in [
        ("genesis_event_id", &got.genesis_event_id),
        ("attacker_genesis_event_id", &got.attacker_genesis_event_id),
        ("type_headline_id", &got.type_headline_id),
        ("belief_cell", &got.belief_cell),
        ("promoter_cell_id", &got.promoter_cell_id),
        ("forged_promoter_cell_id", &got.forged_promoter_cell_id),
        ("attacker_promoter_cell_id", &got.attacker_promoter_cell_id),
        ("cap_pure_id", &got.cap_pure_id),
        ("cap_fin_id", &got.cap_fin_id),
        ("wallet_id", &got.wallet_id),
        ("subwallet_id", &got.subwallet_id),
        ("wallet_view_id", &got.wallet_view_id),
        ("card_id", &got.card_id),
        ("receipt_key", &got.receipt_key),
        ("receipt_unknown_id", &got.receipt_unknown_id),
        ("receipt_definite_id", &got.receipt_definite_id),
        ("receipt_key_unknown_only", &got.receipt_key_unknown_only),
        ("receipt_unknown_only_id", &got.receipt_unknown_only_id),
        ("genesis_author", &got.genesis_author),
    ] {
        rep.expect_str(&format!("v3.{field}"), g, golden[field].as_str().unwrap());
    }
    rep.expect_str(
        "v3.grinding_attempts",
        &got.grinding_attempts.to_string(),
        &golden["grinding_attempts"].as_i64().unwrap().to_string(),
    );
    rep.expect_str(
        "v3.expires_at",
        &got.expires_at.to_string(),
        &golden["expires_at"].as_i64().unwrap().to_string(),
    );

    // Events: id / verb / lamport / authorized / body, per recorded event.
    let want_events = golden["events"].as_array().unwrap();
    rep.expect_str(
        "v3.events.len",
        &got.events.len().to_string(),
        &want_events.len().to_string(),
    );
    for (i, want) in want_events.iter().enumerate() {
        let g = got.events.get(i);
        rep.expect_true(&format!("v3.events[{i}].present"), g.is_some());
        if let Some(g) = g {
            for field in ["id", "verb", "lamport", "authorized", "body"] {
                rep.expect_eq(&format!("v3.events[{i}].{field}"), &g[field], &want[field]);
            }
        }
    }

    // Folded feature projections.
    rep.expect_eq("v3.mv_before", &got.mv_before, &golden["mv_before"]);
    rep.expect_eq("v3.mv_after", &got.mv_after, &golden["mv_after"]);
    rep.expect_eq("v3.promotion", &got.promotion, &golden["promotion"]);
    rep.expect_eq(
        "v3.anti_grinding",
        &got.anti_grinding,
        &golden["anti_grinding"],
    );
    rep.expect_eq("v3.receipts", &got.receipts, &golden["receipts"]);
    rep.expect_eq("v3.leases", &got.leases, &golden["leases"]);

    // Log frontier + final root.
    rep.expect_str(
        "v3.head_after",
        &got.head_after,
        golden["head_after"].as_str().unwrap(),
    );
    rep.expect_str(
        "v3.lamport_after",
        &got.lamport_after.to_string(),
        &golden["lamport_after"].as_i64().unwrap().to_string(),
    );
    rep.expect_str(
        "v3.event_count",
        &got.event_count.to_string(),
        &golden["event_count"].as_i64().unwrap().to_string(),
    );
    rep.expect_str(
        "v3.state_root",
        &got.state_root,
        golden["state_root"].as_str().unwrap(),
    );
    rep
}
