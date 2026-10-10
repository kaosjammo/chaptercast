"""Opt-in REAL Kokoro/FFmpeg acceptance, entirely in ignored local paths.

Run after installing dev+tts extras and chaptercast-models. No mock replaces
inference. The fixture below is original test prose, not a redistributed work.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.database import connect, initialise
from chaptercast.jobs import GenerationSettings, JobStore, PIPELINE_VERSION, file_digest
from chaptercast.tts import available_backend
from chaptercast.worker import Worker

EVIDENCE = Path('.evidence/issue-4').resolve()
MODELS = Path('.runtime/models').resolve()


def source_identity():
    def git(*args):
        return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout.strip()
    names = git('ls-files', '--cached', '--others', '--exclude-standard').splitlines()
    return {'head': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
            'status': git('status', '--short'),
            'files': {name: file_digest(Path(name)) for name in names if Path(name).is_file()}}


def original_chapter():
    """Original, clearly labelled multi-paragraph chapter-length test input."""
    paragraphs = ['Original ChapterCast acceptance fixture. The workshop journey.']
    for place, object_name in [
        ('garden', 'lantern'), ('bridge', 'compass'), ('orchard', 'basket'),
        ('library', 'notebook'), ('harbour', 'rope'), ('tower', 'clock'),
        ('meadow', 'ribbon'), ('workshop', 'kettle'), ('courtyard', 'bell'),
        ('greenhouse', 'flowerpot'), ('station', 'ticket'), ('kitchen', 'teapot'),
    ]:
        paragraphs.append(
            f'Mara reached the {place} just after breakfast. She carried a {object_name} in a small canvas bag, '
            'along with a pencil and a folded map. The morning was cool, but the clouds were beginning to part. '
            'A neighbour waved from the path and asked whether she needed help. She smiled and explained her plan: '
            'today she would repair something useful, learn something new, and leave the place a little better than she had found it. '
            'They worked slowly, checking each step before moving on. When the task was finished, they sat together and shared an apple. '
            'There was no grand adventure to announce, only the quiet satisfaction of a promise kept. '
            'Mara wrote a short note in her book, packed the bag, and continued towards the next stop.')
    paragraphs.append('At sunset, Mara returned home. Every stop was recorded, and every tool was put away. '
                      'She opened the window to the evening air and listened to the birds settling in the hedge. '
                      'Tomorrow would bring its own small tasks. For now, the journey was complete. This is the final sentence of the original test chapter.')
    return '\n\n'.join(paragraphs)


def init_store(root):
    for name in ('uploads', 'audio'):
        (root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    initialise(root / 'metadata.sqlite3')
    return JobStore(root)


def import_fixture(store, text):
    path = store.root / 'uploads/original-fixture.txt'
    path.write_text(text)
    path.chmod(0o600)
    with connect(store.database) as db:
        return db.execute('INSERT INTO chapters (title,text_path) VALUES (?,?)',
                          ('Original ChapterCast acceptance fixture', 'uploads/original-fixture.txt')).lastrowid


def media(path):
    probe_argv = ['ffprobe', '-v', 'error', '-show_entries',
                  'stream=codec_name,channels,sample_rate,bit_rate:format=duration,size,format_name', '-of', 'json', str(path)]
    probe = subprocess.run(probe_argv, capture_output=True, check=True, timeout=30)
    decode_argv = ['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-threads', '1', '-i', str(path),
                   '-ac', '1', '-ar', '24000', '-f', 's16le', 'pipe:1']
    decoded_bytes = 0
    with subprocess.Popen(decode_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        for block in iter(lambda: process.stdout.read(65536), b''):
            decoded_bytes += len(block)
        decode_exit = process.wait(timeout=60)
        assert decode_exit == 0
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': file_digest(path),
            'ffprobe': json.loads(probe.stdout), 'ffprobe_argv': probe_argv, 'ffprobe_exit': probe.returncode,
            'decode_argv': decode_argv, 'decode_exit': decode_exit,
            'decoded_duration_seconds': decoded_bytes / 48000}


class ObservedBackend:
    """Record real calls/results while delegating every operation to Kokoro."""
    def __init__(self, backend):
        self.backend = backend
        self.renders = []
        self.call_checks = 0
        self.encoded_inputs = []
        self.peak_artifact_bytes = 0

    def measure(self, work):
        size = sum(p.stat().st_size for p in work.rglob('*') if p.is_file())
        self.peak_artifact_bytes = max(self.peak_artifact_bytes, size)

    def synthesize(self, text, settings, destination, check, **kwargs):
        self.call_checks = 0
        def monitored_check():
            self.call_checks += 1
            self.measure(destination.parent)
            check()
        self.backend.synthesize(text, settings, destination, monitored_check, **kwargs)
        self.call_checks = 0
        self.renders.append({'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                             'characters': len(text), **self.backend.last_render})

    def encode(self, *args, **kwargs):
        self.encoded_inputs = [{'path': str(p), 'sha256': file_digest(p)} for p in args[0]]
        chunks, settings, destinations, check = args
        def monitored_check():
            self.measure(chunks[0].parent)
            check()
        return self.backend.encode(chunks, settings, destinations, monitored_check, **kwargs)

    def close(self):
        self.backend.close()


def backend():
    result = available_backend(MODELS)
    assert result is not None, 'Install dev+tts and set up models first'
    return ObservedBackend(result)


def run_complete(root):
    observer = backend()
    store = init_store(root)
    worker = Worker(store, observer)
    chapter = original_chapter()
    started = time.monotonic()
    job = store.enqueue(import_fixture(store, chapter), GenerationSettings(speed=1.1))
    assert worker.run_once()
    generation_seconds = time.monotonic() - started
    job = store.get(job['id'])
    assert job['status'] == 'completed', job
    with connect(store.database) as db:
        chunks = [r[0] for r in db.execute('SELECT text FROM job_chunks WHERE job_id=? ORDER BY position', (job['id'],))]
        rows = list(db.execute('SELECT duration_seconds FROM job_chunks WHERE job_id=? ORDER BY position', (job['id'],)))
    assert ''.join(chunks) == chapter
    assert [r['text_sha256'] for r in observer.renders] == [hashlib.sha256(c.encode()).hexdigest() for c in chunks]
    assert all(r['coverage'] and r['device'] == 'cpu' and r['threads'] <= 4 for r in observer.renders)
    assert any(r['blocks'] > 1 for r in observer.renders), 'Exercise multiple Kokoro yields'
    assert observer.peak_artifact_bytes <= store.limits.job_bytes
    return {'job': job, 'input_path': str(root / 'uploads/original-fixture.txt'),
            'original_fixture': True, 'paragraphs': len(chapter.split('\n\n')), 'words': len(chapter.split()),
            'characters': len(chapter), 'generation_seconds': generation_seconds,
            'exact_chunk_reconstruction': True, 'all_graphemes_covered_in_order': True,
            'checkpoint_duration_seconds': sum(r[0] for r in rows), 'renders': observer.renders,
            'observed_peak_artifact_bytes': observer.peak_artifact_bytes,
            'outputs': {f: media(store.output(job['id'], f)) for f in ('opus', 'm4a')}}


def run_preview(root):
    observer = backend()
    started = time.monotonic()
    # Exercise the real HTTP route, server-owned queue and download gate.
    with TestClient(create_app(Settings(data_dir=root), backend=observer)) as client:
        assert client.get('/api/jobs/worker').json()['backend_ready']
        response = client.post('/api/jobs/preview', json={'voice': 'bf_emma', 'speed': 1.0})
        assert response.status_code == 202
        job = response.json()
        deadline = time.monotonic() + 180
        while job['status'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.05)
            job = client.get(f"/api/jobs/{job['id']}").json()
        assert job['status'] == 'completed', job
        generation_seconds = time.monotonic() - started
        assert client.post('/api/jobs/preview', json={}).json()['id'] == job['id']
        for output in job['outputs']:
            assert client.get(output['url']).status_code == 200
    store = JobStore(root)
    return {'job': job, 'voice': 'bf_emma', 'speed': 1.0, 'http_submission_exit': 202,
            'cache_reused': True, 'downloads_http_status': 200, 'generation_seconds': generation_seconds,
            'renders': observer.renders,
            'outputs': {f: media(store.output(job['id'], f)) for f in ('opus', 'm4a')}}


def interrupted_run(root, cancel):
    store = init_store(root)
    observer = backend()
    # After checkpoint one is committed, interrupt INSIDE actual second model
    # inference, not a fake or waiting adapter. The check runs in the real parent
    # monitor while the child is working and has not returned its report.
    interrupted = False
    interrupted_at = None
    worker = Worker(store, observer)
    # Two full original paragraphs suffice to exercise missing-chunk recovery;
    # the separate chapter run already covers the complete long input.
    text = '\n\n'.join(original_chapter().split('\n\n')[:3])
    job = store.enqueue(import_fixture(store, text), GenerationSettings(speed=1.2 if cancel else 1.15))
    original_check = worker._check
    def check(identifier, deadline):
        nonlocal interrupted, interrupted_at
        if not interrupted and observer.call_checks >= 2 and store.get(identifier)['completed_chunks'] == 1:
            process = observer.backend._process
            if process is not None and process.is_alive() and not observer.backend._connection.poll():
                interrupted = True
                interrupted_at = time.monotonic()
                if cancel:
                    store.cancel(identifier)
                else:
                    worker._stop.set()
        original_check(identifier, deadline)
    worker._check = check
    started = time.monotonic()
    assert worker.run_once()
    interrupted_seconds = time.monotonic() - started
    state = store.get(job['id'])
    assert interrupted
    assert state['status'] == ('cancelled' if cancel else 'queued'), state
    assert state['completed_chunks'] == 1 and state['outputs'] == []
    assert observer.backend._process is None
    assert not list((root / 'audio/.work' / str(job['id'])).glob('*.part'))
    with connect(store.database) as db:
        first = db.execute('SELECT path,sha256,text FROM job_chunks WHERE job_id=? AND position=0', (job['id'],)).fetchone()
        remaining = [r[0] for r in db.execute('SELECT content_key FROM job_chunks WHERE job_id=? AND position>0 ORDER BY position', (job['id'],))]
    receipt = {'status': state['status'], 'completed_chunks': 1, 'outputs_hidden': True,
               'real_inference_child_terminated': True, 'partial_removed': True,
               'attempt_seconds': interrupted_seconds, 'first_checkpoint_sha256': first[1]}
    receipt['interruption_to_settled_seconds'] = time.monotonic() - interrupted_at
    if not cancel:
        resumed = backend()
        started = time.monotonic()
        assert Worker(store, resumed).run_once()
        assert store.get(job['id'])['status'] == 'completed'
        assert [r['text_sha256'] for r in resumed.renders] == remaining
        assert resumed.encoded_inputs[0]['sha256'] == first[1]
        # Worker removes checkpoints after successful publication; no call for
        # first text plus exact remaining snapshot hashes proves it was reused.
        receipt.update(resume_seconds=time.monotonic() - started, resume_completed=True,
                       first_chunk_not_resynthesized=True, remaining_chunks=len(resumed.renders),
                       outputs={f: media(store.output(job['id'], f)) for f in ('opus', 'm4a')})
    return receipt


def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    root = EVIDENCE / stamp
    root.mkdir(mode=0o700, parents=True)
    result = {'timestamp_utc': stamp, 'pipeline': PIPELINE_VERSION, 'model_provenance_path': str(MODELS / 'provenance.json'),
              'model_provenance': json.loads((MODELS / 'provenance.json').read_text()),
              'limits': asdict(JobStore(root).limits), 'mock_tts': False, 'source': source_identity()}
    try:
        for name, action in [('preview', run_preview), ('chapter', run_complete),
                             ('cancellation', lambda p: interrupted_run(p, True)),
                             ('checkpoint_resume', lambda p: interrupted_run(p, False))]:
            print(f'Running real {name}...', flush=True)
            result[name] = action(root / name)
            (EVIDENCE / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
            print(f'{name}: verified', flush=True)
        assert source_identity() == result['source'], 'Source changed during acceptance'
        result['result'] = 'PASS_LOCAL_REAL_TTS'
    except BaseException as exc:
        result.update(result='FAIL_OR_INCOMPLETE', failure_type=type(exc).__name__)
        raise
    finally:
        (EVIDENCE / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
        (EVIDENCE / 'acceptance.json').chmod(0o600)


if __name__ == '__main__':
    main()
