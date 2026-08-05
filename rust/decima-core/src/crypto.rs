//! Principals and signing — real Ed25519 (RFC 8032, deterministic → golden).
//!
//! Port of heartbeat/decima/crypto.py + keystore.py (DerivedKeyStore only —
//! the default custodian; DirectoryKeyStore is an alternative custody backend
//! not needed for conformance). Every principal's Ed25519 keypair is DERIVED
//! deterministically from one 32-byte master seed + the principal id, domain-
//! separated by the BLAKE2b personalization string, so identities and
//! signatures are stable across runs.

use blake2::digest::consts::U8;
use blake2::digest::core_api::{Buffer, UpdateCore, VariableOutputCore};
use blake2::{Blake2b, Blake2bVarCore, Digest};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};

const PERSON_ED255: &[u8] = b"decima:ed255";
const PERSON_KEYID: &[u8] = b"decima:keyid";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Principal {
    /// Public, stable identifier (named mint: blake2b-8 of the name;
    /// keyed mint: blake2b-8 of the public key — self-certifying).
    pub id: String,
    /// Human-facing label.
    pub name: String,
    /// "root" | "human" | "agent" | "executor" | "reckoner"
    pub kind: String,
}

fn blake2b_8(data: &[u8]) -> String {
    let mut h = Blake2b::<U8>::new();
    h.update(data);
    hex::encode(h.finalize())
}

/// BLAKE2b with a personalization string and no key — exactly
/// `hashlib.blake2b(data, digest_size=32, person=person)`. Built on the var
/// core directly (the blake2 crate's MAC types would inject a key block even
/// for an empty key, which hashlib does NOT do).
fn blake2b_personal_32(data: &[u8], person: &[u8]) -> [u8; 32] {
    let mut core = Blake2bVarCore::new_with_params(&[], person, 0, 32);
    let mut buffer = Buffer::<Blake2bVarCore>::default();
    buffer.digest_blocks(data, |blocks| core.update_blocks(blocks));
    let mut full = blake2::digest::Output::<Blake2bVarCore>::default();
    core.finalize_variable_core(&mut buffer, &mut full);
    let mut out = [0u8; 32];
    out.copy_from_slice(&full[..32]); // TRUNC_SIDE is Left
    out
}

/// One master seed; each principal's keypair is derived from it + its id
/// (DerivedKeyStore semantics), with self-certifying keyed principals adopted
/// under their key-derived pid.
pub struct Keyring {
    pub master: [u8; 32],
    principals: std::collections::HashMap<String, Principal>,
    /// Adopted seeds (mint_keyed path) overriding derivation for their pid.
    adopted: std::collections::HashMap<String, [u8; 32]>,
}

impl Keyring {
    pub fn new(master: [u8; 32]) -> Self {
        Keyring {
            master,
            principals: std::collections::HashMap::new(),
            adopted: std::collections::HashMap::new(),
        }
    }

    /// pid = blake2b(name, digest_size=8) hex (crypto.Keyring.mint).
    pub fn mint(&mut self, name: &str, kind: &str) -> Principal {
        let pid = blake2b_8(name.as_bytes());
        let p = Principal {
            id: pid.clone(),
            name: name.to_string(),
            kind: kind.to_string(),
        };
        self.principals.insert(pid, p.clone());
        p
    }

    /// The self-certifying principal id for an Ed25519 public key:
    /// blake2b(pubkey, digest_size=8) hex (crypto.Keyring.keyed_pid).
    pub fn keyed_pid(public_key: &[u8; 32]) -> String {
        blake2b_8(public_key)
    }

    /// Mint a SELF-CERTIFYING principal: derive the keypair FIRST
    /// (seed = blake2b(master + name, 32, person="decima:keyid")), then set
    /// pid = blake2b(public_key); the seed is adopted under that pid
    /// (crypto.Keyring.mint_keyed + KeyStore.adopt).
    pub fn mint_keyed(&mut self, name: &str, kind: &str) -> Principal {
        let mut input = self.master.to_vec();
        input.extend_from_slice(name.as_bytes());
        let seed = blake2b_personal_32(&input, PERSON_KEYID);
        let sk = SigningKey::from_bytes(&seed);
        let pid = Self::keyed_pid(sk.verifying_key().as_bytes());
        self.adopted.insert(pid.clone(), seed);
        let p = Principal {
            id: pid.clone(),
            name: name.to_string(),
            kind: kind.to_string(),
        };
        self.principals.insert(pid, p.clone());
        p
    }

    fn signing_key(&self, pid: &str) -> SigningKey {
        if let Some(seed) = self.adopted.get(pid) {
            return SigningKey::from_bytes(seed);
        }
        // DerivedKeyStore._sk: seed = blake2b(master + pid, 32, person="decima:ed255")
        let mut input = self.master.to_vec();
        input.extend_from_slice(pid.as_bytes());
        SigningKey::from_bytes(&blake2b_personal_32(&input, PERSON_ED255))
    }

    /// The principal's Ed25519 verify key, hex.
    pub fn public_key(&self, pid: &str) -> String {
        hex::encode(self.signing_key(pid).verifying_key().as_bytes())
    }

    /// Ed25519-sign `message` (UTF-8 bytes) with the principal's private key;
    /// 64-byte signature as hex. Deterministic (RFC 8032) → golden.
    pub fn sign(&self, pid: &str, message: &str) -> String {
        hex::encode(self.signing_key(pid).sign(message.as_bytes()).to_bytes())
    }

    /// Verify with the principal's PUBLIC key. Any bad/forged/malformed input
    /// returns false, never panics (fail closed).
    pub fn verify(&self, pid: &str, message: &str, sig_hex: &str) -> bool {
        (|| {
            let sk = self.signing_key(pid);
            let sig_bytes: [u8; 64] = hex::decode(sig_hex).ok()?.try_into().ok()?;
            let sig = Signature::from_bytes(&sig_bytes);
            sk.verifying_key()
                .verify_strict(message.as_bytes(), &sig)
                .ok()
        })()
        .is_some()
    }

    /// Verify a detached signature under an explicit public key (used for
    /// keyed/self-certifying principals and one-byte-tamper checks).
    pub fn verify_with_key(public_key: &[u8; 32], message: &str, sig_hex: &str) -> bool {
        let vk = match VerifyingKey::from_bytes(public_key) {
            Ok(vk) => vk,
            Err(_) => return false,
        };
        let sig_bytes: [u8; 64] = match hex::decode(sig_hex).ok().and_then(|b| b.try_into().ok()) {
            Some(b) => b,
            None => return false,
        };
        vk.verify_strict(message.as_bytes(), &Signature::from_bytes(&sig_bytes))
            .is_ok()
    }
}
