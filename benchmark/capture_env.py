"""
Environment capture for reproducibility.

Dumps host hardware/OS, Python, Docker engine resources, container images
(with digests), and key benchmark dependency versions into a JSON document.
run_trials.py writes this as environment.json alongside every campaign so
any reported number can be traced back to the exact machine configuration.
"""

import json
import os
import platform
import subprocess
import sys
import time
from importlib import metadata

KEY_PACKAGES = [
    "httpx",
    "grpcio",
    "grpcio-tools",
    "protobuf",
    "numpy",
    "scipy",
    "matplotlib",
]


def _run(cmd: list, cwd: str = None) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, cwd=cwd
        )
        return out.stdout.strip() if out.returncode == 0 else f"<error: {out.stderr.strip()[:200]}>"
    except Exception as exc:  # docker missing, timeout, etc.
        return f"<unavailable: {exc}>"


def _run_json(cmd: list, cwd: str = None):
    raw = _run(cmd, cwd=cwd)
    if raw.startswith("<"):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # `docker compose images --format json` sometimes emits one JSON object per line
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return items or raw


def capture(compose_dir: str = None) -> dict:
    env = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count_logical": os.cpu_count(),
            "python": sys.version,
        },
        "docker": {},
        "packages": {},
    }

    docker_info = _run_json(["docker", "info", "--format", "{{json .}}"])
    if isinstance(docker_info, dict):
        env["docker"]["engine"] = {
            k: docker_info.get(k)
            for k in ("ServerVersion", "OperatingSystem", "OSType", "Architecture", "NCPU", "MemTotal")
        }
    else:
        env["docker"]["engine"] = docker_info

    if compose_dir:
        env["docker"]["compose_dir"] = os.path.abspath(compose_dir)
        env["docker"]["images"] = _run_json(
            ["docker", "compose", "images", "--format", "json"], cwd=compose_dir
        )
        env["docker"]["containers"] = _run_json(
            ["docker", "compose", "ps", "--format", "json"], cwd=compose_dir
        )

    for pkg in KEY_PACKAGES:
        try:
            env["packages"][pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            env["packages"][pkg] = "<not installed>"

    return env


if __name__ == "__main__":
    compose = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(capture(compose), indent=2))
