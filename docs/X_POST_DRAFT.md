# X post draft (HUMAN posts — do not auto-post)

Suggested text (edit freely):

---

Built **Verdict** for the NEAR hackathon:

A 3-model AI panel runs in NEAR AI Cloud TEEs, signs every vote (EIP-191), and only auto-settles when all three agree.

What’s different vs “we called an LLM”:
• request bytes bound on-chain to market / round / rules / evidence
• evidence must be committed by a relayer before votes
• hardware allowlist (TDX + NVIDIA) — fail closed on unknown digests
• offline screener mode: no API key needed to verify cached signatures + rebuild the scoreboard

Post-cutoff sports benchmark (cached): panel settled 107 markets, matched UMA on 107. Disputed thin-evidence: abstained 34/34. Curated multi-source subset (12 markets): 0 settles / 12 abstain / 0 invented.

Repo: https://github.com/Baheet18/verdict

(Not financial advice. Not an endorsement by NEAR/Illia. Demo video + testnet txs coming.)

---

Notes for Baheet:
- Attach the demo video when recorded.
- Add explorer links only after you sign testnet txs.
- Do not paste API keys or `.env` into the post.
