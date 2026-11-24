//! Canonical vote message. Step 2 must sign these exact bytes.

use crate::Outcome;

/// ASCII prefix. 13 bytes, no terminator.
pub const MESSAGE_PREFIX: &[u8; 13] = b"VERDICT-SOL/1";

/// Fixed size of [`canonical_message`].
pub const MESSAGE_LEN: usize = 119;

/// Yes / No at or above this confidence can auto-propose. Below it, the vote is low-confidence.
/// Unclear always escalates. The owner cannot change this.
pub const MIN_CLEAR_CONFIDENCE: u8 = 70;

/// Little-endian layout, 119 bytes:
///
/// | offset | len | field                                      |
/// | -----: | --: | ------------------------------------------ |
/// |      0 |  13 | `VERDICT-SOL/1`                            |
/// |     13 |   8 | `market_id` as `u64` little-endian         |
/// |     21 |  32 | `evidence_hash`                            |
/// |     53 |  32 | `rules_hash`                               |
/// |     85 |   1 | outcome: 0 yes, 1 no, 2 unclear            |
/// |     86 |   1 | confidence, 0..=100                        |
/// |     87 |  32 | cited rule hash (id or rule text)          |
pub fn canonical_message(
    market_id: u64,
    evidence_hash: &[u8; 32],
    rules_hash: &[u8; 32],
    outcome: Outcome,
    confidence: u8,
    cited_rule: &[u8; 32],
) -> [u8; MESSAGE_LEN] {
    let mut out = [0u8; MESSAGE_LEN];
    out[..13].copy_from_slice(MESSAGE_PREFIX);
    out[13..21].copy_from_slice(&market_id.to_le_bytes());
    out[21..53].copy_from_slice(evidence_hash);
    out[53..85].copy_from_slice(rules_hash);
    out[85] = outcome.as_byte();
    out[86] = confidence;
    out[87..119].copy_from_slice(cited_rule);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn message_binds_market_id() {
        let evidence = [7u8; 32];
        let rules = [9u8; 32];
        let cited = [3u8; 32];
        let a = canonical_message(1, &evidence, &rules, Outcome::Yes, 90, &cited);
        let b = canonical_message(2, &evidence, &rules, Outcome::Yes, 90, &cited);
        assert_ne!(a, b);
        assert_eq!(&a[..13], b"VERDICT-SOL/1");
        assert_eq!(&a[13..21], 1u64.to_le_bytes());
        assert_eq!(a[85], 0);
        assert_eq!(a[86], 90);
        assert_eq!(a.len(), MESSAGE_LEN);
    }

    #[test]
    fn outcome_bytes_are_stable() {
        assert_eq!(Outcome::Yes.as_byte(), 0);
        assert_eq!(Outcome::No.as_byte(), 1);
        assert_eq!(Outcome::Unclear.as_byte(), 2);
    }
}
