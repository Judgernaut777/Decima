//! Milestone-2 conformance tests (verifier v2 criteria 3 & 5): the
//! SQLite-persisted Weft, its stored-bytes format, on-read verification
//! (fail closed on tamper / forged signature), warm start, and the
//! INVOKE/ATTEST fold subsets. Golden ids/roots are pinned from
//! rust/vectors/extended_vectors.json (generated FROM the reference by
//! rust/vectors/generate.py); decima-verify re-derives the whole file.

use decima_core::crypto::Keyring;
use decima_core::hashing::python_dumps_sorted;
use decima_core::reference::MASTER_SEED;
use decima_core::reference_ext::run_extended_script;
use decima_core::weave::Weave;
use decima_core::weft::{WeftError, ASSERT, ATTEST, INVOKE};
use decima_core::weft_db::WeftDb;
use serde_json::{json, Value};

fn temp_db(tag: &str) -> std::path::PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    std::env::temp_dir().join(format!(
        "decima-test-{}-{}-{}.db",
        tag,
        std::process::id(),
        n
    ))
}

/// The extended script, minimal form: capability grant + two INVOKEs through
/// it + ATTESTs (one by a second principal) — enough to exercise the fold.
fn append_mini_script(weft: &mut WeftDb, author: &str, other: &str) -> (String, Vec<String>) {
    let cap_id = decima_core::hashing::content_id(&json!({"cap": "pay", "v": 1}), "cell");
    let mut ids = Vec::new();
    ids.push(
        weft.append(
            author,
            ASSERT,
            json!({"cell": "note:1", "type": "note", "kind": "CONTENT",
                   "content": {"text": "hi", "n": 1}}),
            None,
        )
        .unwrap()
        .id,
    );
    let inv1 = weft
        .append(
            author,
            INVOKE,
            json!({"cap": cap_id, "args": {"amount": 1}, "nonce": "n1"}),
            Some(&cap_id),
        )
        .unwrap();
    let inv2 = weft
        .append(
            author,
            INVOKE,
            json!({"cap": cap_id, "args": {"amount": 2}, "nonce": "n2"}),
            Some(&cap_id),
        )
        .unwrap();
    let att = weft
        .append(
            other,
            ATTEST,
            json!({"target_cell": "note:1", "claim": "witnessed"}),
            None,
        )
        .unwrap();
    ids.extend([inv1.id, inv2.id, att.id]);
    (cap_id, ids)
}

#[test]
fn python_dumps_matches_cpython_byte_for_byte() {
    // Pinned against CPython 3.x json.dumps(., sort_keys=True) output.
    let cases: Vec<(Value, &str)> = vec![
        (
            json!({"s": "café 王 😀"}),
            "{\"s\": \"caf\\u00e9 \\u738b \\ud83d\\ude00\"}",
        ),
        (
            json!({"s": "a\"b\\c\nd\te\rf\u{8}g\u{c}h"}),
            "{\"s\": \"a\\\"b\\\\c\\nd\\te\\rf\\bg\\fh\"}",
        ),
        (
            json!({"s": "\u{0}\u{1f}\u{7f}"}),
            "{\"s\": \"\\u0000\\u001f\\u007f\"}",
        ),
        (
            json!({"a": [1, true, false, null, {"x": []}]}),
            "{\"a\": [1, true, false, null, {\"x\": []}]}",
        ),
        (
            json!({"accent": "é", "emoji": "😀"}),
            "{\"accent\": \"\\u00e9\", \"emoji\": \"\\ud83d\\ude00\"}",
        ),
    ];
    for (v, want) in cases {
        assert_eq!(python_dumps_sorted(&v), want);
    }
    let big: Value =
        serde_json::from_str("{\"big\":123456789012345678901234567890,\"neg\":-5}").unwrap();
    assert_eq!(
        python_dumps_sorted(&big),
        "{\"big\": 123456789012345678901234567890, \"neg\": -5}"
    );
}

