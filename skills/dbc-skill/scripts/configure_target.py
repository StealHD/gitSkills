#!/usr/bin/env python3
"""Persist the runtime backend target for dbc-skill."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "api-targets.local.json"


def normalize_base_url(host: str, port: int, scheme: str, base_path: str) -> str:
    host = host.strip()
    if not host:
        raise SystemExit("host is required")
    path = "/" + base_path.strip("/") if base_path.strip("/") else ""
    return f"{scheme}://{host}:{port}{path}"


def load_config(path: Path) -> dict:
    if not path.exists():
        return {"default_target": "dbc-pod", "targets": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid target config {path}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="Backend host or IP")
    parser.add_argument("--port", required=True, type=int, help="Backend port")
    parser.add_argument("--scheme", choices=["http", "https"], default="http")
    parser.add_argument("--base-path", default="", help="Optional API base path prefix")
    parser.add_argument("--config", default=None, help="Path to api-targets.local.json")
    args = parser.parse_args()

    path = Path(args.config).expanduser() if args.config else default_config_path()
    config = load_config(path)
    config["default_target"] = "dbc-pod"
    targets = config.setdefault("targets", {})
    targets["dbc-pod"] = {
        "base_url": normalize_base_url(args.host, args.port, args.scheme, args.base_path),
        "description": "Configured runtime backend for dbc-skill.",
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "config": str(path), "target": targets["dbc-pod"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
