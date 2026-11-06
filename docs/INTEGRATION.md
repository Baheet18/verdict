# Integrating with Verdict

This is the local integration surface for another prediction-market app. It replaces the manual loop of JSON files and hand-run commands (`deploy_prepare.py`, `publish_evidence.py`, then `commit_evidence` and three `submit_verdict` calls).

The server you start with `python scripts/api.py` is a **mock**. It keeps an in-process chain and a scripted panel. It does not submit NEAR transactions, it does not deploy a contract, and it does not call Qwen, DeepSeek, or GLM. `GET /v1/health` repeats the limits below.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/api.py --data-dir out/relayer-api --port 8787
```

Bind is `127.0.0.1` by default. There is no authentication. Do not expose this process on a public network.

Offline check, with no NEAR account and no NEAR AI key:

```bash
python scripts/test_relayer_api.py
```

## What the app supplies

`POST /v1/markets` with a JSON object:

| Field | Required | Meaning |
| --- | --- | --- |
| `question` | yes | Market question. Stored on the market and copied into the evidence bundle. |
| `rules` | yes | Resolution rules. `rules_hash` is sha256 of these exact bytes. |
| `sources` | yes | One or more `http://` or `https://` URLs, no userinfo. This is the locked source list. |
| `deadline_ns` | yes | Integer nanoseconds. Must be greater than the mock clock (`GET /v1/health` field `now_ns`). Betting is not part of this API; the deadline is when the relayer may lock evidence. |
| `resolvers` | no | Omit it to bind the three signers from `GET /v1/resolvers`. If you send it, it must be exactly those three addresses. Any other set is rejected. This process cannot sign for accounts it did not generate. |
| `source_snapshots` | no | `[{url, text, fetched_at?, sha256?}]`. `url` must be one of `sources`. `sha256` is recomputed from `text` and rejected if it does not match. If a source has no snapshot, the relayer fetches it only when the server was started with `--fetch`. The default does not fetch. |
| `market_end` | no | Short string copied into the evidence bundle. It is not a second deadline. |
| `dispute_window_ns` | no | Default `3600000000000` (one hour). A unanimous YES/NO stays `Proposed` until this window has elapsed and relay runs again. Returned as `dispute_window_ns`. |
| `dispute_bond_yocto` | no | Decimal string. Default `"1000000000000000000000000"` (1 NEAR), matching `scripts/deploy_prepare.py`. This API does not submit disputes. Returned as `dispute_bond_yocto`. |
| `prompt_template_hash` | no | Must be omitted or equal the shipped prompt in `scripts/verdict_request.py`. Other prompts are rejected. |
| `mock_script` | no | Mock only. Map of the three model ids to `YES`, `NO`, `TOO_EARLY`, or `AMBIGUOUS`. Omitted models abstain (`AMBIGUOUS`). This is not evidence and it is not sent to a model. |

The three model ids are `Qwen/Qwen3.8-27B`, `deepseek-ai/DeepSeek-V4.1-Flash-1017`, and `z-ai/glm-5.3-flash`.

## What Verdict returns

`GET /v1/markets/{id}` is safe to poll. It does not submit votes.

| Field | Meaning |
| --- | --- |
| `status` | `Open`, `Proposed`, `Escalated`, `Final`, or `Disputed`. |
| `proposed_outcome` | `YES` or `NO` when the three votes agree and re-check, and the dispute window is still open. Otherwise `null`. |
| `final_outcome` | `YES` or `NO` only when the chain status is `Final` and every vote re-checked. Otherwise `null`. |
| `escalated` | `true` when the panel split, or all three returned `AMBIGUOUS`. `final_outcome` stays `null`. No outcome is filled in. |
| `votes` | One entry per submitted vote: model, signer, outcome, both sha256 values, `signature_ok`, `model_binding_ok`, and `label`. The single-market GET also includes `request_raw`, `response_raw`, and `signature` so you can recompute the signature. The list endpoint omits those bodies. |
| `label` | `Signature valid · hardware check pending` when the signature recovered to the registered signer for that model. This path does not re-check TDX or NRAS. It will not say `Hardware + host stack verified`. |
| `evidence_bundle_hash` | sha256 of the evidence string (the user-message body after the header newline), once `commit_evidence` has run. |
| `evidence_uri` | Always a `PENDING_COMMIT_SHA://…` placeholder. This API does not invent a commit SHA and does not emit `/master/`, `/main/`, or a `raw.githubusercontent.com` URL. |
| `evidence_api_path` | `GET /v1/evidence/{hash}` returns the wrapper JSON. Hash the `evidence` field and compare it to `evidence_bundle_hash`. |
| `chain_calls` | The contract methods this market has already run: `create_market`, `commit_evidence`, `submit_verdict`, `finalize`. |
| `panel_execution` | `scripted` on this server. |
| `chain` | `mock`. |