#[test]
fn weft_db_stores_reference_payload_bytes() {
    // The stored TEXT is json.dumps(., sort_keys=True) with DEFAULT
    // separators and ensure_ascii — spaced and ASCII-escaped, NOT the
    // compact canonical bytes the id hashes.
    let mut kr = Keyring::new(MASTER_SEED);
    let author = kr.mint("tester", "human").id;
    let db = temp_db("bytes");
    let mut weft = WeftDb::open(&db, &kr).unwrap();
    weft.append(
        &author,
        ASSERT,
        json!({"cell": "c1", "content": {"t": "café 😀"}}),
        None,
    )
    .unwrap();
    let rows = weft.stored_payloads().unwrap();
    assert_eq!(rows.len(), 1);
    let stored = &rows[0].1;
    assert!(
        stored.contains("\": \""),
        "default key separator, got {stored}"
    );
    assert!(stored.contains(", \"verb\""), "default item separator");
    assert!(stored.contains("\\u00e9"), "ensure_ascii escapes non-ASCII");
    assert!(
        stored.contains("\\ud83d\\ude00"),
        "astral as surrogate pair"
    );
    // The golden extended vectors pin the exact bytes for the full script.
    let got = run_extended_script(&kr);
    assert_eq!(got.stored_payloads.len(), 10);
    assert!(got.stored_payloads.windows(2).all(|w| w[0].0 < w[1].0));
    let _ = std::fs::remove_file(&db);
}

#[test]
fn weft_db_warm_start_recovers_head_lamport_and_root() {
    let mut kr = Keyring::new(MASTER_SEED);
    let author = kr.mint("tester", "human").id;
    let other = kr.mint("attester", "agent").id;
    let db = temp_db("warm");
    let (first_root, head, lamport, count);
    {
        let mut weft = WeftDb::open(&db, &kr).unwrap();
        append_mini_script(&mut weft, &author, &other);
        head = weft.head().unwrap().to_string();
        lamport = weft.lamport();
        count = weft.count().unwrap();
        let evs = weft.events().unwrap();
        first_root = Weave::fold_events(evs.iter()).state_root();
    }
    // Reopen the same file: _load_head semantics + identical fold.
    let warm = WeftDb::open(&db, &kr).unwrap();
    assert_eq!(warm.head().unwrap(), head);
    assert_eq!(warm.lamport(), lamport);
    assert_eq!(warm.count().unwrap(), count);
    let evs = warm.events().unwrap();
    assert_eq!(Weave::fold_events(evs.iter()).state_root(), first_root);
    // And appends CONTINUE the chain after a warm start.
    let mut warm = warm;
    let ev = warm
        .append(
            &author,
            ATTEST,
            json!({"target_cell": "note:1", "claim": "later"}),
            None,
        )
        .unwrap();
    assert_eq!(ev.lamport, lamport + 1);
    assert_eq!(ev.parents, vec![head]);
    let _ = std::fs::remove_file(&db);
}

#[test]
fn weft_db_tampered_stored_payload_fails_closed() {
    let mut kr = Keyring::new(MASTER_SEED);
    let author = kr.mint("tester", "human").id;
    let db = temp_db("tamper");
    let mut weft = WeftDb::open(&db, &kr).unwrap();
    weft.append(
        &author,
        ASSERT,
        json!({"cell": "c1", "content": {"x": 1}}),
        None,
    )
    .unwrap();
    weft.append(
        &author,
        ASSERT,
        json!({"cell": "c2", "content": {"x": 2}}),
        None,
    )
    .unwrap();
    // Edit the FIRST row's payload bytes in place (a flipped content value).
    weft.tamper_row(
        "UPDATE events SET payload = replace(payload, '\"x\": 1', '\"x\": 999') WHERE seq = 1",
    )
    .unwrap();
    let err = weft.events().unwrap_err();
    match err {
        WeftError::ContentTampered { seq } => assert_eq!(seq, 1),
        other => panic!("expected ContentTampered, got {other}"),
    }
    let _ = std::fs::remove_file(&db);
}

#[test]
fn weft_db_forged_signature_fails_closed() {
    let mut kr = Keyring::new(MASTER_SEED);
    let author = kr.mint("tester", "human").id;
    let intruder = kr.mint("intruder", "agent").id;
    let db = temp_db("forgery");
    let mut weft = WeftDb::open(&db, &kr).unwrap();
    weft.append(&author, ASSERT, json!({"cell": "c1"}), None)
        .unwrap();
    // Swap in a signature by ANOTHER principal (forgery: honest id, wrong key).
    let head_eid = weft.head().unwrap();
    let forged = kr.sign(&intruder, head_eid);
    weft.tamper_row(&format!("UPDATE events SET sig = '{forged}' WHERE seq = 1"))
        .unwrap();
    let err = weft.events().unwrap_err();
    match err {
        WeftError::BadSignature { seq } => assert_eq!(seq, 1),
        other => panic!("expected BadSignature, got {other}"),
    }
    // A corrupt sig encoding fails closed the same way (never panics).
    weft.tamper_row("UPDATE events SET sig = 'zz' WHERE seq = 1")
        .unwrap();
    assert!(matches!(
        weft.events(),
        Err(WeftError::BadSignature { seq: 1 })
    ));
    let _ = std::fs::remove_file(&db);
}

