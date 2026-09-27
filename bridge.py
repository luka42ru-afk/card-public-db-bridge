import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ALLOWED_MODES = {"deploy"}


def fail(message):
    raise SystemExit(message)


def load_config():
    raw = (os.environ.get("BRIDGE_CONFIG") or "").strip()
    if not raw:
        fail("bridge configuration is not available")
    try:
        data = json.loads(raw)
    except Exception:
        fail("bridge configuration is invalid")
    if not isinstance(data, dict):
        fail("bridge configuration is invalid")
    return data


def safe_repo(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        fail("source repository configuration is invalid")
    return value


def safe_ref(value):
    value = str(value or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        fail("source ref configuration is invalid")
    return value


def safe_relative_path(value):
    value = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not value or value.startswith("../") or "/../" in value:
        fail("deploy command configuration is invalid")
    return value


def clone_source(config, directory):
    source = config.get("source") if isinstance(config.get("source"), dict) else {}
    repository = safe_repo(source.get("repository"))
    ref = safe_ref(source.get("ref") or "main")
    token = str(source.get("token") or "").strip()
    if not token:
        fail("source access configuration is missing")

    askpass = Path(directory) / "askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
        "  *) printf '%s\\n' \"$BRIDGE_SOURCE_TOKEN\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(askpass.stat().st_mode | stat.S_IXUSR)

    source_dir = Path(directory) / "source"
    env = os.environ.copy()
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["BRIDGE_SOURCE_TOKEN"] = token

    result = subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--branch",
            ref,
            f"https://github.com/{repository}.git",
            str(source_dir),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        fail("source checkout failed")

    return source_dir, source


def deploy_source(config, source_dir, source):
    target = config.get("target") if isinstance(config.get("target"), dict) else {}
    required = ["host", "user", "password", "root"]
    if any(not str(target.get(key) or "").strip() for key in required):
        fail("target configuration is missing")

    deploy_script = safe_relative_path(source.get("deploy_script") or ".deploy/ftp_deploy.py")
    script_path = source_dir / deploy_script
    if not script_path.is_file():
        fail("deploy command is not available")

    listed = subprocess.run(
        ["git", "ls-files"],
        cwd=source_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if listed.returncode != 0:
        fail("source file listing failed")

    env = os.environ.copy()
    env.update(
        {
            "FTP_HOST": str(target["host"]),
            "FTP_USER": str(target["user"]),
            "FTP_PASSWORD": str(target["password"]),
            "FTP_ROOT": str(target["root"]),
            "FTP_PORT": str(target.get("port") or "21"),
            "FTP_MODE": str(target.get("mode") or "auto"),
        }
    )

    result = subprocess.run(
        ["python3", str(script_path)],
        cwd=source_dir,
        env=env,
        input=listed.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        fail("deployment failed")


def verify_deploy(config):
    verify = config.get("verify")
    if not isinstance(verify, dict):
        return

    url = str(verify.get("url") or "").strip()
    expected = str(verify.get("contains") or "")
    if not url:
        return
    if not url.startswith("https://"):
        fail("verification configuration is invalid")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "public-bridge/2.0", "Cache-Control": "no-cache"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception:
        fail("deployment verification failed")

    if expected and expected not in body:
        fail("deployment verification failed")


def main():
    if len(sys.argv) != 2:
        fail("usage: bridge.py <mode>")

    mode = sys.argv[1].strip().lower()
    if mode not in ALLOWED_MODES:
        fail("unsupported mode")

    config = load_config()

    with tempfile.TemporaryDirectory(prefix="bridge-") as directory:
        source_dir, source = clone_source(config, directory)
        deploy_source(config, source_dir, source)
        verify_deploy(config)

    print("bridge operation succeeded")


if __name__ == "__main__":
    main()
