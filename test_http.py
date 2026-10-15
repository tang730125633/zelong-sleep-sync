"""Synthetic-only end-to-end checks; binds localhost, never contacts real services."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).parent / 'server'))
with tempfile.TemporaryDirectory() as temp:
    os.environ['HEALTH_DATA_DIR'] = temp
    import app
    server = app.make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, token='', body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path, data=data,
            headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)
    payload = {'data': {'native': {'version': 1, 'start': '2026-01-01T00:00:00+08:00',
        'end': '2026-01-03T12:00:00+08:00', 'batches': [[
            {'kind': 'Sleep', 'value': '核心睡眠', 'unit': '', 'source': '',
             'start': '2026-01-01T23:00:00+08:00', 'end': '2026-01-02T06:00:00+08:00'}
        ]]}}}
    try:
        assert request('/api/health')[0] == 401
        assert request('/api/health', server.upload_key)[0] == 401
        assert request('/api/health/import', server.read_key, payload)[0] == 401
        status, first = request('/api/health/import', server.upload_key, payload)
        assert status == 200 and first['changed'] > 0
        assert request('/api/health/import', server.upload_key, payload)[1]['changed'] == 0
        snap = request('/api/health', server.read_key)[1]
        assert snap['sleep'][0]['day'] == '2026-01-02'
        assert snap['sleep'][0]['totalSleep'] == 7
        assert '未提供' in snap['sleep'][0]['sources']
        before = snap['sleep']
        bad = copy.deepcopy(payload)
        bad['data']['native']['batches'][0][0]['value'] = 'unsupported stage'
        assert request('/api/health/import', server.upload_key, bad)[0] == 400
        assert request('/api/health', server.read_key)[1]['sleep'] == before
        for raw in (b'{', b'[]', b'{"data":null}', b'{"data":{"native":null}}', b'{"data":NaN}'):
            assert request('/api/health/import', server.upload_key, raw=raw)[0] == 400
        bad = copy.deepcopy(payload)
        bad['data']['native']['batches'][0][0]['kind'] = 'Steps'
        assert request('/api/health/import', server.upload_key, bad)[0] == 400
        call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'get_health_history', 'arguments': {'metric': 'sleep_analysis',
                'start': '2026-01-01', 'end': '2026-01-03'}}}
        assert request('/api/health/mcp', server.upload_key, call)[0] == 401
        status, reply = request('/api/health/mcp', server.read_key, call)
        assert status == 200 and reply['result']['isError'] is False
        records = json.loads(reply['result']['content'][0]['text'])['records']
        assert len(records) == 1 and records[0]['totalSleep'] == 7
        assert request('/api/health/sleep?day=bad', server.read_key)[0] == 400
        assert request('/api/health/sleep?day=2026-01-02', server.read_key)[1]['segments']
        for path in Path(temp).iterdir():
            assert path.stat().st_mode & 0o077 == 0
        print('PASS: HTTP import, replay, read, MCP, separate credentials, invalid-data rollback and private file permissions')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
