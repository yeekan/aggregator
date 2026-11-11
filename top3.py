#!/usr/bin/env python3
"""Rank Aggregator output proxies by real Mihomo delay and emit the top 3.

Reads output/clash.yaml (produced by subscribe/process.py), starts the
bundled Mihomo binary with those proxies, queries each proxy's delay to
https://www.google.com/generate_204 via the external controller API,
and writes:
  - output/top3.json : machine-readable result with delays
  - output/top3.yaml : a minimal ready-to-import Clash config with only the top 3
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
INPUT = os.path.join(HERE, "output", "clash.yaml")
OUT_JSON = os.path.join(HERE, "output", "top3.json")
OUT_YAML = os.path.join(HERE, "output", "top3.yaml")
TEST_URL = "https://www.google.com/generate_204"
TIMEOUT_MS = 5000
CONTROLLER = "127.0.0.1:9091"
MIXED_PORT = 7891
WORKDIR = os.path.join(HERE, ".top3work")

sys.path.insert(0, os.path.join(HERE, ".venv", "lib", "python3.12", "site-packages"))
import yaml  # noqa: E402


def load_proxies():
    with open(INPUT, encoding="utf8") as f:
        data = yaml.load(f, Loader=yaml.FullLoader)
    proxies = data.get("proxies") or []
    seen, uniq = set(), []
    for p in proxies:
        name = p.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        uniq.append(p)
    return uniq


def delay_of(name):
    q = urllib.parse.quote(name, safe="")
    url = (
        f"http://{CONTROLLER}/proxies/{q}/delay"
        f"?timeout={TIMEOUT_MS}&url={urllib.parse.quote(TEST_URL)}"
    )
    try:
        with urllib.request.urlopen(url, timeout=(TIMEOUT_MS + 2000) / 1000) as r:
            data = json.loads(r.read().decode("utf8"))
        d = data.get("delay", -1)
        return d if isinstance(d, int) and d > 0 else -1
    except Exception:
        return -1


def main():
    proxies = load_proxies()
    print(f"loaded {len(proxies)} unique proxies from {INPUT}", flush=True)
    if not proxies:
        print("no proxies, abort", flush=True)
        return 1

    os.makedirs(WORKDIR, exist_ok=True)
    config = {
        "mixed-port": MIXED_PORT,
        "external-controller": CONTROLLER,
        "mode": "Rule",
        "log-level": "silent",
        "proxies": proxies,
    }
    cfg_path = os.path.join(WORKDIR, "config.yaml")
    with open(cfg_path, "w", encoding="utf8") as f:
        yaml.dump(config, f, allow_unicode=True)

    clash_bin = os.path.join(HERE, "clash", "clash-linux-amd")
    os.chmod(clash_bin, 0o755)
    subprocess.run(["pkill", "-f", "clash-linux-amd"], capture_output=True)
    time.sleep(1)
    proc = subprocess.Popen(
        [clash_bin, "-d", WORKDIR, "-f", cfg_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(7)
        with ThreadPoolExecutor(max_workers=32) as ex:
            delays = list(ex.map(delay_of, [p["name"] for p in proxies]))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    ranked = sorted(
        ({"proxy": p, "delay_ms": d} for p, d in zip(proxies, delays) if d > 0),
        key=lambda x: x["delay_ms"],
    )
    print(f"alive: {len(ranked)}/{len(proxies)}", flush=True)
    top = ranked[:3]
    result = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "tested": len(proxies),
        "alive": len(ranked),
        "test_url": TEST_URL,
        "top3": [
            {
                "name": t["proxy"].get("name"),
                "type": t["proxy"].get("type"),
                "server": t["proxy"].get("server"),
                "port": t["proxy"].get("port"),
                "delay_ms": t["delay_ms"],
            }
            for t in top
        ],
    }
    with open(OUT_JSON, "w", encoding="utf8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if top:
        names = [t["proxy"]["name"] for t in top]
        top_config = {
            "mixed-port": 7890,
            "external-controller": "127.0.0.1:9090",
            "mode": "Rule",
            "log-level": "info",
            "proxies": [t["proxy"] for t in top],
            "proxy-groups": [
                {"name": "🚀 最快3节点", "type": "select", "proxies": names}
            ],
            "rules": ["MATCH,🚀 最快3节点"],
        }
        with open(OUT_YAML, "w", encoding="utf8") as f:
            yaml.dump(top_config, f, allow_unicode=True)
    for t in top:
        print(f"TOP {t['delay_ms']}ms {t['proxy'].get('type')} {t['proxy'].get('name')}", flush=True)
    return 0 if top else 2


if __name__ == "__main__":
    raise SystemExit(main())
