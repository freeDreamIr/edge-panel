import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_config import render


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.server.label)))
        self.end_headers()
        self.wfile.write(self.server.label)


class EdgeConnection(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


with tempfile.TemporaryDirectory() as temporary:
    work = Path(temporary)
    certificate, key = work / "cert.pem", work / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "1",
        "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=api.example.com",
        "-addext", "subjectAltName=DNS:api.example.com,DNS:example.com",
    ], check=True, capture_output=True)
    bundle = work / "edge.pem"
    bundle.write_bytes(certificate.read_bytes() + key.read_bytes())
    context = ssl.create_default_context(cafile=str(certificate))
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as site, ThreadingHTTPServer(("127.0.0.1", 0), Handler) as node:
        for service, label in [(site, b"public website"), (node, b"route backend")]:
            service.label = label
            threading.Thread(target=service.serve_forever, daemon=True).start()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        cfg = work / "haproxy.cfg"
        cfg.write_text(render("api.example.com", [
            {"path": "/s2", "host": "127.0.0.1", "port": node.server_port},
        ], cert=bundle, listen=f"127.0.0.1:{port}", web=f"127.0.0.1:{site.server_port}"))
        subprocess.run(["haproxy", "-c", "-f", str(cfg)], check=True)
        proxy = subprocess.Popen(["haproxy", "-db", "-f", str(cfg)])
        try:
            def response(host, path):
                connection = EdgeConnection(host, port, context=context, timeout=3)
                try:
                    connection.request("GET", path)
                    return connection.getresponse().read()
                finally:
                    connection.close()

            for attempt in range(30):
                try:
                    assert response("api.example.com", "/") == b"public website"
                    break
                except ConnectionRefusedError:
                    if proxy.poll() is not None or attempt == 29:
                        raise
                    time.sleep(.1)
            assert response("api.example.com", "/s2") == b"route backend"
            assert response("api.example.com", "/s20") == b"public website"
            assert response("example.com", "/s2") == b"public website"
        finally:
            proxy.terminate()
            proxy.wait(timeout=5)
print("TLS, route match, similar paths, and website fallback passed.")
