//! Verdict on Solana.
//!
//! A vote is accepted when it carries the same EIP-191 secp256k1 signature the
//! NEAR contract checks. Ed25519 is not used: NEAR AI Cloud does not produce
//! those signatures. The protocol owner registers signer addresses and does
//! not settle markets.

use anchor_lang::prelude::*;
use anchor_lang::system_program::{self, Transfer};

mod eip191;
mod parse;

use parse::{bind_request, parse_response, ParsedOutcome};

declare_id!("6nbXLUgehsnFqUTvg3EUCtP8mDHodBXFv6NhdJwBt3AW");

/// The default 32 KB heap is too small once a real panel request is hashed and
/// parsed. Transactions must request this same heap frame.
#[cfg(all(feature = "custom-heap", target_os = "solana"))]
#[global_allocator]
static HEAP: solana_program_entrypoint::BumpAllocator = unsafe {
    solana_program_entrypoint::BumpAllocator::with_fixed_address_range(
        solana_program_entrypoint::HEAP_START_ADDRESS as usize,
        256 * 1024,
    )
};

/// Yes or no at or above this confidence (0.70) can be proposed. Below it, or
/// with no confidence in the signed response, the vote is not clear.
pub const MIN_CLEAR_CONFIDENCE_BPS: u16 = 7_000;
pub const CONFIDENCE_ABSENT: u16 = u16::MAX;
/// Request plus response must fit in one allocation. Real panel votes are about 6 KB.
pub const MAX_PAYLOAD: u32 = 9_600;
/// Discriminator plus the fixed `Payload` header. Bytes after this are the raw bodies.
pub const PAYLOAD_DATA: usize = 8 + 8 + 1 + 1 + 4 + 4 + 4;

#[program]
pub mod verdict {
    use super::*;

    pub fn initialize_config(
        ctx: Context<InitializeConfig>,
        signers: [SignerArg; 3],
        dispute_window_seconds: i64,
        dispute_bond_lamports: u64,
    ) -> Result<()> {
        let stored = check_signers(&signers)?;
        check_terms(dispute_window_seconds, dispute_bond_lamports)?;
        let config = &mut ctx.accounts.config;
        config.owner = ctx.accounts.owner.key();
        config.signers = stored;
        config.dispute_window_seconds = dispute_window_seconds;
        config.dispute_bond_lamports = dispute_bond_lamports;
        config.bump = ctx.bumps.config;
        emit!(ConfigUpdated {
            owner: config.owner,
            signers: config.signers,
            dispute_window_seconds,
            dispute_bond_lamports,
        });
        Ok(())
    }

    pub fn update_config(
        ctx: Context<UpdateConfig>,
        signers: [SignerArg; 3],
        dispute_window_seconds: i64,
        dispute_bond_lamports: u64,
    ) -> Result<()> {
        let stored = check_signers(&signers)?;
        check_terms(dispute_window_seconds, dispute_bond_lamports)?;
        let config = &mut ctx.accounts.config;
        config.signers = stored;
        config.dispute_window_seconds = dispute_window_seconds;
        config.dispute_bond_lamports = dispute_bond_lamports;
        emit!(ConfigUpdated {
            owner: config.owner,
            signers: config.signers,
            dispute_window_seconds,
            dispute_bond_lamports,
        });
        Ok(())
    }

    pub fn create_market(
        ctx: Context<CreateMarket>,
        market_id: u64,
        question: String,
        rules_hash: [u8; 32],
        evidence_hash: [u8; 32],
        prompt_template_hash: [u8; 32],
        deadline: i64,
        escalation_owner: Pubkey,
    ) -> Result<()> {
        require!(
            !question.is_empty() && question.len() <= Market::MAX_QUESTION,
            VerdictError::BadQuestion
        );
        let now = ctx.accounts.clock.unix_timestamp;
        require!(deadline > now, VerdictError::DeadlineNotFuture);
        require!(escalation_owner != Pubkey::default(), VerdictError::BadEscalationOwner);
        let config = &ctx.accounts.config;
        let market = &mut ctx.accounts.market;
        market.market_id = market_id;
        market.bump = ctx.bumps.market;
        market.question = question;
        market.rules_hash = rules_hash;
        market.evidence_hash = evidence_hash;
        market.prompt_template_hash = prompt_template_hash;
        market.deadline = deadline;
        market.escalation_owner = escalation_owner;
        market.dispute_window_seconds = config.dispute_window_seconds;
        market.dispute_bond_lamports = config.dispute_bond_lamports;
        market.round = 0;
        market.signers = config.signers;
        market.status = Status::Open;
        market.proposed_outcome = Outcome::Ambiguous;
        market.proposed_at = 0;
        market.final_outcome = Outcome::Ambiguous;
        market.disputer = Pubkey::default();
        market.bond_lamports = 0;
        market.votes = [StoredVote::default(); 3];
        emit!(MarketCreated {
            market_id,
            escalation_owner,
            evidence_hash,
            rules_hash,
            prompt_template_hash,
            deadline,
            dispute_window_seconds: market.dispute_window_seconds,
            dispute_bond_lamports: market.dispute_bond_lamports,
        });
        Ok(())
    }

