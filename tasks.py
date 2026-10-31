"""Cross-platform task runner for Kavach (Windows, macOS, Linux).

Usage:
    python tasks.py test               # Run the complete test suite (71 tests)
    python tasks.py demo               # Run interactive terminal demo
    python tasks.py demo --fast        # Fast automated verification (~5s)
    python tasks.py demo --video       # Timed 3-minute video presentation (~2m 40s)
    python tasks.py eval               # Run adversarial benchmark (40 trials)
    python tasks.py clean              # Clean cache and temporary files
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"


def run_cmd(args: list[str]) -> int:
    """Run command and stream output."""
    print(f"\n[tasks.py] > {' '.join(args)}\n")
    res = subprocess.run(args)
    return res.returncode


def cmd_test(extra_args: list[str]) -> int:
    return run_cmd([sys.executable, "-m", "pytest", "tests/", "-v"] + extra_args)


def cmd_demo(extra_args: list[str]) -> int:
    return run_cmd([sys.executable, "demo.py"] + extra_args)


def cmd_eval(extra_args: list[str]) -> int:
    return run_cmd([sys.executable, "eval/run_eval.py"] + extra_args)


def cmd_clean() -> int:
    root = Path(__file__).parent
    patterns = ["__pycache__", ".pytest_cache", "*.egg-info", "*.db", "*.db-wal", "*.db-shm"]
    removed = 0
    for pattern in patterns:
        for p in root.rglob(pattern):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
            elif p.is_file():
                p.unlink(missing_ok=True)
                removed += 1
    print(f"[tasks.py] Cleaned {removed} temporary items.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Kavach Task Runner")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("test", help="Run full test suite (pytest)")
    demo_p = subparsers.add_parser("demo", help="Run interactive demo")
    demo_p.add_argument("--fast", action="store_true", help="Fast mode (~5s)")
    demo_p.add_argument("--video", action="store_true", help="3-min video mode (~2m 40s)")
    demo_p.add_argument("--pace", type=float, default=None, help="Custom pace multiplier")

    eval_p = subparsers.add_parser("eval", help="Run adversarial evaluation harness")
    eval_p.add_argument("--trials", type=int, default=20, help="Trials per condition")
    eval_p.add_argument("--live", action="store_true", help="Use live LLM")

    subparsers.add_parser("clean", help="Clean cache files")

    args, unknown = parser.parse_known_args()

    if args.command == "test":
        sys.exit(cmd_test(unknown))
    elif args.command == "demo":
        extra = []
        if args.fast:
            extra.append("--fast")
        if args.video:
            extra.append("--video")
        if args.pace is not None:
            extra.extend(["--pace", str(args.pace)])
        sys.exit(cmd_demo(extra + unknown))
    elif args.command == "eval":
        extra = ["--trials", str(args.trials)]
        if args.live:
            extra.append("--live")
        sys.exit(cmd_eval(extra + unknown))
    elif args.command == "clean":
        sys.exit(cmd_clean())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
