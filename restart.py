#!/usr/bin/env python3
"""NyaaDIYPC-MCP restart script (macmini side).

Pull latest image → restart container → clean up dangling images.

Principle: pull FIRST, then stop — minimizes client disconnection time.

Usage:
  python restart.py              # standard restart
  python restart.py --no-pull    # skip pull (restart with existing image)
"""

import argparse
import os
import subprocess
from pathlib import Path

PROJECT = "nyaadiypc-mcp"
CONTAINER = "nyaadiypc-mcp"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  -> {' '.join(cmd)}")
    return subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(description=f"Restart {PROJECT}")
    parser.add_argument("--no-pull", action="store_true", help="Skip docker compose pull")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    os.chdir(here)

    # ---- 1. pull first ----
    if not args.no_pull:
        print("[1/4] Pulling latest image...")
        cp = run(["docker", "compose", "pull"], check=False)
        if cp.returncode != 0:
            print("[WARN] Pull failed, continuing with existing image...")

    # ---- 2. stop ----
    print("[2/4] Stopping container...")
    run(["docker", "compose", "down"])

    # ---- 3. start ----
    print("[3/4] Starting container...")
    run(["docker", "compose", "up", "-d"])

    # ---- 4. prune dangling ----
    print("[4/4] Cleaning up dangling images...")
    run(["docker", "image", "prune", "-f"])

    # ---- status ----
    print(f"\n=== {PROJECT} status ===")
    run(["docker", "ps", "--filter", f"name={CONTAINER}",
         "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"])


if __name__ == "__main__":
    main()