#[test]
fn fold_invoke_tally_and_invocations() {
    let mut kr = Keyring::new(MASTER_SEED);
    let author = kr.mint("tester", "human").id;
    let other = kr.mint("attester", "agent").id;
    let db = temp_db("invoke");
    let mut weft = WeftDb::open(&db, &kr).unwrap();
    let (cap_id, ids) = append_mini_script(&mut weft, &author, &other);
    let evs = weft.events().unwrap();
    let w = Weave::fold_events(evs.iter());
    // weave.py: one Invocation per INVOKE, per-capability tally.
    assert_eq!(w.invocations.len(), 2);
    assert_eq!(w.invocations[0].cap, cap_id);
    assert_eq!(w.invocations[0].by, author);
    assert_eq!(w.invocations[0].event, ids[1]);
    assert_eq!(w.invocations[0].args, json!({"amount": 1}));
    assert_eq!(w.invocations[1].args, json!({"amount": 2}));
    assert_eq!(w.invoke_counts.get(&cap_id), Some(&2));
    let _ = std::fs::remove_file(&db);
}

#[test]
fn fold_attestations_including_other_signer() {
    let mut kr = Keyring::new(MASTER_SEED);
    let author = kr.mint("tester", "human").id;
    let other = kr.mint("attester", "agent").id;
    let db = temp_db("attest");
    let mut weft = WeftDb::open(&db, &kr).unwrap();
    let (_, ids) = append_mini_script(&mut weft, &author, &other);
    let evs = weft.events().unwrap();
    let w = Weave::fold_events(evs.iter());
    // The ATTEST folds onto the TARGET cell as {by, claim, event}; an
    // attestation from a DIFFERENT signer is recorded identically (the fold
    // records evidence; it never gates on who signed — weave.py ATTEST).
    let note = w.cells.get("note:1").unwrap();
    assert_eq!(
        note.attestations,
        vec![json!({"by": other, "claim": "witnessed", "event": ids[3]})]
    );
    // Attestations feed state_root records: an attested cell's root differs
    // from the same fold without it.
    let mut weft2 = WeftDb::open(&temp_db("attest-none"), &kr).unwrap();
    weft2
        .append(
            &author,
            ASSERT,
            json!({"cell": "note:1", "type": "note", "kind": "CONTENT",
                                        "content": {"text": "hi", "n": 1}}),
            None,
        )
        .unwrap();
    let evs2 = weft2.events().unwrap();
    let mut w_with = Weave::fold_events(evs.iter());
    let mut w_without = Weave::fold_events(evs2.iter());
    assert_ne!(w_with.state_root(), w_without.state_root());
}

#[test]
fn extended_script_matches_golden_pins() {
    // The headline pins from rust/vectors/extended_vectors.json.
    let kr = Keyring::new(MASTER_SEED);
    let got = run_extended_script(&kr);
    assert_eq!(got.author_pid, "41d7e96ee5bf4f00");
    assert_eq!(got.attester_pid, "801b40c1f6cf0883");
    assert_eq!(got.type_cell_id, "c361b55423537561ed15719dd332bd04");
    assert_eq!(got.parent_cap_id, "ae229001e377332b02486473150de3ba");
    assert_eq!(got.child_cap_id, "3b09e6006418c73b57c838906f31a827");
    assert_eq!(got.event_count, 10);
    assert_eq!(got.events.len(), 9); // define_type stays on the log, unrecorded
    assert_eq!(got.lamport_after, 10);
    assert_eq!(got.invoke_counts, vec![(got.child_cap_id.clone(), 2)]);
    assert_eq!(got.invocations.len(), 2);
    assert_eq!(got.attestations.len(), 2);
    assert_eq!(got.state_root, "a4a0538cadc93cb524abaab8b962a642");
    assert_eq!(got.warm_state_root, got.state_root);
    assert_eq!(got.warm_head, got.head_after);
    assert_eq!(got.warm_lamport, got.lamport_after);
}
