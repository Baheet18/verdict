//! Verdict on Solana: three model signatures, a dispute window, and a
//! per-market escalation owner. The protocol owner sets signers and
//! protocol settings and has no instruction that settles a market.

use anchor_lang::prelude::*;
use anchor_lang::system_program::{self, Transfer};

mod message;
mod sigcheck;

pub use message::{canonical_message, MESSAGE_LEN, MESSAGE_PREFIX, MIN_CLEAR_CONFIDENCE};

declare_id!("6nbXLUgehsnFqUTvg3EUCtP8mDHodBXFv6NhdJwBt3AW");

const INSTRUCTIONS_SYSVAR: Pubkey = pubkey!("Sysvar1nstructions1111111111111111111111111");

/// Signer order is fixed: 0 GLM, 1 Qwen, 2 DeepSeek.
#[program]
pub mod verdict {
    use super::*;

    pub fn initialize_config(
        ctx: Context<InitializeConfig>,
        glm_signer: Pubkey,
        qwen_signer: Pubkey,
        deepseek_signer: Pubkey,
        dispute_window_seconds: i64,
        dispute_bond_lamports: u64,
    ) -> Result<()> {
        check_signers(&[glm_signer, qwen_signer, deepseek_signer])?;
        check_terms(dispute_window_seconds, dispute_bond_lamports)?;
        let config = &mut ctx.accounts.config;
        config.owner = ctx.accounts.owner.key();
        config.glm_signer = glm_signer;
        config.qwen_signer = qwen_signer;
        config.deepseek_signer = deepseek_signer;
        config.dispute_window_seconds = dispute_window_seconds;
        config.dispute_bond_lamports = dispute_bond_lamports;
        config.bump = ctx.bumps.config;
        emit!(ConfigUpdated {
            owner: config.owner,
            glm_signer,
            qwen_signer,
            deepseek_signer,
            dispute_window_seconds,
            dispute_bond_lamports,
        });
        Ok(())
    }

