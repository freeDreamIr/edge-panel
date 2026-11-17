import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_config import render
from site_content import install_pages, page_html, read_page

BASE = "http://127.0.0.1:15001"


def csrf(response):
    return re.search(rb'name="csrf" value="([a-f0-9]+)"', response.data).group(1).decode()


with tempfile.TemporaryDirectory() as temporary:
    config = Path(temporary) / "haproxy.cfg"
    config.write_text(render("api.example.com", []))
    settings = Path(temporary) / "panel.json"
    settings.write_text(json.dumps({
        "domain": "api.example.com",
        "secret": "local-test-secret",
        "password_hash": generate_password_hash("test-only"),
    }))
    for page, file in [("api", "api.html"), ("home", "index.html")]:
        (Path(temporary) / file).write_text(page_html(page, "Title", "<script>test</script>"), encoding="utf-8")
    api = Path(temporary) / "api.html"
    assert "&lt;script&gt;" in api.read_text(encoding="utf-8")
    assert read_page(api, "api")["body"] == "<script>test</script>"
    stock = Path(__file__).resolve().parents[1] / "web"
    home = Path(temporary) / "index.html"
    home.write_bytes((stock / "index.html").read_bytes())
    install_pages(Path(temporary), stock)
    assert read_page(api, "api")["body"] == "<script>test</script>"
    assert 'lang="fa" dir="rtl"' in home.read_text(encoding="utf-8")
    assert "/_edge-site/site.css" in home.read_text(encoding="utf-8")
    os.environ.update(EDGE_SETTINGS=str(settings), EDGE_CONFIG=str(config), EDGE_SITE=temporary)
    import app

    def apply(*args, **kwargs):
        payload = json.loads(kwargs["input"])
        if "routes" in payload:
            config.write_text(render("api.example.com", payload["routes"]))
        return subprocess.CompletedProcess(args, 0, stdout="")

    with app.app.test_client() as client, patch.object(app.subprocess, "run", side_effect=apply) as runner:
        assert client.get("/", base_url=BASE).status_code == 302
        assert client.get("/", base_url="http://other.example").status_code == 400
        login = client.get("/login", base_url=BASE)
        assert client.post("/login", base_url=BASE, data={"password": "test-only"}).status_code == 403
        assert client.post("/login", base_url=BASE, data={"csrf": csrf(login), "username": "admin", "password": "test-only"}).status_code == 302

        def change(action, **fields):
            page = client.get("/", base_url=BASE)
            revision = re.search(rb'name="revision" value="([a-f0-9]+)"', page.data).group(1).decode()
            data = {"csrf": csrf(page), "revision": revision, "action": action, **fields}
            return client.post("/routes", base_url=BASE, data=data, follow_redirects=True)

        assert b"node.example.com:8080" in change("add", path="/s2", host="node.example.com", port="8080").data
        assert b"node.example.com:9443" in change("edit", old_path="/s2", path="/s2", host="node.example.com", port="9443").data
        assert b"node.example.com:9443" not in change("delete", old_path="/s2").data
        change("add", path="/s2", host="node.example.com\nbackend other", port="8080")
        change("add", path="/edge-panel/login", host="node.example.com", port="8080")
        assert runner.call_count == 3
        site = client.get("/website", base_url=BASE)
        assert b"&lt;script&gt;test&lt;/script&gt;" in site.data
        assert client.post("/website", base_url=BASE, data={
            "csrf": csrf(site), "page": "api", "title": "New", "body": "Updated",
        }).status_code == 302
        assert json.loads(runner.call_args.kwargs["input"])["operation"] == "save_page"
    public = "http://api.example.com"
    headers = {"X-Forwarded-Prefix": "/edge-panel", "X-Forwarded-Proto": "https"}
    with app.app.test_client() as client:
        assert client.get("/", base_url=public, headers=headers).location == "/edge-panel/login"
        assert client.get("/login", base_url=public).status_code == 400
        login = client.get("/login", base_url=public, headers=headers)
        assert b"/edge-panel/static/panel.css" in login.data
        cookie = login.headers["Set-Cookie"]
        assert "Secure" in cookie and "Path=/edge-panel/" in cookie and "HttpOnly" in cookie
print("Local and public login, CSRF, cookies, routes, and website editing passed.")
