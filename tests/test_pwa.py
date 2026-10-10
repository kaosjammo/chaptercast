"""Shell transport and isolated browser-API contract tests; no device claims."""

import shutil
import struct
import subprocess

import pytest
from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings


def test_installable_shell_resources(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        shell = client.get('/')
        assert 'rel="manifest"' in shell.text
        assert 'offline-limits' in shell.text
        manifest = client.get('/static/manifest.webmanifest')
        assert manifest.headers['content-type'].startswith('application/manifest+json')
        data = manifest.json()
        assert data['start_url'] == '/' and data['scope'] == '/'
        assert data['display'] == 'standalone'
        assert {icon['sizes'] for icon in data['icons']} == {'192x192', '512x512'}
        for icon in data['icons']:
            response = client.get(icon['src'])
            assert response.headers['content-type'] == 'image/png'
            assert response.content[:8] == b'\x89PNG\r\n\x1a\n'
            size = int(icon['sizes'].split('x')[0])
            assert struct.unpack('>II', response.content[16:24]) == (size, size)
        worker = client.get('/sw.js')
        assert worker.status_code == 200
        assert 'javascript' in worker.headers['content-type']
        assert worker.headers['cache-control'] == 'no-store'
        assert client.get('/api/library').headers['cache-control'] == 'no-store'


def test_browser_api_contracts():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for isolated service-worker/Media Session contract checks')
    result = subprocess.run([node, '--test', 'tests/js/pwa.test.cjs'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
