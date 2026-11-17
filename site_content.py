import base64
from html import escape
import json
import os
from pathlib import Path
from string import Template
import tempfile

PAGES = {"api": "api.html", "home": "index.html"}
DEFAULTS = {
    "api": ("Edge Gateway", "The public entry point for web traffic. Find connection details and quick checks for this endpoint."),
    "home": ("درگاه وب و زیرساخت لبه", "درگاه عمومی دریافت درخواست‌های وب. جزئیات اتصال و روش بررسی دسترسی به این صفحه را اینجا بخوانید."),
}
PREFIX = "<!-- edge-panel:"
LANDING = Path(__file__).with_name("templates") / "landing.html"
COPY = {
    "api": {
        "lang": "en", "direction": "ltr", "skip": "Skip to content", "navigation": "Page sections",
        "nav_overview": "Overview", "nav_connection": "Connection", "nav_faq": "FAQ",
        "eyebrow": "Web infrastructure", "cta": "Connection details",
        "photo_alt": "Rows of servers in a data center aisle", "photo_caption": "Data center illustration",
        "connection_label": "Endpoint details", "connection_title": "How this gateway works",
        "secure_title": "HTTPS access", "secure_body": "Your browser connects using TLS. Use this domain name so it can validate the certificate.",
        "web_title": "Web responses", "web_body": "The homepage and its assets are delivered by the gateway’s web server.",
        "status_title": "Reachability", "status_body": "If this page loads, the endpoint is reachable from your network.",
        "guide_label": "Quick reference", "guide_title": "Common questions",
        "question_one": "Which URL should I use?", "answer_one": "Open this host with HTTPS on the standard port 443. You can bookmark this page.",
        "question_two": "Why do I see a 404 on another path?", "answer_two": "The address you requested may not be available here. Return to the homepage to check the host.",
        "question_three": "Does this page monitor other services?", "answer_three": "It confirms a response from this web endpoint. Other services may have separate health checks.",
        "top": "Back to top",
    },
    "home": {
        "lang": "fa", "direction": "rtl", "skip": "رفتن به محتوا", "navigation": "بخش‌های صفحه",
        "nav_overview": "معرفی", "nav_connection": "اطلاعات اتصال", "nav_faq": "پرسش‌ها",
        "eyebrow": "زیرساخت وب", "cta": "جزئیات اتصال",
        "photo_alt": "ردیف رک‌های سرور در راهروی مرکز داده", "photo_caption": "تصویر نمونهٔ مرکز داده",
        "connection_label": "مشخصات درگاه", "connection_title": "این درگاه چطور کار می‌کند؟",
        "secure_title": "اتصال HTTPS", "secure_body": "مرورگر با TLS وصل می‌شود. از نام دامنه استفاده کنید تا مرورگر بتواند اعتبار گواهی را بررسی کند.",
        "web_title": "پاسخ وب", "web_body": "صفحهٔ اصلی و فایل‌های آن مستقیماً از وب‌سرور درگاه ارائه می‌شوند.",
        "status_title": "دسترسی به صفحه", "status_body": "باز شدن این صفحه نشان می‌دهد که شبکهٔ شما به وب‌سرور درگاه دسترسی دارد.",
        "guide_label": "راهنمای سریع", "guide_title": "پرسش‌های متداول",
        "question_one": "با چه آدرسی وصل شوم؟", "answer_one": "این دامنه را با HTTPS و پورت استاندارد ۴۴۳ باز کنید. می‌توانید همین صفحه را نشانک بزنید.",
        "question_two": "چرا در مسیر دیگر خطای ۴۰۴ می‌بینم؟", "answer_two": "ممکن است مسیر درخواستی روی این دامنه تعریف نشده باشد. برای بررسی دامنه به صفحهٔ اصلی برگردید.",
        "question_three": "آیا این صفحه وضعیت سرویس‌های دیگر را هم نشان می‌دهد؟", "answer_three": "این صفحه پاسخ‌گویی همین وب‌سرور را نشان می‌دهد. وضعیت سرویس‌های دیگر جداگانه بررسی می‌شود.",
        "top": "بازگشت به بالا",
    },
}


def read_page(path, page):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(PREFIX):
            return json.loads(base64.b64decode(line[len(PREFIX):].split(" ", 1)[0]))
    title, body = DEFAULTS[page]
    return {"title": title, "body": body}


def page_html(page, title, body):
    if page not in PAGES or not isinstance(title, str) or not isinstance(body, str):
        raise ValueError("صفحه معتبر نیست.")
    if not 1 <= len(title) <= 100 or not 1 <= len(body) <= 1400:
        raise ValueError("عنوان باید ۱ تا ۱۰۰ و متن ۱ تا ۱۴۰۰ نویسه باشد.")
    data = base64.b64encode(json.dumps({"title": title, "body": body}).encode()).decode()
    return Template(LANDING.read_text(encoding="utf-8")).substitute(
        **{key: escape(value) for key, value in COPY[page].items()},
        title=escape(title), description=escape(" ".join(body.split())[:160]),
        body=escape(body).replace("\n", "<br>"), metadata=f"{PREFIX}{data} -->",
    )


def publish_page(destination, page, title, body):
    html = page_html(page, title, body)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
            pending = Path(file.name)
            file.write(html.encode())
            file.flush()
            os.fsync(file.fileno())
        pending.chmod(0o644)
        pending.replace(destination)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def install_pages(destination, stock):
    for page, filename in PAGES.items():
        current = destination / filename
        if current.exists():
            previous = current.read_text(encoding="utf-8")
            legacy = (stock / filename).read_text(encoding="utf-8")
            if previous.strip() != legacy.strip() and not any(line.startswith(PREFIX) for line in previous.splitlines()):
                continue
            content = read_page(current, page)
        else:
            title, body = DEFAULTS[page]
            content = {"title": title, "body": body}
        publish_page(current, page, **content)
