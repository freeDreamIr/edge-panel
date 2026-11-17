import hashlib
import json
import os
from pathlib import Path
from datetime import timedelta
import secrets
import subprocess

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from edge_config import read_routes, validate_routes
from site_content import PAGES, read_page

CONFIG = Path(os.environ.get("EDGE_CONFIG", "/etc/edge-panel/haproxy.cfg"))
SETTINGS = json.loads(Path(os.environ.get("EDGE_SETTINGS", "/etc/edge-panel/panel.json")).read_text())
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_prefix=1)
app.config.update(
    SECRET_KEY=SETTINGS["secret"],
    MAX_CONTENT_LENGTH=24576,
    SESSION_COOKIE_NAME="edge_panel",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_REFRESH_EACH_REQUEST=False,
)


class PanelSession(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):
        return request.is_secure

    def get_cookie_path(self, app):
        return request.script_root + "/" if request.script_root else "/"


app.session_interface = PanelSession()
SITE = Path(os.environ.get("EDGE_SITE", "/var/www/edge-panel"))
SERVICES = {"edge-haproxy": "HAProxy", "nginx": "Nginx", "edge-panel": "پنل", "certbot.timer": "تمدید خودکار"}
OPERATIONS = {
    "check_haproxy": "بررسی کانفیگ HAProxy",
    "reload_haproxy": "بررسی و بازخوانی HAProxy",
    "check_nginx": "بررسی کانفیگ Nginx",
    "reload_nginx": "بررسی و بازخوانی Nginx",
    "check_cert": "بررسی تاریخ گواهی دامنه",
}


@app.before_request
def protect():
    local = request.host == "127.0.0.1:15001" and not request.script_root and not request.is_secure
    public = request.host == SETTINGS["domain"] and request.script_root == "/edge-panel" and request.is_secure
    if not (local or public):
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
        username = request.form.get("username", "").encode()
        if check_password_hash(SETTINGS["password_hash"], request.form.get("password", "")) and secrets.compare_digest(username, b"admin"):
            session.clear()
            session["signed_in"] = True
            session.permanent = True
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


def manage(payload):
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/local/sbin/edge-panel-apply"],
        input=json.dumps(payload), text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=45,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip()[:600] or "اعمال تنظیمات ناموفق بود.")
    return result.stdout.strip()[:600]


@app.get("/services")
def services():
    status = {}
    for name, label in SERVICES.items():
        try:
            result = subprocess.run(["/usr/bin/systemctl", "is-active", name], capture_output=True, text=True, timeout=2)
            status[label] = result.stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            status[label] = "unknown"
    return render_template("services.html", status=status, operations=OPERATIONS)


@app.post("/services")
def operate():
    operation = request.form.get("operation")
    if operation not in OPERATIONS:
        abort(400)
    try:
        message = manage({"operation": operation})
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        flash(str(error), "error")
    else:
        flash(message or "عملیات انجام شد.", "success")
    return redirect(url_for("services"))


@app.get("/website")
def website():
    pages = {key: read_page(SITE / file, key) for key, file in PAGES.items()}
    return render_template("website.html", pages=pages)


@app.post("/website")
def save_website():
    try:
        manage({
            "operation": "save_page",
            "page": request.form.get("page"),
            "title": request.form.get("title", "").strip(),
            "body": request.form.get("body", "").strip(),
        })
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        flash(str(error), "error")
    else:
        flash("صفحه ذخیره شد.", "success")
    return redirect(url_for("website"))


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
        manage({"revision": revision, "routes": routes})
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        flash(str(error), "error")
    else:
        flash("تغییر مسیرها ذخیره شد و درخواست بارگذاری مجدد فرستاده شد.", "success")
    return redirect(url_for("index"))
