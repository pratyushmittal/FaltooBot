#!/usr/bin/env python3
import argparse
import json
import sys
from urllib.request import urlopen
from urllib.error import URLError, HTTPError


def fetch_json(url: str):
    with urlopen(url, timeout=5) as resp:
        return json.load(resp)


def main():
    parser = argparse.ArgumentParser(
        description="Probe a Chrome DevTools Protocol endpoint"
    )
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:9222", help="Base CDP HTTP endpoint"
    )
    args = parser.parse_args()

    base = args.endpoint.rstrip("/")
    try:
        version = fetch_json(f"{base}/json/version")
        targets = fetch_json(f"{base}/json/list")
    except HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        sys.exit(1)
    except URLError as exc:
        print(f"Connection error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("== Browser ==")
    print(f"Browser: {version.get('Browser', 'unknown')}")
    print(f"Protocol-Version: {version.get('Protocol-Version', 'unknown')}")
    print(f"User-Agent: {version.get('User-Agent', 'unknown')}")
    print(f"webSocketDebuggerUrl: {version.get('webSocketDebuggerUrl', 'unknown')}")
    print()
    print(f"== Targets ({len(targets)}) ==")
    for idx, target in enumerate(targets, start=1):
        print(
            f"{idx}. [{target.get('type', '?')}] {target.get('title', '')} -> {target.get('url', '')}"
        )
        ws = target.get("webSocketDebuggerUrl")
        if ws:
            print(f"   WS: {ws}")


if __name__ == "__main__":
    main()