    /// Owner-only. Applies to markets created afterwards. Existing markets keep
    /// the signer keys, window, and bond snapshotted at creation.
    pub fn update_config(
        ctx: Context<UpdateConfig>,
        glm_signer: Pubkey,
        qwen_signer: Pubkey,
        deepseek_signer: Pubkey,
        dispute_window_seconds: i64,
        dispute_bond_lamports: u64,
    ) -> Result<()> {
        check_signers(&[glm_signer, qwen_signer, deepseek_signer])?;
        check_terms(dispute_window_seconds, dispute_bond_lamports)?;
        let config = &mut ctx.accounts.config;
        config.glm_signer = glm_signer;
        config.qwen_signer = qwen_signer;
        config.deepseek_signer = deepseek_signer;
        config.dispute_window_seconds = dispute_window_seconds;
        config.dispute_bond_lamports = dispute_bond_lamports;
        emit!(ConfigUpdated {
            owner: config.owner,
            glm_signer,
            qwen_signer,
            deepseek_signer,
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
        deadline: i64,
        escalation_owner: Pubkey,
    ) -> Result<()> {
        require!(
            !question.is_empty() && question.len() <= Market::MAX_QUESTION,
            VerdictError::BadQuestion
        );
        let now = ctx.accounts.clock.unix_timestamp;
        require!(deadline > now, VerdictError::DeadlineNotFuture);
        require!(
            escalation_owner != Pubkey::default(),
            VerdictError::BadEscalationOwner
        );

        let config = &ctx.accounts.config;
        let market = &mut ctx.accounts.market;
        market.market_id = market_id;
        market.bump = ctx.bumps.market;
        market.question = question;
        market.rules_hash = rules_hash;
        market.evidence_hash = evidence_hash;
        market.deadline = deadline;
        market.escalation_owner = escalation_owner;
        market.dispute_window_seconds = config.dispute_window_seconds;
        market.dispute_bond_lamports = config.dispute_bond_lamports;
        market.signers = [
            config.glm_signer,
            config.qwen_signer,
            config.deepseek_signer,
        ];
        market.status = Status::Open;
        market.proposed_outcome = Outcome::Unclear;
        market.proposed_at = 0;
        market.final_outcome = Outcome::Unclear;
        market.disputer = Pubkey::default();
        market.bond_lamports = 0;
        market.votes = [StoredVote::default(); 3];
        market.votes_set = false;

        emit!(MarketCreated {
            market_id,
            escalation_owner,
            evidence_hash,
            rules_hash,
            deadline,
            dispute_window_seconds: market.dispute_window_seconds,
            dispute_bond_lamports: market.dispute_bond_lamports,
        });
        Ok(())
    }

    /// Three ed25519 signatures, in signer order, in the instruction immediately
    /// before this one. Unanimous clear Yes or No opens the dispute window.
    /// A split, an unclear vote, or a low-confidence vote escalates.
    pub fn submit_verdict(
        ctx: Context<SubmitVerdict>,
        market_id: u64,
        votes: [VoteInput; 3],
    ) -> Result<()> {
        let now = ctx.accounts.clock.unix_timestamp;
        let market = &ctx.accounts.market;
        require!(market.market_id == market_id, VerdictError::MarketMismatch);
        require!(market.status == Status::Open, VerdictError::NotOpen);
        require!(now >= market.deadline, VerdictError::DeadlineNotReached);
        for vote in &votes {
            require!(vote.confidence <= 100, VerdictError::BadConfidence);
        }

        let messages = [
            canonical_message(
                market_id,
                &market.evidence_hash,
                &market.rules_hash,
                votes[0].outcome,
                votes[0].confidence,
                &votes[0].cited_rule,
            ),
            canonical_message(
                market_id,
                &market.evidence_hash,
                &market.rules_hash,
                votes[1].outcome,
                votes[1].confidence,
                &votes[1].cited_rule,
            ),
            canonical_message(
                market_id,
                &market.evidence_hash,
                &market.rules_hash,
                votes[2].outcome,
                votes[2].confidence,
                &votes[2].cited_rule,
            ),
        ];
        sigcheck::verify_panel(
            &ctx.accounts.instructions.to_account_info(),
            &market.signers,
            &messages,
        )?;

        let unanimous_clear = votes.iter().all(|vote| {
            matches!(vote.outcome, Outcome::Yes | Outcome::No)
                && vote.confidence >= MIN_CLEAR_CONFIDENCE
                && vote.outcome == votes[0].outcome
        });

        let market = &mut ctx.accounts.market;
        let signers = market.signers;
        market.votes_set = true;
        for (stored, (vote, signer)) in market.votes.iter_mut().zip(votes.iter().zip(signers)) {
            *stored = StoredVote {
                signer,
                outcome: vote.outcome,
                confidence: vote.confidence,
                cited_rule: vote.cited_rule,
            };
        }

        let (status, proposed) = if unanimous_clear {
            market.status = Status::Proposed;
            market.proposed_outcome = votes[0].outcome;
            market.proposed_at = now;
            (Status::Proposed, votes[0].outcome)
        } else {
            market.status = Status::Escalated;
            (Status::Escalated, Outcome::Unclear)
        };

        emit!(VerdictSubmitted {
            market_id,
            status,
            proposed_outcome: proposed,
            votes: market.votes,
        });
        Ok(())
    }

    /// Anyone may post the snapshotted bond while the window is open.
    /// The lamports stay on the market account. This step does not release them.
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

    /// After the window, with no dispute, the proposed outcome becomes final.
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

    /// Only this market's escalation owner. The protocol owner is not special here.
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

fn check_signers(signers: &[Pubkey; 3]) -> Result<()> {
    require!(
        signers[0] != Pubkey::default()
            && signers[1] != Pubkey::default()
            && signers[2] != Pubkey::default()
            && signers[0] != signers[1]
            && signers[0] != signers[2]
            && signers[1] != signers[2],
        VerdictError::BadSigners
    );
    Ok(())
}

fn check_terms(dispute_window_seconds: i64, dispute_bond_lamports: u64) -> Result<()> {
    require!(dispute_window_seconds > 0, VerdictError::BadWindow);
    require!(dispute_bond_lamports > 0, VerdictError::BadBond);
    Ok(())
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
#[instruction(market_id: u64)]
pub struct SubmitVerdict<'info> {
    #[account(mut, seeds = [b"market".as_ref(), market_id.to_le_bytes().as_ref()], bump = market.bump)]
    pub market: Account<'info, Market>,
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    /// CHECK: address constraint pins this to the instructions sysvar.
    #[account(address = INSTRUCTIONS_SYSVAR)]
    pub instructions: UncheckedAccount<'info>,
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
    /// Index 0. GLM.
    pub glm_signer: Pubkey,
    /// Index 1. Qwen.
    pub qwen_signer: Pubkey,
    /// Index 2. DeepSeek.
    pub deepseek_signer: Pubkey,
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
    pub deadline: i64,
    /// Set at creation. No instruction changes it.
    pub escalation_owner: Pubkey,
    /// Copied from config at creation.
    pub dispute_window_seconds: i64,
    /// Copied from config at creation. Lamports.
    pub dispute_bond_lamports: u64,
    /// Copied from config at creation, in GLM / Qwen / DeepSeek order.
    pub signers: [Pubkey; 3],
    pub status: Status,
    /// Meaningful when `status` is Proposed or when a dispute recorded one.
    pub proposed_outcome: Outcome,
    pub proposed_at: i64,
    /// Meaningful when `status` is Final.
    pub final_outcome: Outcome,
    pub disputer: Pubkey,
    /// Lamports posted with `dispute`, sitting on this account.
    pub bond_lamports: u64,
    pub votes: [StoredVote; 3],
    pub votes_set: bool,
}

impl Market {
    pub const MAX_QUESTION: usize = 200;
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, InitSpace, Debug)]
pub struct StoredVote {
    pub signer: Pubkey,
    pub outcome: Outcome,
    pub confidence: u8,
    pub cited_rule: [u8; 32],
}

impl Default for StoredVote {
    fn default() -> Self {
        Self {
            signer: Pubkey::default(),
            outcome: Outcome::Unclear,
            confidence: 0,
            cited_rule: [0u8; 32],
        }
    }
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, InitSpace, Debug)]
pub struct VoteInput {
    pub outcome: Outcome,
    pub confidence: u8,
    pub cited_rule: [u8; 32],
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, InitSpace, Debug)]
pub enum Outcome {
    Yes,
    No,
    Unclear,
}

