# Panel training cutoffs

No vendor publishes an exact training-data cutoff for these models. We therefore use an **upper bound**: a model cannot have trained on events after its release date. The latest panel release is DeepSeek-V4.1-Flash-1017 (2026-10-17), and every scaled-benchmark market has its game day (US Eastern) **strictly after 2026-10-17**, so all of them are post-cutoff for all three models by construction.

| Model | Release (upper bound) | Source | Other estimates (not vendor-sourced) |
|---|---|---|---|
| Qwen/Qwen3.8-27B | 2026-08-05 | postcutoff.com release listing | BenchGecko knowledge-horizon probe: ~Apr 2026 |
| z-ai/glm-5.3-flash | 2026-08-26 | z.ai blog + docs release notes | BenchGecko: ~Jan 2026 |
| deepseek-ai/DeepSeek-V4.1-Flash-1017 | 2026-10-17 (snapshot suffix) | model id / release notes | BenchGecko: ~May 2026; aggregators list "2025-05" (unsourced) |

Using the latest release as the split is conservative for Qwen and GLM (their true cutoffs are earlier). It does not rule out retrieval at inference time — the NEAR AI endpoints are plain completions with no tool use in our requests.
