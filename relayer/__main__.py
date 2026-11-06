"""CLI: python -m relayer  or  python scripts/relayer.py"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from relayer import CONTRACT_DEFAULT, ROOT
from relayer.chain import NearCli, RpcClient, dump_args, urllib_request
from relayer.config import ConfigError, load_config, materialize_credentials
from relayer.panel import PanelClient
from relayer.publish import GitPublisher
from relayer.service import RelayEnv, load_markets_file, run_pass


def _refuse_network(url: str):
    raise RuntimeError(f"dry-run does not fetch {url}")


def http_fetch(url: str) -> tuple[int, bytes]:
    return urllib_request(
        url,
        headers={"User-Agent": "VerdictRelayer/0.1 (github.com/Baheet18/verdict)"},
        method="GET",
        timeout=60,
    )


def _print_register(account: str, contract: str, out: str | None) -> int:
    payload = dump_args({"account": account})
    args_path = out or "register_relayer.json"
    if out:
        Path(out).write_bytes(payload)
    print(payload.decode("utf-8"))
    if not out:
        print()
        print(f"Save that JSON as {args_path} (UTF-8, no BOM) before the near command.")
    print()
    print("The protocol owner sends register_relayer once. The relayer key cannot register itself.")
    print("Current testnet owner is baheet.testnet. Sign with that owner account.")
    print()
    print(
        "near contract call-function as-transaction "
        f"{contract} register_relayer file-args {args_path} "
        "prepaid-gas '30.0 Tgas' attached-deposit '0 NEAR' "
        "sign-as baheet.testnet network-config testnet sign-with-keychain send"
    )
    print()
    print("PowerShell: Set-Content -Encoding utf8 writes a BOM and near-cli will reject the JSON.")
    print("Use [System.IO.File]::WriteAllText((Join-Path (Get-Location) 'register_relayer.json'), '{\"account\":\"" + account + "\"}')")
    return 0


def _emit(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)


def _build_env(cfg, *, markets_file: str | None, now_ns: int | None, creds_path: str | None):
    fixture_paths = []
    fixture_dir = Path(cfg.fixture_dir)
    if fixture_dir.exists():
        fixture_paths.append(fixture_dir)
    real_panel = ROOT / "contract" / "testdata" / "real_panel.json"
    if real_panel.is_file():
        fixture_paths.append(real_panel)
    panel = PanelClient(live=not cfg.dry_run, fixture_paths=fixture_paths)

    if markets_file:
        file_now, markets = load_markets_file(markets_file)
        clock = file_now if now_ns is None else now_ns

        def get_market(market_id, _markets=markets):
            market = _markets.get(market_id)
            return json.loads(json.dumps(market)) if market is not None else None

        def market_ids(_markets=markets):
            if cfg.market_ids is not None:
                return [i for i in cfg.market_ids if i in _markets]
            return sorted(_markets)

        def send(method, args):
            raise RuntimeError("offline markets file cannot send transactions")

        return RelayEnv(
            dry_run=True,
            now_ns=clock,
            get_market=get_market,
            market_ids=market_ids,
            send=send,
            fetch=_refuse_network,
            publish=None,
            panel=panel,
            max_rounds=cfg.max_rounds_per_pass,
        )

    rpc = RpcClient(cfg.rpc_url, cfg.contract)
    if cfg.dry_run:
        clock = rpc.now_ns if now_ns is None else (lambda: now_ns)

        def send(method, args):
            raise RuntimeError("dry-run tried to send a transaction")

        return RelayEnv(
            dry_run=True,
            now_ns=clock,
            get_market=rpc.get_market,
            market_ids=lambda: rpc.iter_ids(cfg.market_ids, cfg.max_markets),
            send=send,
            fetch=_refuse_network,
            publish=None,
            panel=panel,
            max_rounds=cfg.max_rounds_per_pass,
        )

    if not cfg.signer_account:
        raise ConfigError("live mode needs VERDICT_SIGNER_ACCOUNT")
    if not shutil.which(cfg.near_bin):
        raise ConfigError(f"near CLI not found ({cfg.near_bin}). Install near-cli-rs or set VERDICT_NEAR_BIN.")
    if not rpc.is_relayer(cfg.signer_account):
        raise ConfigError(
            f"{cfg.signer_account} is not a relayer on {cfg.contract}. "
            "The owner runs register_relayer once: python scripts/relayer.py print-register --account "
            f"{cfg.signer_account}"
        )
    near = NearCli(
        near_bin=cfg.near_bin,
        contract=cfg.contract,
        signer=cfg.signer_account,
        network=cfg.network,
        credentials_file=creds_path or "",
    )
    publisher = GitPublisher(
        Path(cfg.evidence_workdir),
        remote=cfg.evidence_remote,
        github_repo=cfg.evidence_repo,
        push=cfg.evidence_push,
        verify_http=cfg.verify_http,
        http_get=http_fetch,
    )
    return RelayEnv(
        dry_run=False,
        now_ns=rpc.now_ns,
        get_market=rpc.get_market,
        market_ids=lambda: rpc.iter_ids(cfg.market_ids, cfg.max_markets),
        send=near.transact,
        fetch=http_fetch,
        publish=publisher.publish,
        panel=panel,
        max_rounds=cfg.max_rounds_per_pass,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verdict testnet relayer (commit_evidence, submit_verdict, finalize).")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "print-register"))
    parser.add_argument("--once", action="store_true", help="single pass (default)")
    parser.add_argument("--loop", action="store_true", help="poll until interrupted")
    parser.add_argument("--config", default=None, help="JSON config path; secrets are rejected if present in the file")
    parser.add_argument("--mode", choices=("dry-run", "live"), default=None)
    parser.add_argument("--markets-file", default=None, help="offline get_market fixtures; implies dry-run")
    parser.add_argument("--now-ns", type=int, default=None, help="simulated clock for dry-run only")
    parser.add_argument("--account", default=None, help="relayer account for print-register")
    parser.add_argument("--out", default=None, help="write register_relayer args to this file (UTF-8, no BOM)")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.mode:
        cfg.mode = args.mode
        cfg.verify_http = cfg.mode == "live" and cfg.evidence_push
    if args.command == "print-register":
        account = args.account or cfg.signer_account
        if not account:
            print("pass --account YOUR_RELAYER.testnet", file=sys.stderr)
            return 2
        return _print_register(account, cfg.contract or CONTRACT_DEFAULT, args.out)
    if args.markets_file and cfg.mode == "live":
        print("--markets-file is offline only. Live mode reads the contract.", file=sys.stderr)
        return 2
    if args.now_ns is not None and cfg.mode == "live":
        print("--now-ns is refused in live mode so the dispute window follows chain time.", file=sys.stderr)
        return 2
    if not args.loop:
        args.once = True

    creds_path = None
    delete_creds = False
    try:
        if cfg.mode == "live":
            try:
                creds, delete_creds = materialize_credentials(cfg)
            except ConfigError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            creds_path = str(creds)
        while True:
            try:
                env = _build_env(cfg, markets_file=args.markets_file, now_ns=args.now_ns, creds_path=creds_path)
                reports = run_pass(env)
            except (ConfigError, RuntimeError) as exc:
                print(str(exc), file=sys.stderr)
                return 2
            for report in reports:
                _emit(report)
            errors = sum(1 for r in reports if r.get("level") == "error")
            summary = {
                "summary": True,
                "markets": len(reports),
                "errors": errors,
                "by_level": {},
                "dry_run": cfg.dry_run,
                "contract": cfg.contract,
            }
            for report in reports:
                summary["by_level"][report.get("level")] = summary["by_level"].get(report.get("level"), 0) + 1
            _emit(summary)
            if not args.loop:
                return 1 if errors else 0
            time.sleep(cfg.poll_interval_s)
    finally:
        if delete_creds and creds_path:
            path = Path(creds_path)
            path.unlink(missing_ok=True)
            path.parent.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
