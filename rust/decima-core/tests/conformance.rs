//! Per-section conformance unit tests (verifier v1 criterion 3). The golden
//! literals here are pinned from heartbeat/protocol/reference_vectors.json;
//! decima-verify additionally re-derives EVERY value in that file at runtime.

use decima_core::crypto::Keyring;
use decima_core::hashing::{blob_id, canonical, content_id, nfc_deep};
use decima_core::model;
use decima_core::reference::{run_fold_script, MASTER_SEED};
use decima_core::weave::Weave;
use decima_core::weft::{Weft, ASSERT, RETRACT};
use serde_json::{json, Value};

#[test]
fn canonical_sorted_keys_compact_utf8() {
    // Key-order invariance: {"a":1,"b":2} == {"b":2,"a":1}.
    let a = json!({"a": 1, "b": 2});
    let b = json!({"b": 2, "a": 1});
    let want = b"{\"a\":1,\"b\":2}";
    assert_eq!(canonical(&a), want);
    assert_eq!(canonical(&b), want);
    assert_eq!(content_id(&a, "cell"), "7bccaa7b9f27fa82912157651b81b719");
    assert_eq!(content_id(&a, "event"), "c9f15e91ec42d3c9815817db436967be");
    // Raw UTF-8 (ensure_ascii=False), nothing else escaped.
    let u = json!({"unicode": "café 王 😀"});
    assert_eq!(
        String::from_utf8(canonical(&u)).unwrap(),
        "{\"unicode\":\"café 王 😀\"}"
    );
    assert_eq!(content_id(&u, "cell"), "5bd459eede416c8c875731899c5c224d");
}

#[test]
fn nfc_collapse_combining_equals_precomposed() {
    // é precomposed (U+00E9) and e + U+0301 are ONE identity.
    let pre = json!({"accent": "\u{00e9}", "note": "nfc"});
    let comb = json!({"accent": "e\u{0301}", "note": "nfc"});
    assert_eq!(nfc_deep(&pre), nfc_deep(&comb));
    assert_eq!(canonical(&pre), canonical(&comb));
    assert_eq!(content_id(&pre, "cell"), content_id(&comb, "cell"));
    assert_eq!(content_id(&pre, "cell"), "31bc06afa9ff47f7b863cad8a129befa");
    // Deep: keys and nested values normalize too.
    let deep = json!({"e\u{0301}": ["a\u{0301}", {"y\u{0301}": 1}]});
    let deep_n = json!({"\u{00e9}": ["\u{00e1}", {"\u{00fd}": 1}]});
    assert_eq!(nfc_deep(&deep), nfc_deep(&deep_n));
}

#[test]
fn big_int_round_trips_as_bare_number() {
    // Arbitrary precision: 123456789012345678901234567890 survives.
    let payload: Value =
        serde_json::from_str("{\"big_int\": 123456789012345678901234567890}").unwrap();
    assert_eq!(
        String::from_utf8(canonical(&payload)).unwrap(),
        "{\"big_int\":123456789012345678901234567890}"
    );
    assert_eq!(
        content_id(&payload, "cell"),
        "cee15588d82e8264283509ce6e11e3d6"
    );
    assert_eq!(
        content_id(&payload, "event"),
        "28ffd8d7bba20572415ce3a57e27d5f3"
    );
}

#[test]
fn empty_containers_and_negative_ints() {
    let payload = json!({"empty_map": {}, "empty_list": [], "zero": 0, "neg": -5});
    assert_eq!(
        String::from_utf8(canonical(&payload)).unwrap(),
        "{\"empty_list\":[],\"empty_map\":{},\"neg\":-5,\"zero\":0}"
    );
    assert_eq!(
        content_id(&payload, "cell"),
        "8fbc4fe1c1d197754039b1ce14d611e2"
    );
}

