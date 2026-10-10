"""Original synthetic blocks test adapter contracts, never live TTS acceptance."""
from types import SimpleNamespace
import wave

import pytest

from chaptercast.jobs import GenerationSettings, PIPELINE_VERSION
from chaptercast.tts import render_blocks, KokoroBackend
from test_jobs import store


def blocks(*texts):
    import numpy as np
    return [SimpleNamespace(graphemes=text, phonemes='test', audio=np.ones(2400, dtype=np.float32) * 0.1) for text in texts]


def test_all_yields_are_written_and_coverage_is_checked(tmp_path):
    path = tmp_path / 'chunk.wav'
    report = render_blocks('First sentence. Second sentence.', blocks('First sentence.', 'Second sentence.'), path, lambda: None, max_bytes=10000)
    with wave.open(str(path)) as audio:
        assert audio.getnframes() == 4800
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
    assert report['blocks'] == 2 and report['coverage'] is True
    with pytest.raises(ValueError, match='coverage'):
        render_blocks('First sentence. Second sentence.', blocks('First sentence.'), path, lambda: None, max_bytes=10000)
    assert not path.exists()


def test_peak_chunk_budget_and_cancellation_cleanup(tmp_path):
    path = tmp_path / 'chunk.wav'
    with pytest.raises(ValueError, match='budget'):
        render_blocks('First. Second.', blocks('First.', 'Second.'), path, lambda: None, max_bytes=5000)
    assert not path.exists()
    def cancel():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        render_blocks('First.', blocks('First.'), path, cancel, max_bytes=5000)
    assert not path.exists()


def test_pipeline_cannot_reuse_queue_v1():
    assert PIPELINE_VERSION != 'kokoro-82m:queue-v1'
    assert PIPELINE_VERSION in GenerationSettings().serialise()


def test_thread_bounds(tmp_path):
    for count in (0, 5, True):
        with pytest.raises(ValueError):
            KokoroBackend(tmp_path, threads=count)


def test_real_ffmpeg_streams_ordered_checkpoints_with_combined_budget(tmp_path):
    import shutil
    from chaptercast.audio_validation import validate_output
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg/ffprobe required; synthetic PCM is not real TTS')
    paths = []
    for i, text in enumerate(('First.', 'Second.')):
        path = tmp_path / f"chunk'{i}.wav"
        render_blocks(text, blocks(text), path, lambda: None, max_bytes=5000)
        paths.append(path)
    backend = KokoroBackend(tmp_path)
    outputs = {f: tmp_path / f'chapter.{f}' for f in ('opus', 'm4a')}
    backend.encode(paths, GenerationSettings(), outputs, lambda: None, max_bytes=100000)
    for format, path in outputs.items():
        assert abs(validate_output(path, format, 100000, 0.2).duration_seconds - 0.2) < 0.1
    assert not list(tmp_path.glob('*.ffconcat'))
    # Both destinations AND the path manifest must fit the same peak allowance.
    for path in outputs.values():
        path.unlink()
    with pytest.raises(ValueError, match='budget'):
        backend.encode(paths, GenerationSettings(), outputs, lambda: None, max_bytes=100)
    assert not any(path.exists() for path in outputs.values())
    with pytest.raises(ValueError, match='budget'):
        backend.encode(paths, GenerationSettings(), outputs, lambda: None, max_bytes=2000)
    assert not any(path.exists() for path in outputs.values())


def test_encoding_cancellation_removes_all_outputs_and_manifest(tmp_path):
    import shutil
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    path = tmp_path / 'chunk.wav'
    render_blocks('First.', blocks('First.'), path, lambda: None, max_bytes=5000)
    outputs = {f: tmp_path / f'chapter.{f}' for f in ('opus', 'm4a')}
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        if checks == 4:
            raise InterruptedError('original cancellation fixture')
    with pytest.raises(InterruptedError):
        KokoroBackend(tmp_path).encode([path], GenerationSettings(), outputs, cancel, max_bytes=100000)
    assert not any(p.exists() for p in outputs.values())
    assert not list(tmp_path.glob('*.ffconcat'))


def test_preview_uses_queue_and_reuses_private_sample(store):
    first = store.preview(GenerationSettings())
    assert store.preview(GenerationSettings())['id'] == first['id']
    assert store.preview(GenerationSettings(speed=1.25))['id'] != first['id']


def test_unavailable_preview_is_explicit(tmp_path):
    from fastapi.testclient import TestClient
    from chaptercast.app import create_app
    from chaptercast.config import Settings
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        response = client.post('/api/jobs/preview', json={})
        assert response.status_code == 503
        assert response.json()['code'] == 'backend_unavailable'
        assert client.post('/api/jobs/preview', json={'text': 'Original unsupported fixture.'}).status_code == 400


def test_only_pinned_intact_models_enable_default_backend(tmp_path, monkeypatch):
    import json
    import chaptercast.tts as tts
    from chaptercast.jobs import file_digest
    versions = {'kokoro': '0.9.4', 'misaki': '0.9.4', 'en-core-web-sm': '3.8.0'}
    monkeypatch.setattr(tts, 'version', versions.__getitem__)
    monkeypatch.setattr(tts.shutil, 'which', lambda name: '/original/test/tool')
    files = {}
    for name in tts.MODEL_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'Original synthetic provenance fixture, never a model.')
        files[name] = {'bytes': path.stat().st_size, 'sha256': file_digest(path)}
    manifest = tmp_path / 'provenance.json'
    manifest.write_text(json.dumps({'repository': tts.MODEL_REPO, 'revision': tts.MODEL_REVISION, 'files': files}))
    assert tts.available_backend(tmp_path) is not None
    (tmp_path / tts.MODEL_FILES[0]).write_bytes(b'Original damaged fixture')
    assert tts.available_backend(tmp_path) is None
    manifest.write_text('null')
    assert tts.available_backend(tmp_path) is None


def test_old_pipeline_is_not_restorable():
    import json
    from chaptercast.jobs import JobFailure
    data = json.loads(GenerationSettings().serialise())
    data['pipeline'] = 'kokoro-82m:queue-v1'
    with pytest.raises(JobFailure, match='unsupported pipeline'):
        GenerationSettings.restore(json.dumps(data))


def stalled_engine(connection, model_dir, threads):
    """Original unit stand-in for a hung library call, never TTS evidence."""
    import time
    from pathlib import Path
    request = connection.recv()
    Path(request[2]).write_bytes(b'Original incomplete unit fixture')
    (model_dir / 'unit-child-started').touch()
    time.sleep(20)


@pytest.mark.parametrize('interrupt', ['timeout', 'cancel'])
def test_parent_terminates_stalled_child_and_removes_partial(tmp_path, monkeypatch, interrupt):
    import time
    import chaptercast.tts as tts
    monkeypatch.setattr(tts, '_engine', stalled_engine)
    backend = tts.KokoroBackend(tmp_path, operation_seconds=2)
    path = tmp_path / 'chunk.part'
    def check():
        if interrupt == 'cancel' and (tmp_path / 'unit-child-started').exists():
            raise InterruptedError('Original unit cancellation fixture')
    started = time.monotonic()
    with pytest.raises(TimeoutError if interrupt == 'timeout' else InterruptedError):
        backend.synthesize('Original unit fixture.', GenerationSettings(), path, check, max_bytes=1000)
    assert time.monotonic() - started < 5
    assert (tmp_path / 'unit-child-started').exists()
    assert backend._process is None and backend._connection is None
    assert not path.exists()
