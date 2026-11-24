//! Pull the fields the NEAR contract checks out of the signed request and response bytes.
//!
//! The bytes are the raw chat-completions JSON. Strings are unescaped the way
//! `serde_json` does, then the user message is split on the first newline.

use anchor_lang::prelude::*;

use crate::VerdictError;

pub const HEADER_PREFIX: &str = "VERDICT/1";

#[derive(Clone, Debug)]
pub struct BoundRequest {
    pub model: String,
    pub market_id: u64,
    pub round: u32,
    pub rules_hash: [u8; 32],
    pub prompt_template_hash: [u8; 32],
    pub evidence_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ParsedOutcome {
    Yes,
    No,
    TooEarly,
    Ambiguous,
}

pub struct ParsedResponse {
    pub outcome: ParsedOutcome,
    /// Basis points, 0..=10000. `None` when the signed JSON has no confidence.
    pub confidence_bps: Option<u16>,
}

pub fn bind_request(request: &[u8], evidence_hash_expected: &[u8; 32]) -> Result<BoundRequest> {
    let model = first_string(request, b"model")?;
    let contents = all_strings(request, b"content")?;
    require!(contents.len() == 2, VerdictError::BadPayload);
    let system = &contents[0];
    let user = std::str::from_utf8(&contents[1]).map_err(|_| error!(VerdictError::BadPayload))?;
    let (header, evidence) = user.split_once('\n').ok_or(error!(VerdictError::BadPayload))?;
    let evidence_hash = sha256(evidence.as_bytes());
    require!(evidence_hash == *evidence_hash_expected, VerdictError::EvidenceMismatch);
    let fields = parse_header(header)?;
    require!(fields.evidence_hash == evidence_hash, VerdictError::EvidenceMismatch);
    require!(sha256(system) == fields.prompt_template_hash, VerdictError::HeaderMismatch);
    let model = std::str::from_utf8(&model).map_err(|_| error!(VerdictError::BadPayload))?;
    Ok(BoundRequest {
        model: model.to_string(),
        market_id: fields.market_id,
        round: fields.round,
        rules_hash: fields.rules_hash,
        prompt_template_hash: fields.prompt_template_hash,
        evidence_hash: fields.evidence_hash,
    })
}

pub fn parse_response(response: &[u8]) -> Result<ParsedResponse> {
    let reasons = all_strings(response, b"finish_reason")?;
    require!(reasons.len() == 1 && reasons[0] == b"stop", VerdictError::BadPayload);
    let roles = all_strings(response, b"role")?;
    require!(roles.len() == 1 && roles[0] == b"assistant", VerdictError::BadPayload);
    let contents = all_strings(response, b"content")?;
    require!(contents.len() == 1, VerdictError::BadPayload);
    let mut content = std::str::from_utf8(&contents[0])
        .map_err(|_| error!(VerdictError::BadPayload))?
        .trim();
    if let Some(rest) = content.strip_prefix("```json") {
        content = rest.strip_suffix("```").ok_or(error!(VerdictError::BadPayload))?.trim();
    }
    require!(content.starts_with('{'), VerdictError::BadPayload);
    let raw = content.as_bytes();
    let outcome = match first_string(raw, b"outcome")?.as_slice() {
        b"YES" => ParsedOutcome::Yes,
        b"NO" => ParsedOutcome::No,
        b"TOO_EARLY" => ParsedOutcome::TooEarly,
        b"AMBIGUOUS" => ParsedOutcome::Ambiguous,
        _ => return err!(VerdictError::BadPayload),
    };
    let confidence_bps = number_field(raw, b"confidence")?;
    Ok(ParsedResponse { outcome, confidence_bps })
}

struct HeaderFields {
    market_id: u64,
    round: u32,
    rules_hash: [u8; 32],
    prompt_template_hash: [u8; 32],
    evidence_hash: [u8; 32],
}

fn parse_header(header: &str) -> Result<HeaderFields> {
    let mut parts = header.split(' ');
    require!(parts.next() == Some(HEADER_PREFIX), VerdictError::HeaderMismatch);
    let mut market_id = None;
    let mut round = None;
    let mut rules_hash = None;
    let mut prompt_template_hash = None;
    let mut evidence_hash = None;
    let mut count = 0u8;
    for part in parts {
        let (key, value) = part.split_once('=').ok_or(error!(VerdictError::HeaderMismatch))?;
        count = count.checked_add(1).ok_or(error!(VerdictError::HeaderMismatch))?;
        match key {
            "market_id" => {
                require!(market_id.is_none(), VerdictError::HeaderMismatch);
                market_id = Some(value.parse::<u64>().map_err(|_| error!(VerdictError::HeaderMismatch))?);
            }
            "round" => {
                require!(round.is_none(), VerdictError::HeaderMismatch);
                round = Some(value.parse::<u32>().map_err(|_| error!(VerdictError::HeaderMismatch))?);
            }
            "rules_hash" => {
                require!(rules_hash.is_none(), VerdictError::HeaderMismatch);
                rules_hash = Some(decode_hex32(value)?);
            }
            "prompt_template_hash" => {
                require!(prompt_template_hash.is_none(), VerdictError::HeaderMismatch);
                prompt_template_hash = Some(decode_hex32(value)?);
            }
            "evidence_bundle_hash" => {
                require!(evidence_hash.is_none(), VerdictError::HeaderMismatch);
                evidence_hash = Some(decode_hex32(value)?);
            }
            _ => return err!(VerdictError::HeaderMismatch),
        }
    }
    require!(count == 5, VerdictError::HeaderMismatch);
    let fields = HeaderFields {
        market_id: market_id.ok_or(error!(VerdictError::HeaderMismatch))?,
        round: round.ok_or(error!(VerdictError::HeaderMismatch))?,
        rules_hash: rules_hash.ok_or(error!(VerdictError::HeaderMismatch))?,
        prompt_template_hash: prompt_template_hash.ok_or(error!(VerdictError::HeaderMismatch))?,
        evidence_hash: evidence_hash.ok_or(error!(VerdictError::HeaderMismatch))?,
    };
    let canonical = format!(
        "{HEADER_PREFIX} market_id={} round={} rules_hash={} prompt_template_hash={} evidence_bundle_hash={}",
        fields.market_id,
        fields.round,
        hex32(&fields.rules_hash),
        hex32(&fields.prompt_template_hash),
        hex32(&fields.evidence_hash),
    );
    require!(header == canonical, VerdictError::HeaderMismatch);
    Ok(fields)
}

pub fn sha256(data: &[u8]) -> [u8; 32] {
    solana_sha256_hasher::hash(data).to_bytes()
}

pub fn hex32(bytes: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(64);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn decode_hex32(value: &str) -> Result<[u8; 32]> {
    require!(value.len() == 64, VerdictError::HeaderMismatch);
    let mut out = [0u8; 32];
    let bytes = value.as_bytes();
    for i in 0..32 {
        out[i] = (hex_nibble(bytes[i * 2])? << 4) | hex_nibble(bytes[i * 2 + 1])?;
    }
    Ok(out)
}

fn hex_nibble(byte: u8) -> Result<u8> {
    hex_val(byte).ok_or(error!(VerdictError::HeaderMismatch))
}

fn hex_nibble_payload(byte: u8) -> Result<u8> {
    hex_val(byte).ok_or(error!(VerdictError::BadPayload))
}

fn hex_val(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn first_string(bytes: &[u8], key: &[u8]) -> Result<Vec<u8>> {
    let all = all_strings(bytes, key)?;
    require!(all.len() == 1, VerdictError::BadPayload);
    Ok(all.into_iter().next().unwrap())
}

/// One pass. Keys are compared without copying the string. Only matching values are stored.
/// The bump allocator does not reuse freed memory, so copying every string OOMs the 32 KB heap.
fn all_strings(bytes: &[u8], key: &[u8]) -> Result<Vec<Vec<u8>>> {
    let mut found = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] != b'"' {
            i += 1;
            continue;
        }
        let (matched, next) = key_equals(bytes, i, key)?;
        if matched {
            let mut j = next;
            while j < bytes.len() && bytes[j].is_ascii_whitespace() {
                j += 1;
            }
            if j < bytes.len() && bytes[j] == b':' {
                j += 1;
                while j < bytes.len() && bytes[j].is_ascii_whitespace() {
                    j += 1;
                }
                if j < bytes.len() && bytes[j] == b'"' {
                    let (value, after) = read_string(bytes, j)?;
                    found.push(value);
                    i = after;
                    continue;
                }
            }
        }
        i = next;
    }
    Ok(found)
}

fn key_equals(bytes: &[u8], start: usize, key: &[u8]) -> Result<(bool, usize)> {
    require!(start < bytes.len() && bytes[start] == b'"', VerdictError::BadPayload);
    let mut i = start + 1;
    let mut k = 0usize;
    let mut matched = true;
    while i < bytes.len() {
        if bytes[i] == b'"' {
            return Ok((matched && k == key.len(), i + 1));
        }
        let decoded = if bytes[i] == b'\\' {
            i += 1;
            require!(i < bytes.len(), VerdictError::BadPayload);
            let decoded = match bytes[i] {
                b'"' | b'\\' | b'/' => bytes[i],
                b'n' => b'\n',
                b'r' => b'\r',
                b't' => b'\t',
                b'u' => {
                    require!(i + 4 < bytes.len(), VerdictError::BadPayload);
                    let mut code = 0u32;
                    for n in 1..=4 {
                        code = (code << 4) | hex_nibble_payload(bytes[i + n])? as u32;
                    }
                    i += 4;
                    if code > 127 {
                        matched = false;
                        i += 1;
                        continue;
                    }
                    code as u8
                }
                _ => return err!(VerdictError::BadPayload),
            };
            i += 1;
            decoded
        } else {
            let decoded = bytes[i];
            i += 1;
            decoded
        };
        if k >= key.len() || decoded != key[k] {
            matched = false;
        } else {
            k += 1;
        }
    }
    err!(VerdictError::BadPayload)
}

/// `number_field` reads a JSON number. Missing key is `Ok(None)`.
fn number_field(bytes: &[u8], key: &[u8]) -> Result<Option<u16>> {
    let mut i = 0;
    let mut found = None;
    while i < bytes.len() {
        if bytes[i] != b'"' {
            i += 1;
            continue;
        }
        let (matched, next) = key_equals(bytes, i, key)?;
        if matched {
            require!(found.is_none(), VerdictError::BadPayload);
            let mut j = next;
            while j < bytes.len() && bytes[j].is_ascii_whitespace() {
                j += 1;
            }
            require!(j < bytes.len() && bytes[j] == b':', VerdictError::BadPayload);
            j += 1;
            while j < bytes.len() && bytes[j].is_ascii_whitespace() {
                j += 1;
            }
            found = Some(parse_bps(&bytes[j..])?);
            i = j;
            continue;
        }
        i = next;
    }
    Ok(found)
}

/// `0.99` → 9900. `1` → 10000. More than 1 is rejected.
fn parse_bps(bytes: &[u8]) -> Result<u16> {
    let mut i = 0;
    require!(i < bytes.len() && bytes[i].is_ascii_digit(), VerdictError::BadPayload);
    let mut whole: u32 = 0;
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        whole = whole * 10 + (bytes[i] - b'0') as u32;
        require!(whole <= 1, VerdictError::BadPayload);
        i += 1;
    }
    let mut frac: u32 = 0;
    let mut digits = 0u32;
    if i < bytes.len() && bytes[i] == b'.' {
        i += 1;
        while i < bytes.len() && bytes[i].is_ascii_digit() && digits < 4 {
            frac = frac * 10 + (bytes[i] - b'0') as u32;
            digits += 1;
            i += 1;
        }
        // Extra fractional digits are ignored for the stored value, but they must be digits only
        // up to the JSON boundary. We do not round.
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
    }
    while digits < 4 {
        frac *= 10;
        digits += 1;
    }
    let bps = whole * 10_000 + frac;
    require!(bps <= 10_000, VerdictError::BadPayload);
    Ok(bps as u16)
}

