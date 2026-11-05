# Published evidence bundles

Files are named `<sha256(evidence)>.json` where `evidence` is the exact
string embedded in the VERDICT/1 request (after the header line).
`commit_evidence` on-chain stores this hash; anyone can recompute it from these files.
