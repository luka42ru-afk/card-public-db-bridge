import ftplib
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ALLOWED_MODES = {"deploy", "operate", "inspect_ftp"}


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


def load_command():
    path = Path("command.json")
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        fail("bridge command is invalid")
    if not isinstance(data, dict):
        fail("bridge command is invalid")
    return data


def download_source(config, directory, ref_override=None):
    source = config.get("source") if isinstance(config.get("source"), dict) else {}
    repository = safe_repo(source.get("repository"))
    ref = safe_ref(ref_override or source.get("ref") or "main")
    token = str(source.get("token") or "").strip()
    if not token:
        fail("source access configuration is missing")

    archive_path = Path(directory) / "source.zip"
    source_dir = Path(directory) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    url = (
        "https://api.github.com/repos/"
        + repository
        + "/zipball/"
        + urllib.parse.quote(ref, safe="")
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "public-bridge/3.0",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            archive_path.write_bytes(response.read())
    except UnicodeEncodeError:
        fail("source token format is invalid")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            fail("source access denied (http " + str(exc.code) + ")")
        fail("source download failed (http " + str(exc.code) + ")")
    except urllib.error.URLError:
        fail("source network request failed")
    except Exception as exc:
        fail("source download failed (" + type(exc).__name__ + ")")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                parts = [part for part in name.split("/") if part]
                if len(parts) <= 1 or info.is_dir():
                    continue
                relative = "/".join(parts[1:])
                if relative.startswith("../") or "/../" in relative or relative.startswith("/"):
                    fail("source archive is invalid")
                target = source_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
    except SystemExit:
        raise
    except Exception:
        fail("source archive is invalid")

    return source_dir, source


def load_private_deploy_paths(source_dir):
    manifest = source_dir / ".deploy" / "public_deploy_manifest.json"
    if not manifest.is_file():
        fail("private deploy manifest is not available")

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        fail("private deploy manifest is invalid")

    if not isinstance(data, dict):
        fail("private deploy manifest is invalid")

    paths = data.get("paths")
    if not isinstance(paths, list) or not paths:
        fail("private deploy manifest is invalid")

    return paths


def deploy_source(config, source_dir, source, selected_paths=None):
    target = config.get("target") if isinstance(config.get("target"), dict) else {}
    required = ["host", "user", "password", "root"]
    if any(not str(target.get(key) or "").strip() for key in required):
        fail("target configuration is missing")

    deploy_script = safe_relative_path(source.get("deploy_script") or ".deploy/ftp_deploy.py")
    script_path = source_dir / deploy_script
    if not script_path.is_file():
        fail("deploy command is not available")

    if selected_paths is None:
        file_list = []
        for path in source_dir.rglob("*"):
            if path.is_file():
                file_list.append(path.relative_to(source_dir).as_posix())
        file_list.sort()
    else:
        if not isinstance(selected_paths, list) or not selected_paths:
            fail("deploy paths configuration is invalid")
        file_list = []
        seen = set()
        for value in selected_paths:
            relative = safe_relative_path(value)
            if relative in seen:
                continue
            target_path = source_dir / relative
            if not target_path.is_file():
                fail("deploy path is not available")
            seen.add(relative)
            file_list.append(relative)
        file_list.sort()

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
        input="\n".join(file_list) + "\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        fail("deployment failed")


def verify_private_deploy_files(config, source_dir, selected_paths):
    target = config.get("target") if isinstance(config.get("target"), dict) else {}
    required = ["host", "user", "password", "root"]
    if any(not str(target.get(key) or "").strip() for key in required):
        fail("target configuration is missing")

    if not isinstance(selected_paths, list) or not selected_paths:
        fail("private deploy verification is unavailable")

    mode = str(target.get("mode") or "auto").lower()
    port = int(target.get("port") or 21)
    root = str(target.get("root") or "/")

    def connect_one(candidate):
        if candidate == "ftps":
            ftp = ftplib.FTP_TLS(timeout=30)
            ftp.connect(str(target["host"]), port)
            ftp.login(str(target["user"]), str(target["password"]))
            ftp.prot_p()
            return ftp
        if candidate == "ftp":
            ftp = ftplib.FTP(timeout=30)
            ftp.connect(str(target["host"]), port)
            ftp.login(str(target["user"]), str(target["password"]))
            return ftp
        raise ValueError("unsupported ftp mode")

    candidates = [mode] if mode in ("ftp", "ftps") else ["ftps", "ftp"]
    ftp = None
    for candidate in candidates:
        try:
            ftp = connect_one(candidate)
            break
        except ftplib.all_errors:
            continue
    if ftp is None:
        fail("private deploy verification failed")

    try:
        for value in selected_paths:
            relative = safe_relative_path(value)
            local_path = source_dir / relative
            if not local_path.is_file():
                fail("private deploy verification failed")

            remote_path = posixpath.normpath(posixpath.join(root, relative))
            chunks = []
            try:
                ftp.retrbinary("RETR " + remote_path, chunks.append)
            except ftplib.all_errors:
                fail("private deploy verification failed")

            if b"".join(chunks) != local_path.read_bytes():
                fail("private deploy verification failed")
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def operate_source(source_dir):
    descriptor = source_dir / ".deploy" / "public_bridge_command.json"
    if not descriptor.is_file():
        fail("private operation descriptor is not available")

    try:
        command = json.loads(descriptor.read_text(encoding="utf-8"))
    except Exception:
        fail("private operation descriptor is invalid")
    if not isinstance(command, dict):
        fail("private operation descriptor is invalid")

    handler = str(command.get("handler") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+_bridge\.py", handler):
        fail("private operation handler is invalid")

    handler_path = source_dir / ".github" / "db-bridge" / handler
    if not handler_path.is_file():
        fail("private operation handler is not available")

    result_path = source_dir / ".bridge-operation-result.json"
    result = subprocess.run(
        ["python3", str(handler_path), str(descriptor), str(result_path)],
        cwd=source_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = ""
        try:
            if result_path.is_file():
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    detail = str(payload.get("error") or "").replace("\n", " ").strip()[:500]
        except Exception:
            detail = ""
        if not detail:
            detail = str(result.stderr or "").replace("\n", " ").strip()[-500:]
        fail("private operation failed" + (": " + detail if detail else ""))

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        fail("private operation verification is unavailable")
    if not isinstance(payload, dict) or payload.get("verified") is not True:
        fail("private operation verification failed")

    sealed = str(payload.get("sealed_result") or "").strip()
    if sealed:
        if len(sealed) > 300000 or not re.fullmatch(r"[A-Za-z0-9+/=]+", sealed):
            fail("private sealed result is invalid")
        print("private-sealed-result:" + sealed)


def inspect_ftp(config, command):
    target = config.get("target") if isinstance(config.get("target"), dict) else {}
    required = ["host", "user", "password", "root"]
    if any(not str(target.get(key) or "").strip() for key in required):
        fail("target configuration is missing")

    relative = safe_relative_path(command.get("inspect_path") or "")
    recursive = bool(command.get("recursive", True))
    max_items = int(command.get("max_items") or 500)
    if max_items < 1 or max_items > 2000:
        fail("inspect max_items is invalid")

    root = str(target["root"] or "/")
    remote_root = posixpath.normpath(posixpath.join(root, relative))
    mode = str(target.get("mode") or "auto").lower()
    port = int(target.get("port") or 21)

    def connect_one(candidate):
        if candidate == "ftps":
            ftp = ftplib.FTP_TLS(timeout=30)
            ftp.connect(str(target["host"]), port)
            ftp.login(str(target["user"]), str(target["password"]))
            ftp.prot_p()
            return ftp
        if candidate == "ftp":
            ftp = ftplib.FTP(timeout=30)
            ftp.connect(str(target["host"]), port)
            ftp.login(str(target["user"]), str(target["password"]))
            return ftp
        raise ValueError("unsupported ftp mode")

    candidates = [mode] if mode in ("ftp", "ftps") else ["ftps", "ftp"]
    ftp = None
    last_error = None
    for candidate in candidates:
        try:
            ftp = connect_one(candidate)
            break
        except ftplib.all_errors as exc:
            last_error = exc
    if ftp is None:
        fail("ftp inspection connection failed" + (": " + str(last_error) if last_error else ""))

    items = []

    def walk(remote_dir, relative_dir=""):
        if len(items) >= max_items:
            return
        try:
            rows = list(ftp.mlsd(remote_dir, facts=["type", "size", "modify"]))
        except ftplib.all_errors as exc:
            fail("ftp inspection failed: " + str(exc))
        for name, facts in rows:
            if name in (".", ".."):
                continue
            item_rel = posixpath.join(relative_dir, name) if relative_dir else name
            kind = str(facts.get("type") or "unknown")
            item = {"path": item_rel, "type": kind}
            if facts.get("size") is not None:
                try:
                    item["size"] = int(facts["size"])
                except Exception:
                    item["size"] = facts["size"]
            if facts.get("modify"):
                item["modify"] = facts["modify"]
            items.append(item)
            if len(items) >= max_items:
                return
            if recursive and kind == "dir":
                walk(posixpath.join(remote_dir, name), item_rel)

    try:
        walk(remote_root)
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass

    result = {
        "verified": True,
        "mode": "inspect_ftp",
        "path": relative,
        "recursive": recursive,
        "count": len(items),
        "truncated": len(items) >= max_items,
        "items": sorted(items, key=lambda row: str(row.get("path") or "").lower()),
    }
    print(json.dumps(result, ensure_ascii=False))


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
            headers={"User-Agent": "public-bridge/3.0", "Cache-Control": "no-cache"},
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
    command = load_command()
    source_ref = command.get("source_ref")
    if source_ref is not None:
        source_ref = safe_ref(source_ref)
    deploy_paths = command.get("deploy_paths")
    deploy_scope = str(command.get("deploy_scope") or "").strip().lower()
    if deploy_scope not in ("", "private"):
        fail("deploy scope is invalid")

    if mode == "inspect_ftp":
        inspect_ftp(config, command)
        print("bridge operation succeeded")
        return

    with tempfile.TemporaryDirectory(prefix="bridge-") as directory:
        source_dir, source = download_source(config, directory, source_ref)
        if mode == "deploy":
            if deploy_scope == "private":
                deploy_paths = load_private_deploy_paths(source_dir)
            deploy_source(config, source_dir, source, deploy_paths)
            if deploy_scope == "private":
                verify_private_deploy_files(config, source_dir, deploy_paths)
            verify_deploy(config)
        else:
            operate_source(source_dir)

    print("bridge operation succeeded")


if __name__ == "__main__":
    main()
