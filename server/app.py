"""Single-owner reference HTTP service; put an HTTPS reverse proxy in front."""
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

import wellbeing
import health_mcp

MAX_BODY = 4_000_000


def key_file(name):
    wellbeing.DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = wellbeing.DATA / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(40))
    except FileExistsError:
        pass
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('Credential files must be private regular files (chmod 600)')
    value = path.read_text().strip()
    if len(value) < 32:
        raise ValueError('Credential is empty or too short')
    return value


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *_):
        pass  # No payloads, tokens, query strings or health records in access logs.

    def send(self, status, value):
        self.close_connection = True
        body = b'' if value is None else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, expected):
        value = self.headers.get('Authorization', '')
        return value.startswith('Bearer ') and hmac.compare_digest(value[7:].encode(), expected.encode())

    def do_GET(self):
        if not self.authorized(self.server.read_key):
            return self.send(401, {'error': 'read_auth_required'})
        url = urlsplit(self.path)
        if url.path == '/api/health':
            return self.send(200, wellbeing.health_snapshot())
        if url.path == '/api/health/sleep':
            try:
                day = parse_qs(url.query).get('day', [''])[0]
                result = wellbeing.sleep_day_detail(day)
            except ValueError:
                return self.send(400, {'error': 'invalid_day'})
            return self.send(200 if result else 404, result or {'error': 'not_found'})
        return self.send(404, {'error': 'not_found'})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ('/api/health/import', '/api/health/mcp'):
            return self.send(404, {'error': 'not_found'})
        # This reference service has no browser frontend / cross-origin API.
        if self.headers.get('Origin'):
            return self.send(403, {'error': 'browser_origin_not_supported'})
        expected = self.server.upload_key if path.endswith('/import') else self.server.read_key
        if not self.authorized(expected):
            return self.send(401, {'error': 'auth_required'})
        if self.headers.get_content_type() != 'application/json':
            return self.send(415, {'error': 'json_required'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.send(400, {'error': 'invalid_length'})
        if self.headers.get('Transfer-Encoding') or not 0 < length <= MAX_BODY:
            return self.send(413, {'error': 'body_too_large_or_empty'})
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('incomplete body')
            def invalid_constant(_):
                raise ValueError('non-finite JSON number')
            body = json.loads(raw, parse_constant=invalid_constant)
            if not isinstance(body, dict):
                raise ValueError('body must be an object')
            if path.endswith('/mcp'):
                result = health_mcp.handle(body)
                return self.send(202 if result is None else 200, result)
            # Keep public ingestion limited to the device-validated Sleep path.
            batches = body.get('data', {}).get('native', {}).get('batches')
            if not isinstance(batches, list) or not batches or any(
                not isinstance(batch, list) or any(not isinstance(row, dict) or row.get('kind') != 'Sleep' for row in batch)
                for batch in batches
            ):
                raise ValueError('native sleep samples required')
            result = wellbeing.import_sleep(body)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            if path.endswith('/import'):
                wellbeing.report_sync({'sync_source': 'iphone-native', 'sync_interval': 86400,
                                       'status': 'error', 'error': 'invalid_source_data'})
            return self.send(400, {'error': 'invalid_source_data'})
        return self.send(200, result)


def make_server(port=8768):
    os.umask(0o077)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.upload_key = key_file('upload.key')
    server.read_key = key_file('read.key')
    if hmac.compare_digest(server.upload_key, server.read_key):
        server.server_close()
        raise ValueError('Upload and read credentials must be different')
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f'Listening on 127.0.0.1:{server.server_port}; private files in {wellbeing.DATA.resolve()}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
