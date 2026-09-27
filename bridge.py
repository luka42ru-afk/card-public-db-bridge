import json
import os
import sys
import urllib.request

ALLOWED_MODES = {"read", "write"}

def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: bridge.py <mode>")

    mode = sys.argv[1].strip().lower()
    if mode not in ALLOWED_MODES:
        raise SystemExit("unsupported mode")

    raw_config = (os.environ.get("BRIDGE_CONFIG") or "").strip()
    if not raw_config:
        raise SystemExit("bridge configuration is not available")

    config = json.loads(raw_config)
    request_config = config.get(mode)
    if not isinstance(request_config, dict):
        raise SystemExit("requested mode is not configured")

    url = str(request_config.get("url") or "")
    method = str(request_config.get("method") or "GET").upper()
    body = request_config.get("body")

    if not url.startswith("https://"):
        raise SystemExit("invalid endpoint configuration")
    if method not in {"GET", "POST"}:
        raise SystemExit("invalid method configuration")

    payload = None
    if method == "POST":
        payload = json.dumps(body if body is not None else {}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "public-bridge/1.0",
        },
        method=method,
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not isinstance(data, dict) or data.get("ok") is not True:
        raise SystemExit("bridge request failed")

    if mode == "write" and data.get("verified") is not True:
        raise SystemExit("bridge verification failed")

    print("bridge request succeeded")

if __name__ == "__main__":
    main()
