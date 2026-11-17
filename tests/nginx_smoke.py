from contextlib import ExitStack, contextmanager
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from site_content import install_pages

PUBLIC_HOST = "api.example.com"


@contextmanager
def server(args, **kwargs):
    process = subprocess.Popen(args, **kwargs)
    try:
        yield process
    finally:
        process.terminate()
        process.wait(timeout=10)


def fetch(path, method="GET", body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", 19081, timeout=3)
    try:
        client.request(method, path, body, {"Host": PUBLIC_HOST, **(headers or {})})
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
    work = Path(temp)
    (work / "settings.json").write_text(json.dumps({
        "domain": PUBLIC_HOST, "secret": os.urandom(32).hex(),
        "password_hash": generate_password_hash("test-only"),
    }))
    (work / "haproxy.cfg").write_text("# edge-panel routes []\n")
    site = work / "site"
    site.mkdir()
    (site / "assets").symlink_to(ROOT / "web/assets", target_is_directory=True)
    install_pages(site, ROOT / "web")
    proxy = (ROOT / "deploy/panel-proxy.conf").read_text()
    (work / "panel-proxy.conf").write_text(proxy.replace("15000", "19000"))
    for kind in ["client_body", "proxy", "fastcgi", "uwsgi", "scgi"]:
        (work / kind).mkdir()
    config = (ROOT / "deploy/nginx.conf").read_text()
    replacements = {
        "listen 80;": "listen 127.0.0.1:19080;",
        "listen [::]:80;": "",
        "127.0.0.1:8081": "127.0.0.1:19081",
        "listen 127.0.0.1:15001;": "listen 127.0.0.1:19001;",
        "@NAMES@": PUBLIC_HOST,
        "@API@": PUBLIC_HOST,
        "/etc/edge-panel/panel-proxy.conf": str(work / "panel-proxy.conf"),
        "/var/www/edge-panel": str(site),
    }
    for old, new in replacements.items():
        config = config.replace(old, new)
    temporary_paths = " ".join(f"{kind}_temp_path {work / kind};" for kind in ["client_body", "proxy", "fastcgi", "uwsgi", "scgi"])
    (work / "nginx.conf").write_text(
        f"daemon off; pid {work / 'nginx.pid'}; error_log stderr; events {{}} "
        f"http {{ {temporary_paths} access_log off; {config} }}"
    )
    nginx = ["/usr/sbin/nginx", "-c", str(work / "nginx.conf"), "-p", str(work)]
    subprocess.run([*nginx, "-t"], check=True)
    stack.enter_context(server(
        ["gunicorn", "--bind", "127.0.0.1:19000", "app:app"], cwd=ROOT,
        env={**os.environ, "EDGE_SETTINGS": str(work / "settings.json"), "EDGE_CONFIG": str(work / "haproxy.cfg")},
    ))
    stack.enter_context(server(nginx))
    for attempt in range(30):
        try:
            status, headers, _ = fetch("/edge-panel/")
            if status != 502:
                break
        except ConnectionRefusedError:
            if attempt == 29:
                raise
        time.sleep(.1)
    assert status == 302 and headers["Location"] == "/edge-panel/login"
    status, headers, html = fetch("/edge-panel/login")
    assert status == 200 and b"/edge-panel/static/panel.css" in html
    assert "Secure" in headers["Set-Cookie"] and "Path=/edge-panel/" in headers["Set-Cookie"]
    assert fetch("/edge-panel/static/panel.css")[0] == 200
    status, _, html = fetch("/")
    assert status == 200 and b"Common questions" in html and b"/_edge-site/site.css" in html
    assert fetch("/_edge-site/site.css")[0] == 200
    status, _, photo = fetch("/_edge-site/server-racks.webp")
    assert status == 200 and photo[:4] == b"RIFF" and photo[8:12] == b"WEBP"
    assert fetch("/login")[0] == 404
print("Nginx dashboard routing, HTTPS session cookie, and public website passed.")
