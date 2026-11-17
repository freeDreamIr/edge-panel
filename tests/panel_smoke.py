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
    os.environ.update(EDGE_SETTINGS=str(settings), EDGE_CONFIG=str(config))
    import app

    def apply(*args, **kwargs):
        config.write_text(render("api.example.com", json.loads(kwargs["input"])["routes"]))
        return subprocess.CompletedProcess(args, 0)

    with app.app.test_client() as client, patch.object(app.subprocess, "run", side_effect=apply) as runner:
        assert client.get("/", base_url=BASE).status_code == 302
        assert client.get("/", base_url="http://other.example").status_code == 400
        login = client.get("/login", base_url=BASE)
        assert client.post("/login", base_url=BASE, data={"password": "test-only"}).status_code == 403
        assert client.post("/login", base_url=BASE, data={"csrf": csrf(login), "password": "test-only"}).status_code == 302

        def change(action, **fields):
            page = client.get("/", base_url=BASE)
            revision = re.search(rb'name="revision" value="([a-f0-9]+)"', page.data).group(1).decode()
            data = {"csrf": csrf(page), "revision": revision, "action": action, **fields}
            return client.post("/routes", base_url=BASE, data=data, follow_redirects=True)

        assert b"node.example.com:8080" in change("add", path="/s2", host="node.example.com", port="8080").data
        assert b"node.example.com:9443" in change("edit", old_path="/s2", path="/s2", host="node.example.com", port="9443").data
        assert b"node.example.com:9443" not in change("delete", old_path="/s2").data
        change("add", path="/s2", host="node.example.com\nbackend other", port="8080")
        assert runner.call_count == 3
print("Login, CSRF, host validation, add/edit/delete, and invalid input passed.")