    pub fn init_payload(
        ctx: Context<InitPayload>,
        market_id: u64,
        slot: u8,
        request_len: u32,
        response_len: u32,
    ) -> Result<()> {
        require!(slot < 3, VerdictError::BadSlot);
        let total = request_len.checked_add(response_len).ok_or(error!(VerdictError::BadPayload))?;
        require!(request_len > 0 && response_len > 0 && total <= MAX_PAYLOAD, VerdictError::BadPayload);
        let market = &ctx.accounts.market;
        require!(market.market_id == market_id, VerdictError::MarketMismatch);
        require!(market.status == Status::Open, VerdictError::NotOpen);
        require!(!market.votes[slot as usize].set, VerdictError::AlreadyVoted);
        let payload = &mut ctx.accounts.payload;
        payload.market_id = market_id;
        payload.slot = slot;
        payload.bump = ctx.bumps.payload;
        payload.request_len = request_len;
        payload.response_len = response_len;
        payload.filled = 0;
        Ok(())
    }

    pub fn write_payload(
        ctx: Context<WritePayload>,
        market_id: u64,
        slot: u8,
        offset: u32,
        data: Vec<u8>,
    ) -> Result<()> {
        let payload = &mut ctx.accounts.payload;
        require!(payload.market_id == market_id && payload.slot == slot, VerdictError::BadPayload);
        require!(offset == payload.filled, VerdictError::BadPayload);
        let end = offset.checked_add(data.len() as u32).ok_or(error!(VerdictError::BadPayload))?;
        let total = payload.request_len + payload.response_len;
        require!(end <= total, VerdictError::BadPayload);
        let start = PAYLOAD_DATA + offset as usize;
        let stop = start + data.len();
        {
            let info = payload.to_account_info();
            let mut buf = info.try_borrow_mut_data()?;
            require!(stop <= buf.len(), VerdictError::BadPayload);
            buf[start..stop].copy_from_slice(&data);
        }
        payload.filled = end;
        Ok(())
    }

    pub fn submit_vote(
        ctx: Context<SubmitVote>,
        market_id: u64,
        slot: u8,
        signature: [u8; 65],
    ) -> Result<()> {
        require!(slot < 3, VerdictError::BadSlot);
        let now = ctx.accounts.clock.unix_timestamp;
        let payload = &ctx.accounts.payload;
        require!(payload.market_id == market_id && payload.slot == slot, VerdictError::BadPayload);
        let total = (payload.request_len + payload.response_len) as usize;
        require!(payload.filled as usize == total, VerdictError::BadPayload);
        let request_len = payload.request_len as usize;
        let market_view = market_checks(&ctx.accounts.market, market_id, slot, now)?;
        let (bound_model, outcome, confidence_bps, request_sha256, response_sha256, address) = {
            let info = payload.to_account_info();
            let buf = info.try_borrow_data()?;
            let start = PAYLOAD_DATA;
            require!(start + total <= buf.len(), VerdictError::BadPayload);
            let bytes = &buf[start..start + total];
            let request = &bytes[..request_len];
            let response = &bytes[request_len..];
            let bound = bind_request(request, &market_view.evidence_hash)?;
            require!(bound.market_id == market_id, VerdictError::HeaderMismatch);
            require!(bound.round == market_view.round, VerdictError::HeaderMismatch);
            require!(bound.rules_hash == market_view.rules_hash, VerdictError::HeaderMismatch);
            require!(bound.prompt_template_hash == market_view.prompt_template_hash, VerdictError::HeaderMismatch);
            require!(bound.evidence_hash == market_view.evidence_hash, VerdictError::EvidenceMismatch);
            require!(bound.model == market_view.model, VerdictError::WrongSigner);
            let address = eip191::recover_address(&bound.model, request, response, &signature)?;
            require!(address == market_view.eth_address, VerdictError::WrongSigner);
            let parsed = parse_response(response)?;
            let outcome = match parsed.outcome {
                ParsedOutcome::Yes => Outcome::Yes,
                ParsedOutcome::No => Outcome::No,
                ParsedOutcome::TooEarly => Outcome::TooEarly,
                ParsedOutcome::Ambiguous => Outcome::Ambiguous,
            };
            (
                bound.model,
                outcome,
                parsed.confidence_bps.unwrap_or(CONFIDENCE_ABSENT),
                parse::sha256(request),
                parse::sha256(response),
                address,
            )
        };
        let _ = bound_model;
        let market = &mut ctx.accounts.market;
        market.votes[slot as usize] = StoredVote {
            eth_address: address,
            outcome,
            confidence_bps,
            request_sha256,
            response_sha256,
            set: true,
        };
        emit!(VoteAccepted {
            market_id,
            slot,
            eth_address: address,
            outcome,
            confidence_bps,
            request_sha256,
            response_sha256,
        });

        if market.votes.iter().all(|vote| vote.set) {
            settle_panel(market, now, market_id)?;
        }
        Ok(())
    }

