import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from edge_config import read_routes, validate_routes

CONFIG = Path(os.environ.get("EDGE_CONFIG", "/etc/edge-panel/haproxy.cfg"))
SETTINGS = json.loads(Path(os.environ.get("EDGE_SETTINGS", "/etc/edge-panel/panel.json")).read_text())
app = Flask(__name__)
app.config.update(
    SECRET_KEY=SETTINGS["secret"],
    MAX_CONTENT_LENGTH=4096,
    SESSION_COOKIE_NAME="edge_panel",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
)


@app.before_request
def protect():
    if request.host != "127.0.0.1:15001":
        abort(400)
    if request.method == "POST" and (
        not session.get("csrf") or not secrets.compare_digest(session["csrf"].encode(), request.form.get("csrf", "").encode())
    ):
        abort(403)
    if request.endpoint not in {"login", "static"} and not session.get("signed_in"):
        return redirect(url_for("login"))


@app.after_request
def headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    return response


@app.context_processor
def token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(24)
    return {"csrf": session["csrf"]}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if check_password_hash(SETTINGS["password_hash"], request.form.get("password", "")):
            session.clear()
            session["signed_in"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="رمز ورود اشتباه است."), 401
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    config = CONFIG.read_bytes()
    routes = read_routes(config.decode())
    editing = next((route for route in routes if route["path"] == request.args.get("edit")), None)
    return render_template(
        "index.html", domain=SETTINGS["domain"], routes=routes, editing=editing,
        revision=hashlib.sha256(config).hexdigest(),
    )


@app.post("/routes")
def change():
    try:
        config = CONFIG.read_bytes()
        revision = hashlib.sha256(config).hexdigest()
        if revision != request.form.get("revision"):
            raise ValueError("مسیرها تغییر کرده‌اند. دوباره تلاش کنید.")
        routes = read_routes(config.decode())
        action = request.form["action"]
        old_path = request.form.get("old_path")
        if action in {"edit", "delete"} and not any(r["path"] == old_path for r in routes):
            raise ValueError("مسیر پیدا نشد.")
        if action in {"edit", "delete"}:
            routes = [r for r in routes if r["path"] != old_path]
        if action in {"add", "edit"}:
            routes.append({
                "path": request.form.get("path", "").strip(),
                "host": request.form.get("host", "").strip().lower(),
                "port": int(request.form.get("port", "")),
            })
        if action not in {"add", "edit", "delete"}:
            abort(400)
        validate_routes(routes)
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/local/sbin/edge-panel-apply"],
            input=json.dumps({"revision": revision, "routes": routes}), text=True,
            capture_output=True, timeout=45,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip()[:600] or "اعمال کانفیگ ناموفق بود.")
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        flash(str(error), "error")
    else:
        flash("تغییر مسیرها ذخیره شد و درخواست بارگذاری مجدد فرستاده شد.", "success")
    return redirect(url_for("index"))
