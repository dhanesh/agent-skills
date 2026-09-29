#!/usr/bin/env python3
"""notes: a tiny HTTP notes service. The fixture app verification-skill-forge's eval
generates a verify skill for and drives live. stdlib only.

    python3 app.py --port N --data DIR [--instance NAME]

GET /health -> {"ok": true, "version": ..., "instance": ..., "port": ...}
POST /notes {"title": "..."} -> 201 {"id": n, "title": "..."}; appends to DIR/notes.jsonl
GET /notes -> {"notes": [...]}
"""
import argparse
import json
import os
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.2.0"


class Server(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind resolves socket.getfqdn(host), a reverse-DNS lookup
        # that can stall for many seconds on a macOS runner; the name is never used.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_handler(data_dir, instance, port):
    store = os.path.join(data_dir, "notes.jsonl")

    def load():
        if not os.path.isfile(store):
            return []
        with open(store, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, doc):
            body = json.dumps(doc).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True, "version": VERSION,
                                        "instance": instance, "port": port})
            if self.path == "/notes":
                return self._send(200, {"notes": load()})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/notes":
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                doc = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "body is not JSON"})
            title = (doc.get("title") or "").strip() if isinstance(doc, dict) else ""
            if not title:
                return self._send(400, {"error": "title is required"})
            note = {"id": len(load()) + 1, "title": title}
            with open(store, "a", encoding="utf-8") as f:
                f.write(json.dumps(note) + "\n")
            return self._send(201, note)

    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--instance", default="default")
    a = p.parse_args()
    os.makedirs(a.data, exist_ok=True)
    sys.stderr.write("notes: binding 127.0.0.1:%d\n" % a.port)
    sys.stderr.flush()
    server = Server(("127.0.0.1", a.port), make_handler(a.data, a.instance, a.port))
    sys.stderr.write("notes: listening\n")
    sys.stderr.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
