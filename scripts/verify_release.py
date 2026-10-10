"""Opt-in local real preview and retained chapter/download qualification.

Requires dev+tts, FFmpeg and verified local models. Arguments identify PRIVATE
original issue #4 evidence. It copies that evidence and never regenerates the
long chapter. All new artifacts go under ignored .evidence/issue-7/.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from fastapi.testclient import TestClient

from chaptercast.app import create_app
from chaptercast.config import Settings
from chaptercast.database import connect
from chaptercast.jobs import PIPELINE_VERSION, file_digest
from chaptercast.tts import available_backend
from verify_tts import ObservedBackend, media, source_identity


def snapshot(root):
    return {str(p.relative_to(root)): file_digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retained-chapter", type=Path, required=True)
    parser.add_argument("--retained-receipt", type=Path, required=True)
    parser.add_argument("--models", type=Path, default=Path(".runtime/models"))
    args = parser.parse_args()
    source = args.retained_chapter.resolve()
    receipt = json.loads(args.retained_receipt.read_text())
    before = snapshot(source)
    # Bind reuse to the unchanged rendering code, dependency lock and pipeline.
    rendering = ["src/chaptercast/tts.py", "src/chaptercast/worker.py",
                 "src/chaptercast/audio_validation.py", "src/chaptercast/chunks.py", "uv.lock"]
    for name in rendering:
        assert file_digest(Path(name)) == receipt["source"]["files"][name], f"Rendering source changed: {name}"
    assert receipt["result"] == "PASS_LOCAL_REAL_TTS"
    assert receipt["chapter"]["original_fixture"] and receipt["chapter"]["all_graphemes_covered_in_order"]
    run = Path(".evidence/issue-7") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run.mkdir(parents=True, exist_ok=False)
    root = (run / "runtime").resolve()
    shutil.copytree(source, root)
    (root / "models").symlink_to(args.models.resolve(), target_is_directory=True)
    backend = available_backend(args.models.resolve())
    assert backend is not None, "Install dev+tts and supply verified local models"
    observer = ObservedBackend(backend)
    outputs = {}
    started = time.monotonic()
    with TestClient(create_app(Settings(data_dir=root), backend=observer), base_url="http://127.0.0.1") as client:
        assert client.get("/api/jobs/worker").json()["backend_ready"]
        # Real chapter bytes, gated by copied completed metadata and hashes.
        identifier = receipt["chapter"]["job"]["id"]
        with connect(root / "metadata.sqlite3") as db:
            settings = json.loads(db.execute("SELECT settings_json FROM jobs WHERE id=?", (identifier,)).fetchone()[0])
        assert settings["pipeline"] == PIPELINE_VERSION
        for format in ("opus", "m4a"):
            url = f"/api/jobs/{identifier}/outputs/{format}"
            response = client.get(url)
            assert response.status_code == 200 and "attachment;" in response.headers["content-disposition"]
            assert response.headers["cache-control"] == "no-store"
            assert len(response.content) == int(response.headers["content-length"])
            assert hashlib.sha256(response.content).hexdigest() == receipt["chapter"]["outputs"][format]["sha256"]
            destination = run / f"downloaded-chapter.{format}"
            destination.write_bytes(response.content)
            ranged = client.get(url, headers={"Range": "bytes=1000-1999"})
            assert ranged.status_code == 206 and ranged.content == response.content[1000:2000]
            assert client.get(url, headers={"Range": f"bytes={len(response.content)}-"}).status_code == 416
            outputs[format] = {"reused": True, "http_status": response.status_code,
                               "range_status": ranged.status_code, "complete_file": str(destination.resolve())}
        # One short fresh preview exercises real Kokoro + both FFmpeg encoders.
        response = client.post("/api/jobs/preview", json={"voice": "bf_emma", "formats": ["opus", "m4a"]})
        assert response.status_code == 202
        job = response.json()
        deadline = time.monotonic() + 180
        while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(.05)
            job = client.get(f"/api/jobs/{job['id']}").json()
        assert job["status"] == "completed", job
        assert observer.renders and all(r["coverage"] and r["device"] == "cpu" and r["threads"] <= 4 for r in observer.renders)
        for output in job["outputs"]:
            assert client.get(output["url"]).status_code == 200
        # A second chapter link supports browser verification of per-chapter resume.
        text = (root / "uploads/original-fixture.txt").read_text()
        imported = client.post("/api/import/text", json={"text": text, "title": "Original retained fixture, second library entry"})
        assert imported.status_code == 201
        linked = client.post("/api/jobs", json={"chapter_id": imported.json()["id"], "speed": settings["speed"]})
        assert linked.status_code == 202 and linked.json()["id"] == identifier
    # Application/worker are shut down. Decode the complete saved downloads.
    for format, output in outputs.items():
        output["after_application_shutdown"] = media(Path(output["complete_file"]))
    preview = {o["format"]: media(root / "audio/ready" / str(job["id"]) / f"chapter.{o['format']}") for o in job["outputs"]}
    assert snapshot(source) == before, "Retained source evidence changed"
    result = {"result": "PASS_LOCAL_REAL_PREVIEW_AND_RETAINED_DOWNLOAD", "source": source_identity(),
              "runtime": str(root), "retained_source": str(source), "retained_receipt_sha256": file_digest(args.retained_receipt),
              "retained_source_unchanged": True, "unchanged_rendering_files": rendering,
              "chapter_generation": {"reused": True, "seconds": receipt["chapter"]["generation_seconds"],
                                     "coverage": True, "chunks": receipt["chapter"]["job"]["total_chunks"]},
              "chapter_downloads": outputs, "preview": {"job": job, "renders": observer.renders, "outputs": preview},
              "total_seconds": time.monotonic() - started,
              "limits": ["In-process HTTP; native TCP/browser checks recorded separately", "Physical-phone, HTTPS deployment and screen-off checks remain controller gates"]}
    (run / "acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"result": result["result"], "evidence": str((run / "acceptance.json").resolve()), "runtime": str(root)}))


if __name__ == "__main__":
    main()