    pub fn dispute(ctx: Context<Dispute>, market_id: u64) -> Result<()> {
        let now = ctx.accounts.clock.unix_timestamp;
        let market = &ctx.accounts.market;
        require!(market.market_id == market_id, VerdictError::MarketMismatch);
        require!(market.status == Status::Proposed, VerdictError::NotProposed);
        let window_end = market
            .proposed_at
            .checked_add(market.dispute_window_seconds)
            .ok_or(VerdictError::BadWindow)?;
        require!(now < window_end, VerdictError::DisputeWindowClosed);
        let bond = market.dispute_bond_lamports;
        let proposed = market.proposed_outcome;
        let disputer = ctx.accounts.disputer.key();
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.key(),
                Transfer {
                    from: ctx.accounts.disputer.to_account_info(),
                    to: ctx.accounts.market.to_account_info(),
                },
            ),
            bond,
        )?;
        let market = &mut ctx.accounts.market;
        market.status = Status::Escalated;
        market.disputer = disputer;
        market.bond_lamports = bond;
        emit!(MarketDisputed {
            market_id,
            disputer,
            bond_lamports: bond,
            proposed_outcome: proposed,
        });
        Ok(())
    }

    pub fn finalize(ctx: Context<Finalize>, market_id: u64) -> Result<()> {
        let now = ctx.accounts.clock.unix_timestamp;
        let market = &mut ctx.accounts.market;
        require!(market.market_id == market_id, VerdictError::MarketMismatch);
        require!(market.status == Status::Proposed, VerdictError::NotProposed);
        let window_end = market
            .proposed_at
            .checked_add(market.dispute_window_seconds)
            .ok_or(VerdictError::BadWindow)?;
        require!(now >= window_end, VerdictError::DisputeWindowOpen);
        let outcome = market.proposed_outcome;
        market.status = Status::Final;
        market.final_outcome = outcome;
        emit!(MarketFinalized { market_id, outcome });
        Ok(())
    }

    pub fn escalation_resolve(
        ctx: Context<EscalationResolve>,
        market_id: u64,
        outcome: Outcome,
    ) -> Result<()> {
        let market = &mut ctx.accounts.market;
        require!(market.market_id == market_id, VerdictError::MarketMismatch);
        require!(
            ctx.accounts.escalation_owner.key() == market.escalation_owner,
            VerdictError::NotEscalationOwner
        );
        require!(market.status == Status::Escalated, VerdictError::NotEscalated);
        market.status = Status::Final;
        market.final_outcome = outcome;
        emit!(EscalationResolved {
            market_id,
            resolver: market.escalation_owner,
            outcome,
        });
        Ok(())
    }
}

struct MarketView {
    evidence_hash: [u8; 32],
    rules_hash: [u8; 32],
    prompt_template_hash: [u8; 32],
    round: u32,
    model: String,
    eth_address: [u8; 20],
}

fn market_checks(market: &Market, market_id: u64, slot: u8, now: i64) -> Result<MarketView> {
    require!(market.market_id == market_id, VerdictError::MarketMismatch);
    require!(market.status == Status::Open, VerdictError::NotOpen);
    require!(now >= market.deadline, VerdictError::DeadlineNotReached);
    require!(!market.votes[slot as usize].set, VerdictError::AlreadyVoted);
    let signer = &market.signers[slot as usize];
    Ok(MarketView {
        evidence_hash: market.evidence_hash,
        rules_hash: market.rules_hash,
        prompt_template_hash: market.prompt_template_hash,
        round: market.round,
        model: model_str(&signer.model, signer.model_len)?.to_string(),
        eth_address: signer.eth_address,
    })
}