`votes_verified` is true only when all three current votes re-checked and name the committed evidence hash.

## Endpoints

| Method and path | Effect |
| --- | --- |
| `GET /v1/health` | Mode, mock clock, panel ids, limitations. |
| `GET /v1/resolvers` | The three in-memory signer addresses for this process. They change when the process restarts. |
| `POST /v1/markets` | Create a market. |
| `GET /v1/markets` | Short list. |
| `GET /v1/markets/{id}` | Status and, when final, the verified outcome. |
| `POST /v1/markets/{id}/relay` | Run the relayer for that market. Before the deadline the status is `409` and `error` is `market not ended`. |
| `POST /v1/relay/tick` | Relay every market. Each result is `{market_id, ok, market}` or `{market_id, ok, error}`. One market still before its deadline does not fail the others. |
| `GET /v1/evidence/{sha256}` | Published bundle for a hash this process has written. |

Mock-only, not part of the app contract:

| Method and path | Effect |
| --- | --- |
| `POST /v1/testing/clock` | Body `{"now_ns": <integer>}`. The mock clock does not follow wall time and does not move unless you call this. |
| `POST /v1/testing/markets/{id}/mock-script` | Body `{"mock_script": {...}}`. Allowed only while the current round has no votes. |

Unknown JSON fields are rejected, including sampling fields such as `logit_bias`.

## What the relayer does

Same order as the contract, using `scripts/verdict_request.py` for the request bytes and `scripts/publish_evidence.py` for the content-addressed file.

1. `create_market` stores the question, rules, source URLs, the shipped `prompt_template_hash`, and the three resolvers.
2. After `now_ns >= deadline_ns`, the relayer builds **one** evidence string from the locked snapshots (`evidence_bundle` in `verdict_request.py`), publishes it, and calls `commit_evidence(market_id, round, evidence_bundle_hash, evidence_uri)`.
3. It builds one request per model with `build_request`. The requests differ in the `model` field. The evidence bytes are the same. Each request is exactly `{model, messages, temperature, seed}` with `temperature` 0 and `seed` 1114. `max_tokens` and other keys are not sent.
4. It checks the EIP-191 signature, then calls `submit_verdict` with those exact request and response bytes. A bad signature is not submitted.
5. If all three agree on `YES` or `NO`, the chain status becomes `Proposed`. When the dispute window has elapsed, relay calls `finalize` and `final_outcome` is set.
6. If all three return `TOO_EARLY`, the chain clears the votes and the evidence commitment and increments `round`. The next relay must commit a new bundle. The old signatures do not verify against the new round header.
7. Any split, or a unanimous `AMBIGUOUS`, becomes `Escalated`. `final_outcome` stays `null`.

Calling relay again after `Final` or `Escalated` does not submit more votes.

The in-process chain rejects the same classes of bad input as `contract/src/lib.rs`: evidence before the deadline, a vote before `commit_evidence`, a second commit by the relayer, a vote whose evidence hash is not the committed hash, an extra request key, a duplicate or backslash-escaped JSON key, a signature that recovers to a signer registered for a different model, and a reused signature.

## Example

