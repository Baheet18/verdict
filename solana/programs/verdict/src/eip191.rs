//! EIP-191 personal_sign, the same text the NEAR contract recovers.
//!
//! Signed text: `model:sha256(request_raw):sha256(response_raw)`
//! Hash: keccak256("\x19Ethereum Signed Message:\n" || decimal_len || text)
//! Address: last 20 bytes of keccak256(recovered 64-byte public key)

use anchor_lang::prelude::*;
use solana_secp256k1_recover::secp256k1_recover;

use crate::VerdictError;
use crate::parse::{hex32, sha256};

pub fn recover_address(model: &str, request: &[u8], response: &[u8], signature: &[u8; 65]) -> Result<[u8; 20]> {
    let req = sha256(request);
    let resp = sha256(response);
    let mut text = Vec::with_capacity(model.len() + 1 + 64 + 1 + 64);
    text.extend_from_slice(model.as_bytes());
    text.push(b':');
    text.extend_from_slice(hex32(&req).as_bytes());
    text.push(b':');
    text.extend_from_slice(hex32(&resp).as_bytes());

    let mut preimage = Vec::with_capacity(26 + 4 + text.len());
    preimage.extend_from_slice(b"\x19Ethereum Signed Message:\n");
    push_decimal(&mut preimage, text.len());
    preimage.extend_from_slice(&text);
    let digest = solana_keccak_hasher::hash(&preimage).to_bytes();

    let recid = match signature[64] {
        27 | 28 => signature[64] - 27,
        0 | 1 => signature[64],
        _ => return err!(VerdictError::BadSignature),
    };
    let pubkey = secp256k1_recover(&digest, recid, &signature[..64])
        .map_err(|_| error!(VerdictError::BadSignature))?;
    let address_hash = solana_keccak_hasher::hash(&pubkey.to_bytes()).to_bytes();
    let mut address = [0u8; 20];
    address.copy_from_slice(&address_hash[12..]);
    Ok(address)
}

fn push_decimal(out: &mut Vec<u8>, mut n: usize) {
    if n == 0 {
        out.push(b'0');
        return;
    }
    let mut tmp = [0u8; 20];
    let mut i = tmp.len();
    while n > 0 {
        i -= 1;
        tmp[i] = b'0' + (n % 10) as u8;
        n /= 10;
    }
    out.extend_from_slice(&tmp[i..]);
}
