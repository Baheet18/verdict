#!/usr/bin/env python3
"""Fetch Docker Hub tags/digests for pinned NEAR AI / dstack images and print an allowlist DIFF.

Does NOT modify scripts/expected_measurements.json. Human must approve any pin change.
"""
from __future__ import annotations
import json, pathlib, sys, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
ALLOW = ROOT / "scripts" / "expected_measurements.json"
OUT = ROOT / "out" / "check_new_digest" / "diff.json"

# Repos we care about (tag → what we expect to compare)
WATCH = [
    ("nearaidev", "compose-manager", ["latest", "prod", "prod-20261029-be649f8"]),
    ("nearaidev", "compose-manager-launcher", ["latest", "prod"]),
]

UA = {"User-Agent": "VerdictDigestCheck/0.1 (research; github.com/Baheet18/verdict)"}


def hub_tags(user: str, repo: str, page_size: int = 100):
    url = f"https://hub.docker.com/v2/repositories/{user}/{repo}/tags?page_size={page_size}&ordering=-last_updated"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


def digests_for_tag(tag_obj) -> list[str]:
    out = []
    for img in tag_obj.get("images") or []:
        d = img.get("digest")
        if d and d.startswith("sha256:"):
            out.append(d)
    # some responses put digest on the tag itself
    d = tag_obj.get("digest")
    if d and d.startswith("sha256:") and d not in out:
        out.append(d)
    return out


def main():
    allow = json.loads(ALLOW.read_text())
    pinned = set(allow["shared_host_images"]["docker_image_digests"])
    report = {
        "pinned_digests": sorted(pinned),
        "watched": [],
        "new_not_pinned": [],
        "pinned_still_present": [],
        "errors": [],
        "note": "DIFF ONLY — do not auto-allowlist. Demo/scoreboard use cached verified runs.",
    }
    for user, repo, want_tags in WATCH:
        try:
            data = hub_tags(user, repo)
        except Exception as e:
            report["errors"].append({"repo": f"{user}/{repo}", "error": str(e)[:200]})
            continue
        tags = {t["name"]: t for t in data.get("results") or []}
        entry = {"repo": f"{user}/{repo}", "tags": {}}
        for name in want_tags:
            t = tags.get(name)
            if not t:
                # scan all returned for prefix match
                matches = [n for n in tags if n == name or n.startswith(name)]
                entry["tags"][name] = {"found": False, "near": matches[:5]}
                continue
            digs = digests_for_tag(t)
            entry["tags"][name] = {
                "found": True,
                "last_updated": t.get("last_updated"),
                "digests": digs,
                "on_allowlist": [d for d in digs if d in pinned],
                "not_on_allowlist": [d for d in digs if d not in pinned],
            }
            for d in digs:
                if d in pinned:
                    report["pinned_still_present"].append(f"{user}/{repo}:{name}@{d}")
                else:
                    report["new_not_pinned"].append(f"{user}/{repo}:{name}@{d}")
        # also list any other recent tags whose digest isn't pinned
        for name, t in list(tags.items())[:15]:
            if name in want_tags:
                continue
            for d in digests_for_tag(t):
                if d not in pinned:
                    report["new_not_pinned"].append(f"{user}/{repo}:{name}@{d}")
        report["watched"].append(entry)

    # unique + sort
    report["new_not_pinned"] = sorted(set(report["new_not_pinned"]))
    report["pinned_still_present"] = sorted(set(report["pinned_still_present"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print("=== check_new_digest (DIFF ONLY; no allowlist write) ===")
    print(f"pinned digests: {len(pinned)}")
    print(f"still present on Hub tags: {len(report['pinned_still_present'])}")
    print(f"new / unpinned on watched tags: {len(report['new_not_pinned'])}")
    for x in report["new_not_pinned"][:20]:
        print(f"  NEW  {x}")
    if report["errors"]:
        print("errors:", report["errors"])
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
