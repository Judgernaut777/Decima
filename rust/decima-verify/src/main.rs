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
use decima_core::hashing::{blob_id, canonical, content_id};
use decima_core::reference::{run_fold_script, MASTER_SEED};
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
            self.failures.push(format!("{what}: expected true, got false"));
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

fn main() -> ExitCode {
    let path = golden_path();
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("FATAL: cannot read {}: {e}", path.display());
            return ExitCode::from(2);
        }
    };
    let golden: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("FATAL: cannot parse {}: {e}", path.display());
            return ExitCode::from(2);
        }
    };

    let mut sections: Vec<(&str, Report)> = Vec::new();
    sections.push(("canonical", check_canonical(&golden)));
    sections.push(("blobs", check_blobs(&golden)));
    sections.push(("principals", check_principals(&golden)));
    sections.push(("signatures", check_signatures(&golden)));
    sections.push(("fold", check_fold(&golden)));

    let mut total_fail = 0usize;
    let mut total_checks = 0usize;
    println!("decima-verify — {}", path.display());
    println!("golden profile: {}", golden["profile"].as_str().unwrap_or("?"));
    for (name, rep) in &sections {
        total_fail += rep.failures.len();
        total_checks += rep.checks;
        if rep.failures.is_empty() {
            println!("PASS  {name:<12} ({} checks)", rep.checks);
        } else {
            println!("FAIL  {name:<12} ({} checks, {} failures)", rep.checks, rep.failures.len());
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
    assert_eq!(seed, MASTER_SEED, "golden master seed must be the fixed all-zero seed");
    let mut kr = Keyring::new(seed);

    for v in p["named"].as_array().unwrap() {
        let name = v["name"].as_str().unwrap();
        let kind = v["kind"].as_str().unwrap();
        let pr = kr.mint(name, kind);
        rep.expect_str(&format!("principals.named[{name}].pid"), &pr.id, v["pid"].as_str().unwrap());
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
        rep.expect_str(&format!("principals.keyed[{name}].pid"), &pr.id, v["pid"].as_str().unwrap());
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

    rep.expect_str("fold.author_pid", &got.author_pid, f["author_pid"].as_str().unwrap());
    rep.expect_str("fold.type_cell_id", &got.type_cell_id, f["type_cell_id"].as_str().unwrap());
    rep.expect_str("fold.parent_cap_id", &got.parent_cap_id, f["parent_cap_id"].as_str().unwrap());
    rep.expect_str("fold.child_cap_id", &got.child_cap_id, f["child_cap_id"].as_str().unwrap());
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

    rep.expect_str("fold.state_root", &got.state_root, f["state_root"].as_str().unwrap());
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
