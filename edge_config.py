import ipaddress
import json
import re

DOMAIN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
PATH = re.compile(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
PREFIX = "# edge-panel routes "


def domain(value):
    if not isinstance(value, str) or not DOMAIN.fullmatch(value):
        raise ValueError("نام دامنه معتبر نیست.")
    return value


def validate_routes(routes):
    if not isinstance(routes, list) or len(routes) > 128:
        raise ValueError("حداکثر ۱۲۸ مسیر مجاز است.")
    seen = set()
    for route in routes:
        if not isinstance(route, dict) or set(route) != {"path", "host", "port"}:
            raise ValueError("ساختار مسیر معتبر نیست.")
        path, host, port = route["path"], route["host"], route["port"]
        if not isinstance(path, str) or len(path) > 120 or not PATH.fullmatch(path):
            raise ValueError("مسیر باید با / شروع شود و فقط حروف لاتین، عدد، خط تیره یا زیرخط داشته باشد.")
        if path == "/edge-panel" or path.startswith("/edge-panel/"):
            raise ValueError("مسیر /edge-panel برای پنل مدیریت رزرو شده است.")
        if path == "/_edge-site" or path.startswith("/_edge-site/"):
            raise ValueError("مسیر /_edge-site برای فایل‌های سایت عمومی رزرو شده است.")
        if path in seen:
            raise ValueError("این مسیر قبلاً ثبت شده است.")
        seen.add(path)
        if not isinstance(host, str):
            raise ValueError("نشانی مقصد معتبر نیست.")
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            domain(host)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("پورت باید بین ۱ تا ۶۵۵۳۵ باشد.")
    return routes


def read_routes(config):
    first_line = config.splitlines()[0]
    if not first_line.startswith(PREFIX):
        raise ValueError("فایل متعلق به Edge Panel نیست.")
    return validate_routes(json.loads(first_line[len(PREFIX):]))


def render(name, routes, cert="/etc/edge-panel/edge.pem", listen="0.0.0.0:443", web="127.0.0.1:8081"):
    domain(name)
    validate_routes(routes)
    lines = [
        PREFIX + json.dumps(routes, separators=(",", ":")),
        "global",
        "    log stdout format raw local0",
        "    user haproxy",
        "    group haproxy",
        "    ssl-default-bind-options ssl-min-ver TLSv1.2",
        "defaults",
        "    mode tcp",
        "    log global",
        "    option tcplog",
        "    timeout connect 5s",
        "    timeout client 10m",
        "    timeout server 10m",
        "frontend edge_tls",
        f"    bind {listen} ssl crt {cert} alpn http/1.1",
        "    tcp-request inspect-delay 2s",
        f"    acl edge_sni ssl_fc_sni -i {name}",
    ]
    for index, route in enumerate(routes):
        prefix = f'GET {route["path"]} HTTP/1.1\r\n'.encode("ascii")
        lines.append(f"    acl route_{index} req.payload(0,{len(prefix)}) -m bin {prefix.hex()}")
    for index in range(len(routes)):
        lines.append(f"    tcp-request content accept if edge_sni route_{index}")
    lines.append("    tcp-request content accept if HTTP")
    for index in range(len(routes)):
        lines.append(f"    use_backend node_{index} if edge_sni route_{index}")
    lines += ["    default_backend website", "backend website", f"    server nginx {web}"]
    for index, route in enumerate(routes):
        lines += [f"backend node_{index}", f'    server upstream {route["host"]}:{route["port"]}']
    return "\n".join(lines) + "\n"
