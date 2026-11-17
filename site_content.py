import base64
from html import escape
import json

PAGES = {"api": "api.html", "home": "index.html"}
DEFAULTS = {
    "api": ("Edge Gateway", "The web endpoint is available."),
    "home": ("صفحهٔ سرویس", "وب‌سرور آمادهٔ پاسخ‌گویی به درخواست‌های وب است."),
}
PREFIX = "<!-- edge-panel:"


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
    lang, direction = ("fa", "rtl") if page == "home" else ("en", "ltr")
    return (
        "<!doctype html>\n"
        f'<html lang="{lang}" dir="{direction}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title></head>"
        f"<body><main><h1>{escape(title)}</h1><p>{escape(body).replace(chr(10), '<br>')}</p></main></body></html>\n"
        f"{PREFIX}{data} -->\n"
    )