impl Outcome {
    pub const fn as_byte(self) -> u8 {
        match self {
            Outcome::Yes => 0,
            Outcome::No => 1,
            Outcome::Unclear => 2,
        }
    }
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
    pub glm_signer: Pubkey,
    pub qwen_signer: Pubkey,
    pub deepseek_signer: Pubkey,
    pub dispute_window_seconds: i64,
    pub dispute_bond_lamports: u64,
}

#[event]
pub struct MarketCreated {
    pub market_id: u64,
    pub escalation_owner: Pubkey,
    pub evidence_hash: [u8; 32],
    pub rules_hash: [u8; 32],
    pub deadline: i64,
    pub dispute_window_seconds: i64,
    pub dispute_bond_lamports: u64,
}

#[event]
pub struct VerdictSubmitted {
    pub market_id: u64,
    pub status: Status,
    pub proposed_outcome: Outcome,
    pub votes: [StoredVote; 3],
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
    #[msg("Signer keys must be distinct and non-default")]
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
    #[msg("Vote confidence must be 0 to 100")]
    BadConfidence,
    #[msg("Ed25519 instruction missing or not immediately before this one")]
    MissingEd25519,
    #[msg("Ed25519 instruction layout is not the canonical three-signature form")]
    BadEd25519Layout,
    #[msg("Signature was not made by this market's model signer")]
    WrongSigner,
    #[msg("Signed message does not match this market and vote")]
    MessageMismatch,
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
