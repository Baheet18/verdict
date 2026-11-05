# Verdict demo video script (~2.5 minutes)

Record locally. Do **not** show API keys, `.env`, or wallet seed phrases.
Offline mode first (judge-reproducible), then optional live attest.

| Time | Shot | On-screen / voiceover |
|---|---|---|
| 0:00–0:15 | Title card: “Verdict — TEE-attested market resolution on NEAR” + GitHub URL | “Verdict is a three-model AI resolution panel that runs in NEAR AI Cloud sealed hardware and signs its verdicts.” |
| 0:15–0:35 | Terminal: clone + `python scripts/offline_demo.py` (no API key) | “A judge with no API key can reproduce this offline: contract tests, cached EIP-191 signatures, and the scoreboard rebuilt from fixtures.” |
| 0:35–0:55 | `cargo test` scrolling PASS (54 tests) | “On-chain checks bind every vote to market, round, rules, prompt template, and a committed evidence hash — and reject steered request keys like logit_bias.” |
| 0:55–1:15 | Open `scoreboard/index.html` in browser | “Post-cutoff sports: 107/107 panel-settled markets matched UMA. Disputed thin-evidence: 0/34 settled. Curated multi-source subset: 0/12 settles, 12 abstain, 0 invented.” |
| 1:15–1:40 | Flash `docs/BENCHMARK.md` + `docs/BENCHMARK_DISPUTED.md` numbers | “Headline is post-cutoff accuracy among settled markets. Easy sports ≠ disputed geopolitics. Curated multi-source subset is in fixtures/evidence.” |
| 1:40–2:05 | `python scripts/attest_demo.py Qwen/Qwen3.8-27B` PASS then tamper FAIL | “Hardware + host stack verified: Intel TDX + NVIDIA NRAS, allowlisted digests. Tampering the compose fails closed.” |
| 2:05–2:25 | `deploy/TESTNET.md` + unsigned `deploy_prepare.py` output | “Wasm is built. Testnet deploy is signed in the builder’s own wallet — keys never enter the repo.” |
| 2:25–2:40 | Limits slide (README Limits bullets) | “Model container not measured; evidence fetched outside the TEE; owner/relayer still trusted for registration and evidence commit.” |
| 2:40–2:50 | End card: repo + “NEAR Hackathon · submit by Nov 14 23:59 UTC” | “Verdict: certified resolutions you can re-hash.” |

## B-roll checklist
- `scoreboard/index.html`
- `fixtures/evidence/MANIFEST.json`
- `scripts/expected_measurements.json` (blur nothing secret — file is public pins)
- Contract snippet: `check_request` allowlist + `commit_evidence`

## Do not film
- `.env`, API key dashboards with key visible, wallet recovery phrases, private RPC tokens.