#[test]
fn blob_ids_domain_separated() {
    assert_eq!(blob_id(b"", "blob"), "51abf558cde727a647f2cf3675451223");
    assert_eq!(
        blob_id(b"hello, fates", "blob"),
        "b618ebeee57355838d47c73b3d4de923"
    );
    let full: Vec<u8> = (0..=255u8).collect();
    assert_eq!(blob_id(&full, "blob"), "a858a69d6486e287285e0348eb135571");
    // Cell/event id spaces are disjoint from blob space for the same bytes.
    assert_ne!(
        blob_id(b"hello, fates", "blob"),
        blob_id(b"hello, fates", "cell")
    );
}

#[test]
fn principals_named_and_keyed() {
    let mut kr = Keyring::new(MASTER_SEED);
    let root = kr.mint("root", "root");
    assert_eq!(root.id, "17b307c541c97c1e");
    assert_eq!(
        kr.public_key(&root.id),
        "4fe236992352e3337a30653e333a714ca82507deac05db86653df630bc081902"
    );
    let nona = kr.mint("nona", "reckoner");
    assert_eq!(nona.id, "55d2e032978d188e");
    assert_eq!(
        kr.public_key(&nona.id),
        "a5be21999dad413063f54ec559f36ee2fb5fee1d79e228d16eae17700612f00f"
    );
    let peer_a = kr.mint_keyed("peer-a", "agent");
    assert_eq!(peer_a.id, "ff0b011e02fa20a7");
    let pub_a = kr.public_key(&peer_a.id);
    assert_eq!(
        pub_a,
        "6938d900f528940f2b8cf6dbfca1ad266d7bc45f82520fc38ec1ceecb56f0a10"
    );
    // Self-certifying: pid == blake2b(public_key).
    let raw: [u8; 32] = hex::decode(&pub_a).unwrap().try_into().unwrap();
    assert_eq!(Keyring::keyed_pid(&raw), peer_a.id);
    let peer_b = kr.mint_keyed("peer-b", "agent");
    assert_eq!(peer_b.id, "cbbe51e4eb419a1a");
}

#[test]
fn signatures_are_golden_and_tamper_fails_closed() {
    let kr = Keyring::new(MASTER_SEED);
    let root = "17b307c541c97c1e";
    // Ed25519 is deterministic → the same (seed, message) yields the golden sig.
    let sig = kr.sign(root, "the fates do not negotiate");
    assert_eq!(
        sig,
        "d13e419d0c0c5a283a7b965efbeb6b8533728523dd78d8e04718f9f5486d21ec3b2a20cb8bfdbf2ec6a7c8c2ea50b08ad6eb10b6351e8c4f59b366d1d51e6c05"
    );
    assert!(kr.verify(root, "the fates do not negotiate", &sig));
    let empty = kr.sign(root, "");
    assert_eq!(
        empty,
        "3f2437a66096d12a2e81a60f45643bf3e8dbd9f55266e4058c10fedcb4e9b7856988b5fc512ad2b3c5a3a378cb250b2f9ad25eaa8350be1a649ba9c6fa1a5e0d"
    );
    // One-byte tamper of the signature fails closed.
    let mut bad = hex::decode(&sig).unwrap();
    bad[0] ^= 0x01;
    assert!(!kr.verify(root, "the fates do not negotiate", &hex::encode(bad)));
    // One-byte tamper of the message fails closed too.
    assert!(!kr.verify(root, "the fates do not negotiatf", &sig));
    // Non-ASCII messages round-trip through UTF-8.
    let uni = kr.sign(root, "café 😀");
    assert_eq!(
        uni,
        "273d3305417645eb61f94eb048a1bb56609c0d55b7a31448e601af1695aa8197c5a12e40df638b038db9d90719d9b95784fba26ab367d89b2a8b7306b92ea40f"
    );
}