fn settle_panel(market: &mut Market, now: i64, market_id: u64) -> Result<()> {
    let votes = market.votes;
    let first = votes[0].outcome;
    let unanimous = votes.iter().all(|vote| vote.outcome == first);
    let clear = matches!(first, Outcome::Yes | Outcome::No)
        && votes.iter().all(|vote| {
            vote.confidence_bps != CONFIDENCE_ABSENT && vote.confidence_bps >= MIN_CLEAR_CONFIDENCE_BPS
        });
    if unanimous && clear {
        market.status = Status::Proposed;
        market.proposed_outcome = first;
        market.proposed_at = now;
        emit!(VerdictSubmitted {
            market_id,
            status: Status::Proposed,
            proposed_outcome: first,
        });
    } else if unanimous && first == Outcome::TooEarly {
        market.round = market.round.checked_add(1).ok_or(error!(VerdictError::BadPayload))?;
        market.votes = [StoredVote::default(); 3];
        emit!(RoundAdvanced { market_id, round: market.round });
    } else {
        market.status = Status::Escalated;
        emit!(VerdictSubmitted {
            market_id,
            status: Status::Escalated,
            proposed_outcome: Outcome::Ambiguous,
        });
    }
    Ok(())
}

fn check_signers(signers: &[SignerArg; 3]) -> Result<[ModelSigner; 3]> {
    let mut stored = [ModelSigner::default(); 3];
    for (i, signer) in signers.iter().enumerate() {
        require!(signer.model.len() <= 64 && !signer.model.is_empty(), VerdictError::BadSigners);
        require!(signer.eth_address != [0u8; 20], VerdictError::BadSigners);
        let mut model = [0u8; 64];
        model[..signer.model.len()].copy_from_slice(signer.model.as_bytes());
        stored[i] = ModelSigner {
            eth_address: signer.eth_address,
            model_len: signer.model.len() as u8,
            model,
        };
    }
    require!(
        stored[0].eth_address != stored[1].eth_address
            && stored[0].eth_address != stored[2].eth_address
            && stored[1].eth_address != stored[2].eth_address,
        VerdictError::BadSigners
    );
    require!(
        model_str(&stored[0].model, stored[0].model_len)? != model_str(&stored[1].model, stored[1].model_len)?
            && model_str(&stored[0].model, stored[0].model_len)? != model_str(&stored[2].model, stored[2].model_len)?
            && model_str(&stored[1].model, stored[1].model_len)? != model_str(&stored[2].model, stored[2].model_len)?,
        VerdictError::BadSigners
    );
    Ok(stored)
}

fn model_str(bytes: &[u8; 64], len: u8) -> Result<&str> {
    let len = len as usize;
    require!(len > 0 && len <= 64, VerdictError::BadSigners);
    std::str::from_utf8(&bytes[..len]).map_err(|_| error!(VerdictError::BadSigners))
}

fn check_terms(dispute_window_seconds: i64, dispute_bond_lamports: u64) -> Result<()> {
    require!(dispute_window_seconds > 0, VerdictError::BadWindow);
    require!(dispute_bond_lamports > 0, VerdictError::BadBond);
    Ok(())
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct SignerArg {
    pub eth_address: [u8; 20],
    pub model: String,
}

#[derive(Accounts)]
pub struct InitializeConfig<'info> {
    #[account(init, payer = owner, space = 8 + Config::INIT_SPACE, seeds = [b"config"], bump)]
    pub config: Account<'info, Config>,
    #[account(mut)]
    pub owner: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct UpdateConfig<'info> {
    #[account(mut, seeds = [b"config"], bump = config.bump, has_one = owner)]
    pub config: Account<'info, Config>,
    pub owner: Signer<'info>,
}

#[derive(Accounts)]
#[instruction(market_id: u64)]
pub struct CreateMarket<'info> {
    #[account(
        init,
        payer = payer,
        space = 8 + Market::INIT_SPACE,
        seeds = [b"market".as_ref(), market_id.to_le_bytes().as_ref()],
        bump
    )]
    pub market: Account<'info, Market>,
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub system_program: Program<'info, System>,
    pub clock: Sysvar<'info, Clock>,
}

