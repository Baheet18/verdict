# Cached panel votes

Drop a JSON file here to reuse a real NEAR AI vote without calling the API again.
One object, or `{"votes": [ ... ]}`, with:

- `model`
- `request_raw` (the exact chat-completions body)
- `response_raw` (the exact HTTP body)
- `signature` (65-byte hex)
- `text` equal to `model:sha256(request_raw):sha256(response_raw)`

The relayer submits a cached vote only when it rebuilds that same `request_raw`.
A different evidence bundle is a cache miss. Do not put API keys or NEAR private keys in this directory.