#[test]
fn weft_append_linear_semantics() {
    let mut kr = Keyring::new(MASTER_SEED);
    let author = kr.mint("tester", "human").id;
    let mut weft = Weft::new(&kr);
    let e1 = weft
        .append(&author, ASSERT, json!({"cell": "c1"}), None)
        .unwrap();
    assert_eq!(e1.lamport, 1);
    assert_eq!(e1.parents, Vec::<String>::new());
    let e2 = weft
        .append(&author, ASSERT, json!({"cell": "c2"}), None)
        .unwrap();
    assert_eq!(e2.lamport, 2);
    assert_eq!(e2.parents, vec![e1.id.clone()]);
    // The author signed the eid STRING's UTF-8 bytes.
    assert!(kr.verify(&author, &e1.id, &e1.sig));
    // Unknown verbs are refused.
    assert!(weft.append(&author, "FROBNICATE", json!({}), None).is_err());
    // Tampered event id fails closed: the recomputed id of a mutated payload
    // differs, and the original signature does not verify against it.
    let tampered_payload = json!({
        "parents": e1.parents, "author": e1.author, "authorized": e1.authorized,
        "verb": e1.verb, "body": {"cell": "EVIL"}, "lamport": e1.lamport,
    });
    let tampered_eid = content_id(&tampered_payload, "event");
    assert_ne!(tampered_eid, e1.id);
    assert!(!kr.verify(&author, &tampered_eid, &e1.sig));
}

#[test]
fn fold_reference_script_matches_golden_root() {
    let kr = Keyring::new(MASTER_SEED);
    let got = run_fold_script(&kr);
    assert_eq!(got.author_pid, "41d7e96ee5bf4f00");
    assert_eq!(got.type_cell_id, "c361b55423537561ed15719dd332bd04");
    assert_eq!(got.parent_cap_id, "ae229001e377332b02486473150de3ba");
    assert_eq!(got.child_cap_id, "3b09e6006418c73b57c838906f31a827");
    assert_eq!(got.state_root, "28aacd8ab21e83790eb81563b5de4e26");
    assert_eq!(got.event_count, 7);
    assert_eq!(
        got.type_counts,
        vec![
            ("note".to_string(), 0),
            ("type".to_string(), 1),
            ("capability".to_string(), 2),
        ]
    );
    let ids: Vec<&str> = got
        .events
        .iter()
        .map(|e| e["id"].as_str().unwrap())
        .collect();
    assert_eq!(
        ids,
        vec![
            "ef607f7e85e53e66dacc0f107d5d6d50",
            "82d38561dcde3300ddbb8171076b2982",
            "3dc5234825a599208f1c034d49001233",
            "fa3c151a6f86a5b200f34df377ad6848",
            "3b9e69838e52b87465cfefb19229d111",
            "b28aa5af37643d34f103189eb3567f85",
        ]
    );
}

#[test]
fn fold_is_order_independent_and_idempotent() {
    // Applying the same events in (lamport, id) order twice — or applying a
    // duplicate — yields the same projection root (FOLD §2 / §11.3).
    let mut kr = Keyring::new(MASTER_SEED);
    let author = kr.mint("tester", "human").id;
    let mut weft = Weft::new(&kr);
    model::define_type(&mut weft, &author, "note", None, None).unwrap();
    model::assert_content(
        &mut weft,
        &author,
        "note:1",
        "note",
        json!({"text": "first", "n": 1}),
    )
    .unwrap();
    model::assert_content(
        &mut weft,
        &author,
        "note:1",
        "note",
        json!({"text": "edited", "n": 2}),
    )
    .unwrap();
    weft.append(
        &author,
        RETRACT,
        json!({"cell": "note:1", "mode": "WITHDRAW"}),
        None,
    )
    .unwrap();

    let mut w1 = Weave::fold(&weft);
    let r1 = w1.state_root();
    let mut w2 = Weave::fold(&weft);
    let r2 = w2.state_root();
    assert_eq!(r1, r2);

    // Duplicate delivery is a no-op.
    let mut w3 = Weave::new();
    for ev in weft.events() {
        w3.apply(ev);
        w3.apply(ev); // duplicate
    }
    assert_eq!(w3.state_root(), r1);

    // WITHDRAW tombstones note:1 (type_counts: note == 0) and the LWW winner
    // is the highest-lamport content version.
    let note = w1.cells.get("note:1").unwrap();
    assert!(note.retracted);
    assert_eq!(note.content, json!({"text": "edited", "n": 2}));
    assert_eq!(note.version, 2);
    assert_eq!(note.content_heads, vec![json!({"text": "edited", "n": 2})]);
}
