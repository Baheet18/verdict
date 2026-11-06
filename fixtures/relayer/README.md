# Offline relayer fixtures

`offline_markets.json` is a synthetic clock and a set of `get_market` shapes for
`python scripts/relayer.py --once --markets-file fixtures/relayer/offline_markets.json`.

It is not chain state. It has no signatures and no transaction hashes.
The dry-run prints the action for each id and does not fetch sources or call NEAR AI.

Signed votes, when a request's bytes match a previous run, live in
`fixtures/relayer_cache/` or in `contract/testdata/real_panel.json`. A cache hit
requires the signed text to equal `model:sha256(request):sha256(response)`.
