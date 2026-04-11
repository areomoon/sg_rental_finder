#!/usr/bin/env python3
"""
SG Rental Finder — CLI Entry Point

用法：
  python run.py --now        立即執行（收集 + 篩選 + 排名 + 發送郵件）
  python run.py --test       測試模式（收集 + 排名，不發送）
  python run.py --preview    預覽摘要（顯示 top 10，不發送）
  python run.py --auth-gmail 只執行 Gmail OAuth 流程（初次設定用）
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(override=True)

import yaml


def load_settings() -> dict:
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def cmd_now(settings: dict) -> None:
    from src.digest import run_digest
    run_digest(settings=settings, send=True, preview=False)


def cmd_test(settings: dict) -> None:
    from src.digest import run_digest
    print("🧪 測試模式：收集 + 排名，不發送郵件")
    run_digest(settings=settings, send=False, preview=True)


def cmd_preview(settings: dict) -> None:
    from src.digest import run_digest
    print("👁️  預覽模式：顯示 top 10，不發送")
    run_digest(settings=settings, send=False, preview=True)


def cmd_auth_gmail() -> None:
    """Trigger Gmail OAuth flow and save token.json."""
    print("🔐 Gmail OAuth 設定")
    print("=" * 50)
    from src.collectors.gmail_alerts import GmailAlertsCollector
    collector = GmailAlertsCollector()
    try:
        collector.authenticate()
        print("\n✅ Gmail OAuth 完成！token.json 已儲存至 config/")
        print("   之後執行 python run.py --test 測試完整流程")
    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ OAuth 失敗：{e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="SG Rental Finder — 雙週租屋摘要 Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--now", action="store_true", help="立即執行（收集 + 發送郵件）")
    group.add_argument("--test", action="store_true", help="測試模式（不發送郵件）")
    group.add_argument("--preview", action="store_true", help="預覽 top 10（不發送）")
    group.add_argument("--auth-gmail", action="store_true", help="Gmail OAuth 初次設定")

    args = parser.parse_args()
    settings = load_settings()

    if args.now:
        cmd_now(settings)
    elif args.test:
        cmd_test(settings)
    elif args.preview:
        cmd_preview(settings)
    elif args.auth_gmail:
        cmd_auth_gmail()


if __name__ == "__main__":
    main()
