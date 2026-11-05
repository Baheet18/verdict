#!/usr/bin/env python3
"""Publish an evidence bundle under fixtures/evidence/<sha256>.json (content-addressed).

The contract stores only the hash via commit_evidence; anyone must be able to fetch the
bundle whose sha256 equals that hash. This script writes the published file and prints
the hash to commit on-chain.
"""
from __future__ import annotations
import argparse, hashlib, json, pathlib, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "fixtures" / "evidence"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="JSON evidence bundle file (or .txt evidence body)")
    ap.add_argument("--body-field", default="evidence", help="If JSON, field containing the exact bytes that were hashed into the request")
    args = ap.parse_args()
    src = pathlib.Path(args.path)
    raw = src.read_bytes()
    # Prefer hashing the evidence string field if present (matches request evidence_bundle_hash)
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and args.body_field in obj and isinstance(obj[args.body_field], str):
            body = obj[args.body_field].encode()
            meta = obj
        else:
            body, meta = raw, None
    except Exception:
        body, meta = raw, None
    h = hashlib.sha256(body).hexdigest()
    DEST.mkdir(parents=True, exist_ok=True)
    out = DEST / f"{h}.json"
    if meta is not None:
        pub = dict(meta)
        pub["evidence_bundle_hash"] = h
        out.write_text(json.dumps(pub, indent=1))
    else:
        out.write_bytes(body)
    print(h)
    print(f"published {out}")

if __name__ == "__main__":
    main()
