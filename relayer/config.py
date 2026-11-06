"""Configuration from a JSON file plus the environment. Secrets stay in the environment."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from relayer import CONTRACT_DEFAULT, GITHUB_REPO_DEFAULT, ROOT, RPC_DEFAULT

FORBIDDEN_FILE_KEYS = {
    "private_key", "secret_key", "near_private_key", "nearai_api_key", "seed_phrase", "api_key", "secret",
}


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    mode: str = "dry-run"
    contract: str = CONTRACT_DEFAULT
    network: str = "testnet"
    rpc_url: str = RPC_DEFAULT
    signer_account: str = ""
    credentials_file: str = ""
    near_bin: str = "near"
    evidence_repo: str = GITHUB_REPO_DEFAULT
    evidence_remote: str = "origin"
    evidence_workdir: str = ""
    evidence_push: bool = False
    verify_http: bool = False
    market_ids: list[int] | None = None
    poll_interval_s: int = 60
    max_markets: int = 500
    max_rounds_per_pass: int = 2
    fixture_dir: str = ""
    git_name: str = "verdict-relayer"
    git_email: str = "verdict-relayer@localhost"

    @property
    def dry_run(self) -> bool:
        return self.mode != "live"


def _reject_secrets(data: dict, where: str) -> None:
    for key in data:
        if key.lower() in FORBIDDEN_FILE_KEYS:
            raise ConfigError(f"{where} contains {key}; put secrets in the environment or a gitignored key file, not in config")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_config(path: str | None = None) -> Config:
    data: dict = {}
    if path:
        file = Path(path)
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"could not parse {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("config file must be a JSON object")
        _reject_secrets(data, path)
    cfg = Config(
        mode=os.environ.get("VERDICT_RELAYER_MODE", data.get("mode", "dry-run")),
        contract=os.environ.get("VERDICT_CONTRACT", data.get("contract", CONTRACT_DEFAULT)),
        network=os.environ.get("VERDICT_NETWORK", data.get("network", "testnet")),
        rpc_url=os.environ.get("VERDICT_RPC_URL", data.get("rpc_url", RPC_DEFAULT)),
        signer_account=os.environ.get("VERDICT_SIGNER_ACCOUNT", data.get("signer_account", "")),
        credentials_file=os.environ.get("NEAR_CREDENTIALS_FILE", ""),
        near_bin=os.environ.get("VERDICT_NEAR_BIN", data.get("near_bin", "near")),
        evidence_repo=os.environ.get("VERDICT_EVIDENCE_REPO", data.get("evidence_repo", GITHUB_REPO_DEFAULT)),
        evidence_remote=os.environ.get("VERDICT_EVIDENCE_REMOTE", data.get("evidence_remote", "origin")),
        evidence_workdir=os.environ.get("VERDICT_EVIDENCE_WORKDIR", data.get("evidence_workdir", str(ROOT))),
        evidence_push=_env_bool("VERDICT_EVIDENCE_PUSH", bool(data.get("evidence_push", False))),
        market_ids=_market_ids(os.environ.get("VERDICT_MARKET_IDS", data.get("market_ids"))),
        poll_interval_s=int(os.environ.get("VERDICT_POLL_INTERVAL_S", data.get("poll_interval_s", 60))),
        max_markets=int(os.environ.get("VERDICT_MAX_MARKETS", data.get("max_markets", 500))),
        max_rounds_per_pass=int(os.environ.get("VERDICT_MAX_ROUNDS_PER_PASS", data.get("max_rounds_per_pass", 2))),
        fixture_dir=os.environ.get("VERDICT_FIXTURE_DIR", data.get("fixture_dir", str(ROOT / "fixtures" / "relayer_cache"))),
        git_name=os.environ.get("VERDICT_GIT_USER_NAME", data.get("git_name", "verdict-relayer")),
        git_email=os.environ.get("VERDICT_GIT_USER_EMAIL", data.get("git_email", "verdict-relayer@localhost")),
    )
    if cfg.mode not in ("dry-run", "live"):
        raise ConfigError("VERDICT_RELAYER_MODE must be dry-run or live")
    if cfg.poll_interval_s < 5:
        cfg.poll_interval_s = 5
    if cfg.max_rounds_per_pass < 1:
        cfg.max_rounds_per_pass = 1
    cfg.verify_http = cfg.mode == "live" and cfg.evidence_push
    return cfg


def _market_ids(value) -> list[int] | None:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        return [int(v) for v in value]
    return [int(part) for part in str(value).split(",") if part.strip() != ""]


def materialize_credentials(cfg: Config) -> tuple[Path, bool]:
    """Return (path, delete_on_exit). Never logs the key."""
    if cfg.credentials_file:
        path = Path(cfg.credentials_file)
        if not path.is_file():
            raise ConfigError(f"NEAR_CREDENTIALS_FILE does not exist: {path}")
        return path, False
    public = os.environ.get("NEAR_PUBLIC_KEY", "").strip()
    private = os.environ.get("NEAR_PRIVATE_KEY", "").strip()
    if not public or not private:
        raise ConfigError("live mode needs NEAR_CREDENTIALS_FILE, or both NEAR_PUBLIC_KEY and NEAR_PRIVATE_KEY")
    if not public.startswith("ed25519:") or not private.startswith("ed25519:"):
        raise ConfigError("NEAR_PUBLIC_KEY and NEAR_PRIVATE_KEY must be ed25519: strings")
    directory = Path(tempfile.mkdtemp(prefix="verdict-relayer-key-"))
    path = directory / "access-key.json"
    path.write_bytes(json.dumps({"public_key": public, "private_key": private}).encode("utf-8"))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path, True