#[derive(Accounts)]
#[instruction(market_id: u64, slot: u8, request_len: u32, response_len: u32)]
pub struct InitPayload<'info> {
    #[account(mut, seeds = [b"market".as_ref(), market_id.to_le_bytes().as_ref()], bump = market.bump)]
    pub market: Account<'info, Market>,
    #[account(
        init,
        payer = payer,
        space = PAYLOAD_DATA + request_len as usize + response_len as usize,
        seeds = [b"payload".as_ref(), market_id.to_le_bytes().as_ref(), &[slot]],
        bump
    )]
    pub payload: Account<'info, Payload>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(market_id: u64, slot: u8)]
pub struct WritePayload<'info> {
    #[account(
        mut,
        seeds = [b"payload".as_ref(), market_id.to_le_bytes().as_ref(), &[slot]],
        bump = payload.bump
    )]
    pub payload: Account<'info, Payload>,
}

#[derive(Accounts)]
#[instruction(market_id: u64, slot: u8)]
pub struct SubmitVote<'info> {
    #[account(mut, seeds = [b"market".as_ref(), market_id.to_le_bytes().as_ref()], bump = market.bump)]
    pub market: Account<'info, Market>,
    #[account(
        mut,
        close = payer,
        seeds = [b"payload".as_ref(), market_id.to_le_bytes().as_ref(), &[slot]],
        bump = payload.bump
    )]
    pub payload: Account<'info, Payload>,
    /// CHECK: rent from the closed payload account is returned here. Not a signer of the vote.
    #[account(mut)]
    pub payer: UncheckedAccount<'info>,
    pub clock: Sysvar<'info, Clock>,
}

#[derive(Accounts)]
#[instruction(market_id: u64)]
pub struct Dispute<'info> {
    #[account(mut, seeds = [b"market".as_ref(), market_id.to_le_bytes().as_ref()], bump = market.bump)]
    pub market: Account<'info, Market>,
    #[account(mut)]
    pub disputer: Signer<'info>,
    pub clock: Sysvar<'info, Clock>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(market_id: u64)]
pub struct Finalize<'info> {
    #[account(mut, seeds = [b"market".as_ref(), market_id.to_le_bytes().as_ref()], bump = market.bump)]
    pub market: Account<'info, Market>,
    pub clock: Sysvar<'info, Clock>,
}

#[derive(Accounts)]
#[instruction(market_id: u64)]
pub struct EscalationResolve<'info> {
    #[account(mut, seeds = [b"market".as_ref(), market_id.to_le_bytes().as_ref()], bump = market.bump)]
    pub market: Account<'info, Market>,
    pub escalation_owner: Signer<'info>,
}

#[account]
#[derive(InitSpace)]
pub struct Config {
    pub owner: Pubkey,
    pub signers: [ModelSigner; 3],
    pub dispute_window_seconds: i64,
    pub dispute_bond_lamports: u64,
    pub bump: u8,
}

#[account]
#[derive(InitSpace)]
pub struct Market {
    pub market_id: u64,
    pub bump: u8,
    #[max_len(200)]
    pub question: String,
    pub rules_hash: [u8; 32],
    pub evidence_hash: [u8; 32],
    pub prompt_template_hash: [u8; 32],
    pub deadline: i64,
    pub escalation_owner: Pubkey,
    pub dispute_window_seconds: i64,
    pub dispute_bond_lamports: u64,
    pub round: u32,
    pub signers: [ModelSigner; 3],
    pub status: Status,
    pub proposed_outcome: Outcome,
    pub proposed_at: i64,
    pub final_outcome: Outcome,
    pub disputer: Pubkey,
    pub bond_lamports: u64,
    pub votes: [StoredVote; 3],
}

impl Market {
    pub const MAX_QUESTION: usize = 200;
}

#[account]
#[derive(InitSpace)]
pub struct Payload {
    pub market_id: u64,
    pub slot: u8,
    pub bump: u8,
    pub request_len: u32,
    pub response_len: u32,
    pub filled: u32,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, InitSpace, Debug)]
pub struct ModelSigner {
    pub eth_address: [u8; 20],
    pub model_len: u8,
    pub model: [u8; 64],
}

impl Default for ModelSigner {
    fn default() -> Self {
        Self { eth_address: [0u8; 20], model_len: 0, model: [0u8; 64] }
    }
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, InitSpace, Debug)]
pub struct StoredVote {
    pub eth_address: [u8; 20],
    pub outcome: Outcome,
    pub confidence_bps: u16,
    pub request_sha256: [u8; 32],
    pub response_sha256: [u8; 32],
    pub set: bool,
}

