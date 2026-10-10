"""Local pinned Kokoro, interruptible CPU inference and bounded FFmpeg export.

Heavy libraries live in one spawned child; the server never imports torch.
Model setup is explicit. Generation never downloads or uses a GPU.
"""
from importlib.metadata import PackageNotFoundError, version
import json
import multiprocessing
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time
import wave

from chaptercast.jobs import file_digest

MODEL_REPO = 'hexgrad/Kokoro-82M'
MODEL_REVISION = 'f3ff3571791e39611d31c381e3a41a3af07b4987'
MODEL_FILES = ('config.json', 'kokoro-v1_0.pth', *(f'voices/{v}.pt' for v in ('bf_emma', 'bf_isabella', 'bm_george', 'bm_lewis')))
SAMPLE_RATE = 24000


def render_blocks(text, blocks, destination, check, *, max_bytes):
    """Consume every yield, rejecting skipped text and malformed/oversize audio."""
    import numpy as np
    destination = Path(destination)
    count, frames, covered = 0, 0, []
    try:
        check()
        if max_bytes < 44:
            raise ValueError('chunk byte budget')
        with wave.open(str(destination), 'wb') as output:
            output.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
            for block in blocks:
                check()
                audio = np.asarray(block.audio)
                if not block.phonemes or audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                    raise ValueError('invalid Kokoro block')
                frames += audio.size
                if 44 + frames * 2 > max_bytes or frames > SAMPLE_RATE * 300:
                    raise ValueError('chunk byte/duration budget')
                pcm = (np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes()
                output.writeframes(pcm)
                covered.append(block.graphemes)
                count += 1
        # Kokoro strips paragraph/chunk whitespace. Every other character must
        # appear in the yielded graphemes, in order; partial output fails closed.
        compact = lambda value: ''.join(value.split())
        if count == 0 or compact(''.join(covered)) != compact(text):
            raise ValueError('Kokoro text coverage mismatch')
        check()
        return {'blocks': count, 'frames': int(frames), 'coverage': True,
                'duration_seconds': frames / SAMPLE_RATE}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _engine(connection, model_dir, threads):
    """Spawn entry point: no internet, bounded CPU threads, one active request."""
    try:
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                          HF_HOME=str(model_dir / 'hub'), CUDA_VISIBLE_DEVICES='',
                          OMP_NUM_THREADS=str(threads), MKL_NUM_THREADS=str(threads),
                          TOKENIZERS_PARALLELISM='false')
        import torch
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        torch.manual_seed(0)
        from loguru import logger
        logger.remove()  # Third-party debug messages can contain private text.
        import spacy
        if not spacy.util.is_package('en_core_web_sm'):
            raise RuntimeError('Install the tts extra before model setup')
        from kokoro import KModel, KPipeline
        from misaki.espeak import EspeakFallback
        # Require the bundled local fallback rather than allowing Misaki to
        # silently substitute a network/GPU fallback when espeak is unavailable.
        EspeakFallback(british=True)
        model = KModel(repo_id=MODEL_REPO, config=str(model_dir / 'config.json'),
                       model=str(model_dir / 'kokoro-v1_0.pth')).to('cpu').eval()
        pipeline = KPipeline(lang_code='b', repo_id=MODEL_REPO, model=model)
        if not isinstance(pipeline.g2p.fallback, EspeakFallback):
            raise RuntimeError('Local espeak fallback required')
        while True:
            text, settings, destination, max_bytes = connection.recv()
            voice = str(model_dir / 'voices' / f'{settings.voice}.pt')
            with torch.inference_mode():
                report = render_blocks(text, pipeline(text, voice=voice, speed=settings.speed),
                                       destination, lambda: None, max_bytes=max_bytes)
            connection.send({'ok': True, **report, 'threads': torch.get_num_threads(),
                             'interop_threads': torch.get_num_interop_threads(), 'device': str(model.device)})
    except EOFError:
        pass
    except BaseException:
        # Stable failure only: never return private text, paths or library errors.
        try:
            connection.send({'ok': False})
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class KokoroBackend:
    def __init__(self, model_dir: Path, *, threads: int = 4, operation_seconds: float = 120):
        if type(threads) is not int or not 1 <= threads <= 4:
            raise ValueError('inference threads must be between 1 and 4')
        self.model_dir = Path(model_dir).resolve()
        self.threads, self.operation_seconds = threads, operation_seconds
        self._process = self._connection = None
        self.last_render = None

    def close(self):
        if self._process is not None:
            self._process.terminate()
            self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=2)
            self._process.close()
            self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def synthesize(self, text, settings, destination, check, *, max_bytes):
        started = time.monotonic()
        try:
            check()
            if self._process is None:
                context = multiprocessing.get_context('spawn')
                self._connection, child = context.Pipe()
                self._process = context.Process(target=_engine, args=(child, self.model_dir, self.threads), daemon=True)
                self._process.start()
                child.close()
            self._connection.send((text, settings, destination, max_bytes))
            while not self._connection.poll(0.05):
                check()
                if not self._process.is_alive():
                    raise RuntimeError('Kokoro process exited')
                if time.monotonic() - started > self.operation_seconds:
                    raise TimeoutError('Kokoro operation deadline')
            report = self._connection.recv()
            check()
            if not report['ok']:
                raise RuntimeError('Kokoro rendering failed')
            self.last_render = {**report, 'generation_seconds': time.monotonic() - started}
        except BaseException:
            self.close()
            Path(destination).unlink(missing_ok=True)
            raise

    def encode(self, chunks, settings, destinations, check, *, max_bytes):
        if not chunks or set(destinations) != set(settings.formats):
            raise ValueError('missing ordered chunks or output formats')
        # Concat demuxer reads the existing checkpoints; only a small path list
        # is temporary. stdout is drained in bounded blocks to cap peak disk use.
        manifest = next(iter(destinations.values())).parent / 'checkpoints.ffconcat'
        process = None
        try:
            check()
            content = 'ffconcat version 1.0\n' + ''.join(
                "file '" + str(path.resolve()).replace("'", "'\\''") + "'\n" for path in chunks)
            used = len(content.encode())
            if used > max_bytes:
                raise ValueError('encoding byte budget')
            manifest.write_text(content)
            manifest.chmod(0o600)
            for format, destination in destinations.items():
                check()
                codec = ['-c:a', 'libopus', '-b:a', '32k', '-vbr', 'off', '-f', 'ogg'] if format == 'opus' else [
                    '-c:a', 'aac', '-b:a', '48k', '-f', 'mp4', '-movflags', 'empty_moov+frag_keyframe+default_base_moof', '-frag_duration', '1000000']
                command = ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-f', 'concat', '-safe', '0',
                           '-i', str(manifest), '-map', '0:a:0', '-map_metadata', '-1', '-ac', '1',
                           '-threads', '1', *codec, 'pipe:1']
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                os.set_blocking(process.stdout.fileno(), False)
                started = time.monotonic()
                with selectors.DefaultSelector() as selector, destination.open('wb') as output:
                    destination.chmod(0o600)
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        check()
                        if time.monotonic() - started > self.operation_seconds:
                            raise TimeoutError('FFmpeg operation deadline')
                        if not selector.select(timeout=0.05):
                            continue
                        data = os.read(process.stdout.fileno(), 65536)
                        if not data:
                            break
                        if used + len(data) > max_bytes:
                            raise ValueError('encoding byte budget')
                        output.write(data)
                        used += len(data)
                process.wait(timeout=2)
                if process.returncode:
                    raise RuntimeError('FFmpeg encoding failed')
                process.stdout.close()
                process = None
            check()
        except BaseException:
            if process is not None:
                process.kill()
                process.wait(timeout=2)
                process.stdout.close()
            for path in destinations.values():
                path.unlink(missing_ok=True)
            raise
        finally:
            manifest.unlink(missing_ok=True)


