#!/usr/bin/env python3
"""NyaaDIYPC-MCP: build image → push to private registry (NyaaDockerHUB).

Usage:
  python rebuild.py              # build + push + registry cleanup + local cleanup
  python rebuild.py --no-cache   # force full rebuild without Docker layer cache
  python rebuild.py --skip-push  # local build only (offline / debugging)

Registry credentials read from .env (PRIVATE_DOCKER_REGISTRY_HOST).
Never hardcoded in this file. Output masks registry address as <PRIVATE_REGISTRY>.

⚠️  rebuild.py 只构建推送，不启动容器（工作空间铁律）。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib import request

PROJECT = "nyaadiypc-mcp"
IMAGE = "nyaadiypc-mcp"
RETRY_MAX = 3
RETRY_DELAY = 2  # seconds


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    env = {}
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        print("[ERROR] .env not found. Cannot proceed without registry config.")
        sys.exit(1)
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            env[k] = v
    return env


def mask(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "<PRIVATE_REGISTRY>")
    return text


def run(cmd: list[str], secrets: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"  -> {' '.join(mask(str(x), secrets) for x in cmd)}")
    return subprocess.run(cmd, **kwargs)


def get_git_sha(length: int = 7) -> str:
    cp = subprocess.run(
        ["git", "rev-parse", f"--short={length}", "HEAD"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parent,
    )
    if cp.returncode != 0:
        print("[ERROR] Not a git repository or no commits.")
        sys.exit(1)
    return cp.stdout.strip()


def registry_health(registry_url: str, secrets: list[str]) -> bool:
    try:
        req = request.Request(f"{registry_url}/v2/")
        with request.urlopen(req, timeout=5) as resp:
            print(f"Registry OK (status {resp.status})")
            return True
    except Exception as e:
        print(f"[WARN] Registry health check failed: {mask(str(e), secrets)}")
        return False


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def docker_build(host: str, sha: str, no_cache: bool, secrets: list[str]):
    tags = [f"{host}/{IMAGE}:{sha}", f"{host}/{IMAGE}:latest"]
    cmd = ["docker", "build", "-f", "Dockerfile"]
    if no_cache:
        cmd.append("--no-cache")
    for t in tags:
        cmd += ["-t", t]
    cmd.append(".")
    cp = run(cmd, secrets)
    if cp.returncode != 0:
        print("[ERROR] Docker build failed.")
        sys.exit(1)
    print("Build OK")


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------

def docker_push(host: str, tag: str, secrets: list[str]):
    full = f"{host}/{IMAGE}:{tag}"
    for attempt in range(1, RETRY_MAX + 1):
        cp = run(["docker", "push", full], secrets)
        if cp.returncode == 0:
            print(f"Push OK  {tag}")
            return
        print(f"Push failed ({tag}, attempt {attempt}/{RETRY_MAX})")
        if attempt < RETRY_MAX:
            time.sleep(RETRY_DELAY)
    print(f"[ERROR] Push exhausted retries for {tag}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# registry cleanup
# ---------------------------------------------------------------------------

def registry_cleanup(registry_url: str, host: str, sha: str, secrets: list[str]):
    print("Registry cleanup (keep-only-latest)...")
    try:
        req = request.Request(f"{registry_url}/v2/{IMAGE}/tags/list")
        with request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        all_tags = data.get("tags") or []
    except Exception as e:
        print(f"[WARN] Cannot list registry tags: {mask(str(e), secrets)}")
        return

    keep = {sha, "latest"}
    obsolete = [t for t in all_tags if t not in keep]
    if not obsolete:
        print("  No obsolete remote tags.")
        return

    for tag in obsolete:
        try:
            head_req = request.Request(
                f"{registry_url}/v2/{IMAGE}/manifests/{tag}",
                method="HEAD",
            )
            with request.urlopen(head_req, timeout=10) as resp:
                digest = resp.headers.get("Docker-Content-Digest", "")
            if digest:
                del_req = request.Request(
                    f"{registry_url}/v2/{IMAGE}/manifests/{digest}",
                    method="DELETE",
                )
                with request.urlopen(del_req, timeout=10) as resp:
                    if resp.status in (200, 202):
                        print(f"  Deleted {tag}")
                    else:
                        print(f"  Delete {tag} -> HTTP {resp.status}")
        except Exception as e:
            print(f"  Skip {tag}: {mask(str(e), secrets)}")


# ---------------------------------------------------------------------------
# local cleanup
# ---------------------------------------------------------------------------

def local_cleanup(host: str, sha: str, secrets: list[str]):
    cp = subprocess.run(
        ["docker", "images", f"{host}/{IMAGE}", "--format", "{{.Tag}}"],
        capture_output=True, text=True,
    )
    if cp.returncode != 0:
        return
    keep = {sha, "latest"}
    for tag in cp.stdout.strip().splitlines():
        tag = tag.strip()
        if tag and tag not in keep:
            subprocess.run(["docker", "rmi", "-f", f"{host}/{IMAGE}:{tag}"],
                           capture_output=True)

    subprocess.run(
        ["docker", "image", "prune", "-f", "--filter", f"label=project={PROJECT}"],
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# env push (optional convenience)
# ---------------------------------------------------------------------------

MACMINI_HOST = "U-MacMini-1"
MACMINI_PORT = "22141"
MACMINI_PROJECT_DIR = "/root/DockerContainer/NyaaDIYPC-MCP"


def push_env(secrets: list[str]):
    """scp .env to macmini so the remote container picks up new variables."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        print("[WARN] .env not found — skipping env push.")
        return

    cmd = [
        "scp", "-P", MACMINI_PORT,
        str(env_path),
        f"{MACMINI_HOST}:{MACMINI_PROJECT_DIR}/.env",
    ]
    print(f"  -> scp .env → {MACMINI_HOST}:{MACMINI_PROJECT_DIR}/")
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode != 0:
        print(f"[WARN] env push failed (macmini may be offline): {cp.stderr.strip()}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=f"Rebuild {PROJECT}")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--skip-push", action="store_true")
    args = parser.parse_args()

    env = load_env()
    host = env.get("PRIVATE_DOCKER_REGISTRY_HOST", "")
    url = env.get("PRIVATE_DOCKER_REGISTRY_URL", "")
    if not host:
        print("[ERROR] PRIVATE_DOCKER_REGISTRY_HOST not set in .env")
        sys.exit(1)
    if not url:
        url = f"http://{host}"

    secrets = [host, url]
    sha = get_git_sha()

    print(f"=== {PROJECT} rebuild ===")
    print(f"  SHA:       {sha}")
    print(f"  Registry:  <PRIVATE_REGISTRY>")
    print()

    if not args.skip_push:
        registry_health(url, secrets)

    docker_build(host, sha, args.no_cache, secrets)

    if args.skip_push:
        print("--skip-push: done (local build only)")
        return

    docker_push(host, sha, secrets)
    docker_push(host, "latest", secrets)
    registry_cleanup(url, host, sha, secrets)
    local_cleanup(host, sha, secrets)
    push_env(secrets)

    print(f"\n=== {PROJECT} rebuild done ===")
    print(f"Image: <PRIVATE_REGISTRY>/{IMAGE}:{sha}")
    print(f"       <PRIVATE_REGISTRY>/{IMAGE}:latest")


if __name__ == "__main__":
    main()