impl Default for StoredVote {
    fn default() -> Self {
        Self {
            eth_address: [0u8; 20],
            outcome: Outcome::Ambiguous,
            confidence_bps: CONFIDENCE_ABSENT,
            request_sha256: [0u8; 32],
            response_sha256: [0u8; 32],
            set: false,
        }
    }
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, InitSpace, Debug)]
pub enum Outcome {
    Yes,
    No,
    TooEarly,
    Ambiguous,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, InitSpace, Debug)]
pub enum Status {
    Open,
    Proposed,
    Escalated,
    Final,
}

#[event]
pub struct ConfigUpdated {
    pub owner: Pubkey,
    pub signers: [ModelSigner; 3],
    pub dispute_window_seconds: i64,
    pub dispute_bond_lamports: u64,
}

#[event]
pub struct MarketCreated {
    pub market_id: u64,
    pub escalation_owner: Pubkey,
    pub evidence_hash: [u8; 32],
    pub rules_hash: [u8; 32],
    pub prompt_template_hash: [u8; 32],
    pub deadline: i64,
    pub dispute_window_seconds: i64,
    pub dispute_bond_lamports: u64,
}

#[event]
pub struct VoteAccepted {
    pub market_id: u64,
    pub slot: u8,
    pub eth_address: [u8; 20],
    pub outcome: Outcome,
    pub confidence_bps: u16,
    pub request_sha256: [u8; 32],
    pub response_sha256: [u8; 32],
}

#[event]
pub struct VerdictSubmitted {
    pub market_id: u64,
    pub status: Status,
    pub proposed_outcome: Outcome,
}

#[event]
pub struct RoundAdvanced {
    pub market_id: u64,
    pub round: u32,
}

#[event]
pub struct MarketDisputed {
    pub market_id: u64,
    pub disputer: Pubkey,
    pub bond_lamports: u64,
    pub proposed_outcome: Outcome,
}

#[event]
pub struct MarketFinalized {
    pub market_id: u64,
    pub outcome: Outcome,
}

#[event]
pub struct EscalationResolved {
    pub market_id: u64,
    pub resolver: Pubkey,
    pub outcome: Outcome,
}

#[error_code]
pub enum VerdictError {
    #[msg("Signer addresses and model names must be distinct and non-empty")]
    BadSigners,
    #[msg("Dispute window must be greater than zero")]
    BadWindow,
    #[msg("Dispute bond must be greater than zero")]
    BadBond,
    #[msg("Question must be 1 to 200 bytes")]
    BadQuestion,
    #[msg("Deadline must be in the future")]
    DeadlineNotFuture,
    #[msg("Escalation owner is missing")]
    BadEscalationOwner,
    #[msg("Market is not open for a verdict")]
    NotOpen,
    #[msg("Deadline has not passed")]
    DeadlineNotReached,
    #[msg("Vote slot is not one of the three signers")]
    BadSlot,
    #[msg("This signer already voted in this round")]
    AlreadyVoted,
    #[msg("Request or response bytes are not the signed panel payload")]
    BadPayload,
    #[msg("Signed header does not match this market")]
    HeaderMismatch,
    #[msg("Evidence bytes do not match the locked evidence hash")]
    EvidenceMismatch,
    #[msg("Signature does not recover under EIP-191")]
    BadSignature,
    #[msg("Recovered address is not this market's signer for that model")]
    WrongSigner,
    #[msg("Market is not in the dispute window")]
    NotProposed,
    #[msg("Dispute window is closed")]
    DisputeWindowClosed,
    #[msg("Dispute window is still open")]
    DisputeWindowOpen,
    #[msg("Only this market's escalation owner can settle it")]
    NotEscalationOwner,
    #[msg("Market is not escalated")]
    NotEscalated,
    #[msg("Market id does not match the account")]
    MarketMismatch,
}

#[cfg(test)]
mod space_tests {
    use super::*;

    #[test]
    fn market_is_small() {
        assert!(Market::INIT_SPACE < 2_000, "market space {}", Market::INIT_SPACE);
        assert!(Config::INIT_SPACE < 1_000, "config space {}", Config::INIT_SPACE);
    }

    #[test]
    fn instruction_discriminator_is_sha256_of_the_global_name() {
        use anchor_lang::Discriminator;
        let hash = solana_sha256_hasher::hash(b"global:submit_vote").to_bytes();
        assert_eq!(&hash[..8], instruction::SubmitVote::DISCRIMINATOR);
    }
}
