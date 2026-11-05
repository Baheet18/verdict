# X post draft (HUMAN posts — do not auto-post)

Plain text only (no markdown bold). Suggested text (edit freely):

---

Why: Bloomberg (May 26, 2026) reported ~9 wallets cast about half of UMA dispute votes on Polymarket; WSJ (May 2026) reported ~1 in 5 disputes had a voter with a stake. Polymarket Protocol V2 (Oct 5, 2026) lets each market choose its oracle. No claim anyone is corrupt — just room for another option.

Built Verdict for the NEAR hackathon:

A 3-model AI panel runs in NEAR AI Cloud TEEs, signs every vote (EIP-191), and only auto-settles when all three agree.

Clear markets: auto-settled at ~$0.002, 107/107 agreed with UMA (post-cutoff sports), hardware-attested. Request params allowlisted on-chain.
Unclear markets: escalated to humans — 0 invented answers. TOO_EARLY is first-class (Bitquery: 243/470 UMA disputes were "too early").

Hand-checked answer-bearing sources on 8 disputed markets (curator: AI; Baheet has not personally verified each row): 8 settled, 7 agree UMA, 1 disagree, 0 invent. Same IDs with thin evidence: all escalated. Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.

Repo: https://github.com/Baheet18/verdict

Not financial advice. Demo video + testnet txs coming after I sign deploy.

---

Notes for Baheet:
- Attach the demo video when recorded.
- Add explorer links only after you sign testnet txs.
- Do not paste API keys or .env into the post.
- Do not claim the rule linter predicts disputes (optional checklist only).
- "Unsteerable" in conversation = request-parameter steering blocked; relayer still picks evidence.
- Do not restore a NEAR/Illia endorsement line.
