//! Bind an ed25519 precompile instruction to the three expected messages.
//!
//! The native program verifies the signatures. This module checks that the
//! instruction immediately before ours is that program, that each offset
//! refers to *this* instruction (`u16::MAX`), and that the pubkey and message
//! bytes the precompile verified are the ones this market expects.
//! Pointing an offset at another instruction's bytes is rejected.

use anchor_lang::prelude::*;
use anchor_lang::compat::solana_instructions_sysvar::get_instruction_relative;
use solana_sdk_ids::ed25519_program;

use crate::VerdictError;
use crate::message::MESSAGE_LEN;

const SIGNATURE_LEN: usize = 64;
const PUBKEY_LEN: usize = 32;
const OFFSETS_LEN: usize = 14;
const OFFSETS_START: usize = 2;
const SIGNATURE_COUNT: usize = 3;

pub fn verify_panel(
    instructions: &AccountInfo,
    signers: &[Pubkey; 3],
    messages: &[[u8; MESSAGE_LEN]; 3],
) -> Result<()> {
    let ix = get_instruction_relative(-1, instructions).map_err(|_| error!(VerdictError::MissingEd25519))?;
    require!(
        ix.program_id.as_ref() == ed25519_program::ID.as_ref(),
        VerdictError::MissingEd25519
    );
    require!(ix.accounts.is_empty(), VerdictError::BadEd25519Layout);

    let data = &ix.data;
    let header_len = OFFSETS_START + SIGNATURE_COUNT * OFFSETS_LEN;
    require!(data.len() >= header_len, VerdictError::BadEd25519Layout);
    require!(data[0] as usize == SIGNATURE_COUNT, VerdictError::BadEd25519Layout);
    require!(data[1] == 0, VerdictError::BadEd25519Layout);

    for i in 0..SIGNATURE_COUNT {
        let start = OFFSETS_START + i * OFFSETS_LEN;
        let signature_offset = u16::from_le_bytes([data[start], data[start + 1]]);
        let signature_ix = u16::from_le_bytes([data[start + 2], data[start + 3]]);
        let pubkey_offset = u16::from_le_bytes([data[start + 4], data[start + 5]]);
        let pubkey_ix = u16::from_le_bytes([data[start + 6], data[start + 7]]);
        let message_offset = u16::from_le_bytes([data[start + 8], data[start + 9]]);
        let message_size = u16::from_le_bytes([data[start + 10], data[start + 11]]);
        let message_ix = u16::from_le_bytes([data[start + 12], data[start + 13]]);

        require!(
            signature_ix == u16::MAX && pubkey_ix == u16::MAX && message_ix == u16::MAX,
            VerdictError::BadEd25519Layout
        );
        require!(
            message_size as usize == MESSAGE_LEN,
            VerdictError::MessageMismatch
        );

        let signature = read_region(data, signature_offset, SIGNATURE_LEN, header_len)?;
        let pubkey = read_region(data, pubkey_offset, PUBKEY_LEN, header_len)?;
        let message = read_region(data, message_offset, MESSAGE_LEN, header_len)?;
        // The region must exist. The precompile checks the signature bytes.
        let _ = signature;

        require!(pubkey == signers[i].as_ref(), VerdictError::WrongSigner);
        require!(message == messages[i].as_ref(), VerdictError::MessageMismatch);
    }
    Ok(())
}

fn read_region<'a>(
    data: &'a [u8],
    offset: u16,
    len: usize,
    header_len: usize,
) -> Result<&'a [u8]> {
    let start = offset as usize;
    require!(start >= header_len, VerdictError::BadEd25519Layout);
    let end = start.checked_add(len).ok_or(error!(VerdictError::BadEd25519Layout))?;
    require!(end <= data.len(), VerdictError::BadEd25519Layout);
    Ok(&data[start..end])
}