def available_backend(model_dir, threads=4):
    """Ready only with installed dependencies, exact provenance and intact files."""
    try:
        for package, expected in (('kokoro', '0.9.4'), ('misaki', '0.9.4'), ('en-core-web-sm', '3.8.0')):
            if version(package) != expected:
                return None
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            return None
        manifest = json.loads((model_dir / 'provenance.json').read_text())
        if manifest['repository'] != MODEL_REPO or manifest['revision'] != MODEL_REVISION:
            return None
        for name in MODEL_FILES:
            path = model_dir / name
            if path.stat().st_size != manifest['files'][name]['bytes'] or file_digest(path) != manifest['files'][name]['sha256']:
                return None
    except (OSError, KeyError, ValueError, TypeError, PackageNotFoundError):
        return None
    return KokoroBackend(model_dir, threads=threads)


def setup_models():
    """Explicit one-time setup; downloaded assets and source/version stay private."""
    from huggingface_hub import hf_hub_download
    from chaptercast.config import Settings
    model_dir = Settings.from_env().data_dir / 'models'
    model_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = {}
    for name in MODEL_FILES:
        path = Path(hf_hub_download(MODEL_REPO, filename=name, revision=MODEL_REVISION,
                                   local_dir=model_dir, etag_timeout=20, token=False))
        path.chmod(0o600)
        files[name] = {'bytes': path.stat().st_size, 'sha256': file_digest(path)}
    provenance = {'repository': MODEL_REPO, 'revision': MODEL_REVISION, 'files': files,
                  'packages': {p: version(p) for p in ('kokoro', 'misaki', 'torch', 'transformers', 'spacy', 'en-core-web-sm', 'espeakng-loader')},
                  'spacy_source': 'https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0'}
    path = model_dir / 'provenance.json'
    path.write_text(json.dumps(provenance, indent=2) + '\n')
    path.chmod(0o600)
    print('Local pinned Kokoro model and four British voices are ready.')
