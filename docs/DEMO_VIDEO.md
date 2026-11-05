# Verdict demo video script (~2.5 minutes)

Record locally. Do **not** show API keys, `.env`, or wallet seed phrases.
Offline mode first (judge-reproducible), then optional live attest.

| Time | Shot | On-screen / voiceover |
|---|---|---|
| 0:00–0:15 | Title: “Verdict” + GitHub URL; subtitles with WHY citations | “Bloomberg May 26 2026: about nine wallets cast roughly half of UMA dispute votes on Polymarket. WSJ May 2026: about one in five disputes had a voter with a stake. Protocol V2 on Oct 5 2026 lets each market choose its oracle. Verdict is a hardware-attested option for that slot — not a claim anyone is corrupt.” |
| 0:15–0:35 | Terminal: clone + `python scripts/offline_demo.py` (no API key) | “A judge with no API key can reproduce this offline: contract tests, cached EIP-191 signatures, and the scoreboard rebuilt from fixtures.” |
| 0:35–0:55 | `cargo test` scrolling PASS (56 tests) | “On-chain checks bind every vote to market, round, rules, prompt template, and a committed evidence hash — reject steered request keys, duplicate JSON keys, and backslash-escaped object keys.” |
| 0:55–1:20 | Open `scoreboard/index.html` | “Clear markets: auto-settled at about $0.002, 107 of 107 agreed with UMA, hardware-attested. Unclear markets escalate — zero invented answers. TOO_EARLY is first-class.” |
| 1:20–1:40 | Hand-curated table on scoreboard | “With curator-(AI)-checked answer-bearing sources on 8 formerly disputed markets: 8 settled, 7 agree UMA, 1 disagree, 0 invented. Same IDs with thin evidence all escalated. Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.” |
| 1:40–2:05 | `python scripts/attest_demo.py Qwen/Qwen3.8-27B` PASS then tamper FAIL | “Hardware + host stack verified: Intel TDX + NVIDIA NRAS, allowlisted digests. Tampering the compose fails closed.” |
| 2:05–2:25 | `deploy/TESTNET.md` + unsigned sports `deploy_prepare.py` (Saints vs Giants) | “Wasm is built. Demo market is a real post-cutoff NFL game. Testnet deploy is signed in the builder’s own wallet — keys never enter the repo. evidence_uri is a commit-SHA permalink after a human-approved push — never /master/.” |
| 2:25–2:40 | Limits slide | “Model container not measured; evidence fetched outside the TEE; owner/relayer still trusted for registration and evidence commit. ‘Unsteerable’ means request-parameter steering is blocked, not that the relayer is trustless.” |
| 2:40–2:50 | End card: repo + “NEAR Hackathon · submit by Nov 14 23:59 UTC” | “Verdict: certified resolutions you can re-hash.” |

## B-roll checklist
- `scoreboard/index.html` (filters + answer-bearing sources column; curator (AI) label)
- `docs/VISA_CASE.md` (under review — a more precise 9.5% figure may support UMA's YES)
- `fixtures/evidence_hand/MANIFEST.json`
- `scripts/expected_measurements.json`
- Contract snippet: `check_request` allowlist + `reject_duplicate_json_keys` + `commit_evidence`
- `scripts/evidence_uri.py` PENDING → commit-SHA flow

## Numbers (must match cache)
- Sports post-cutoff: 124 markets, 107 settled, 107 agree UMA, 0 disagree, ~$0.0022/market (`docs/BENCHMARK.md`)
- Thin disputed: 34 escalate (`docs/BENCHMARK_DISPUTED.md`)
- Hand disputed: 8 settled / 7 agree / 1 disagree / 0 invent (`docs/BENCHMARK_DISPUTED_HAND.md`)
- Bitquery TOO_EARLY: 243/470 (52%) — https://bitquery.io/investigations/who-decides-polymarket-disputes
- Do **not** claim the rule linter predicts disputes.
- Curator column is **curator (AI)** unless Baheet personally verifies.

## Do not film
- `.env`, API key dashboards with key visible, wallet recovery phrases, private RPC tokens.