fn read_string(bytes: &[u8], start: usize) -> Result<(Vec<u8>, usize)> {
    require!(start < bytes.len() && bytes[start] == b'"', VerdictError::BadPayload);
    let mut out = Vec::with_capacity(bytes.len().saturating_sub(start).min(12_000));
    let mut i = start + 1;
    while i < bytes.len() {
        match bytes[i] {
            b'"' => return Ok((out, i + 1)),
            b'\\' => {
                i += 1;
                require!(i < bytes.len(), VerdictError::BadPayload);
                match bytes[i] {
                    b'"' | b'\\' | b'/' => out.push(bytes[i]),
                    b'n' => out.push(b'\n'),
                    b'r' => out.push(b'\r'),
                    b't' => out.push(b'\t'),
                    b'u' => {
                        require!(i + 4 < bytes.len(), VerdictError::BadPayload);
                        let mut code = 0u32;
                        for k in 1..=4 {
                            code = (code << 4) | hex_nibble_payload(bytes[i + k])? as u32;
                        }
                        let ch = char::from_u32(code).ok_or(error!(VerdictError::BadPayload))?;
                        let mut buf = [0u8; 4];
                        out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                        i += 4;
                    }
                    _ => return err!(VerdictError::BadPayload),
                }
                i += 1;
            }
            byte => {
                out.push(byte);
                i += 1;
            }
        }
    }
    err!(VerdictError::BadPayload)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_real_near_panel_vote() {
        let file: serde_json::Value =
            serde_json::from_str(include_str!("../../../../contract/testdata/real_panel.json")).unwrap();
        let vote = &file["votes"][0];
        let request = vote["request_raw"].as_str().unwrap().as_bytes();
        let response = vote["response_raw"].as_str().unwrap().as_bytes();
        let evidence = hex_literal("ccde677158f3e2ca4962f09e365c2ff7be958df4bdd50b7a23ac259f5fc598b7");
        let bound = bind_request(request, &evidence).unwrap();
        assert_eq!(bound.model, "Qwen/Qwen3.8-27B");
        assert_eq!(bound.market_id, 0);
        assert_eq!(bound.round, 0);
        assert_eq!(bound.evidence_hash, evidence);
        assert_eq!(
            bound.rules_hash,
            hex_literal("0a4e65422937656bae4ae33d26e8c8f2e710faeeaa3572dac0aeb0d5ce5897da")
        );
        let parsed = parse_response(response).unwrap();
        assert_eq!(parsed.outcome, ParsedOutcome::Yes);
        assert_eq!(parsed.confidence_bps, Some(9900));
        let req_hex = hex32(&sha256(request));
        let resp_hex = hex32(&sha256(response));
        let text = format!("{}:{req_hex}:{resp_hex}", bound.model);
        assert_eq!(text, vote["text"].as_str().unwrap());
    }

    fn hex_literal(s: &str) -> [u8; 32] {
        let mut out = [0u8; 32];
        for i in 0..32 {
            out[i] = u8::from_str_radix(&s[i * 2..i * 2 + 2], 16).unwrap();
        }
        out
    }
}