The `YES` below comes from `mock_script`. The snapshot text is locked and hashed so you can test your client. It is not graded by the three models.

```bash
# clock is frozen until you move it
curl -s -X POST localhost:8787/v1/testing/clock \
  -H 'content-type: application/json' \
  -d '{"now_ns":1000000000}'

curl -s -X POST localhost:8787/v1/markets \
  -H 'content-type: application/json' \
  -d '{
    "question": "Did the example snapshots agree?",
    "rules": "Map a scripted unanimous YES to YES. This example does not ask the models.",
    "sources": ["https://example.com/a", "https://example.com/b"],
    "deadline_ns": 2000000000,
    "dispute_window_ns": 0,
    "market_end": "2026-11-06T00:00:00Z",
    "source_snapshots": [
      {"url": "https://example.com/a", "fetched_at": "2026-11-06T00:00:00Z", "text": "example snapshot A"},
      {"url": "https://example.com/b", "fetched_at": "2026-11-06T00:00:00Z", "text": "example snapshot B"}
    ],
    "mock_script": {
      "Qwen/Qwen3.8-27B": "YES",
      "deepseek-ai/DeepSeek-V4.1-Flash-1017": "YES",
      "z-ai/glm-5.3-flash": "YES"
    }
  }'

curl -s -X POST localhost:8787/v1/testing/clock \
  -H 'content-type: application/json' \
  -d '{"now_ns":2000000000}'

curl -s -X POST localhost:8787/v1/markets/0/relay
curl -s localhost:8787/v1/markets/0
```

A successful mock run has `status` `Final`, `final_outcome` `YES`, `votes_verified` true, and `evidence_uri` starting with `PENDING_COMMIT_SHA://`. Set one model to `NO` in `mock_script` and the same flow returns `Escalated` with `final_outcome` null.

Leave `dispute_window_ns` at the default and the first relay stops at `Proposed`. `final_outcome` is null until you advance `now_ns` past `status_raw.Proposed.at_ns + dispute_window_ns` and call relay again.

## Live NEAR and live models

`scripts/panel.py` contains `LivePanel`, the same chat-completions and `/signature/{id}` calls `scripts/bench_scale.py` already makes. `scripts/api.py` does not construct it. Tests pass a fake transport and a fake key provider. The library does not read the process environment and does not open a `.env` file.

Signing a real `commit_evidence` or `submit_verdict` on NEAR still needs your own testnet keychain. This repository does not hold that key. The manual steps stay in `deploy/TESTNET.md`. `chain_calls` names the methods and the evidence hash. The exact `submit_verdict` bytes are the `request_raw`, `response_raw`, and `signature` on each vote in `GET /v1/markets/{id}`. This process does not send them.

## Limitations

- Mock chain only. Restarting the process drops markets and generates new signer addresses. Nothing here talks to `baheet.testnet`.
- The scripted panel does not run the models. If you omit `mock_script`, every model returns `AMBIGUOUS` and the market escalates. That default is intentional: the mock does not invent `YES` or `NO`.
- Hardware attestation stays an owner step (`scripts/hw_verify.py`, then `register_signer`). Vote labels from this API stay `Signature valid · hardware check pending`.
- Evidence is gathered outside the TEE. The relayer chooses the snapshots. The contract rejects steered request parameters. That does not make the relayer's evidence choice trustless.
- `evidence_uri` is a pending placeholder until a person pushes the file and chooses a commit SHA with `scripts/evidence_uri.py`. This service will not pick one. `GET /v1/evidence/{hash}` serves the bytes for this process.
- The owner-only evidence replacement on the contract is not exposed. After the first vote of a round the commitment is frozen.
- This API does not take deposits, submit disputes, or pay claims.
- It does not predict which markets will be disputed. `scripts/rule_linter.py` is a separate authoring checklist and is not called.
- Temperature 0 and seed 1114 are required by the contract. They do not promise that a live model returns the same bytes on every call. The mock is deterministic only because the outcome is scripted.
- The sports two-source rule lives in the shipped system prompt. The mock panel does not apply it.
