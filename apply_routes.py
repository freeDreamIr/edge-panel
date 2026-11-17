import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from edge_config import render
from site_content import PAGES, publish_page

BASE = Path("/etc/edge-panel")
LIVE = BASE / "haproxy.cfg"


def temporary_config(content, directory=BASE):
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
        os.fchmod(file.fileno(), 0o644)
        return Path(file.name)


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "اجرای فرمان ناموفق بود.").strip())
    return result.stdout.strip()


def apply_routes(payload):
    old = LIVE.read_bytes()
    if payload["revision"] != hashlib.sha256(old).hexdigest():
        raise ValueError("مسیرها هم‌زمان تغییر کرده‌اند. دوباره تلاش کنید.")
    settings = json.loads((BASE / "panel.json").read_text())
    config = render(settings["domain"], payload["routes"])
    candidate = temporary_config(config.encode())
    try:
        run("/usr/sbin/haproxy", "-c", "-f", str(candidate))
        os.replace(candidate, LIVE)
        try:
            run("/usr/bin/systemctl", "reload", "edge-haproxy.service")
        except (RuntimeError, subprocess.TimeoutExpired):
            os.replace(temporary_config(old), LIVE)
            run("/usr/bin/systemctl", "reload", "edge-haproxy.service")
            raise
    finally:
        candidate.unlink(missing_ok=True)


def apply(payload):
    with (BASE / "apply.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        operation = payload.get("operation")
        if operation is None:
            apply_routes(payload)
        elif operation in {"check_haproxy", "reload_haproxy"}:
            run("/usr/sbin/haproxy", "-c", "-f", str(LIVE))
            if operation == "reload_haproxy":
                run("/usr/bin/systemctl", "reload", "edge-haproxy.service")
        elif operation in {"check_nginx", "reload_nginx"}:
            run("/usr/sbin/nginx", "-t")
            if operation == "reload_nginx":
                run("/usr/bin/systemctl", "reload", "nginx.service")
        elif operation == "check_cert":
            print(run("/usr/bin/openssl", "x509", "-in", str(BASE / "edge.pem"), "-noout", "-dates"))
        elif operation == "save_page":
            page = payload["page"]
            if not isinstance(page, str) or page not in PAGES:
                raise ValueError("صفحه معتبر نیست.")
            destination = Path("/var/www/edge-panel") / PAGES[page]
            publish_page(destination, page, payload["title"], payload["body"])
        else:
            raise ValueError("عملیات مجاز نیست.")


if __name__ == "__main__":
    try:
        apply(json.loads(sys.stdin.buffer.read(65537)))
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit(str(error))
