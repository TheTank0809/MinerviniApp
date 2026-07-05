"""Tiny static server for local preview of the site (docs/)."""
import os
import http.server

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
os.chdir(DOCS)

http.server.ThreadingHTTPServer(
    ("127.0.0.1", 8123), http.server.SimpleHTTPRequestHandler
).serve_forever()
