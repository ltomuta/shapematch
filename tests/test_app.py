"""Catalog and job contracts use a fake worker; separate tests run spawned CAD."""
import json
from pathlib import Path
import threading
import time

import pytest

from shapematch_app import library as lib
from shapematch_app.library import Library, canonical, discover, shortlist
from shapematch_cache import source_hash, write_json


class FakeWorker:
    workers = 2

    def __init__(self):
        self.calls = []
        self.change = None
        self.block = False

    def run(self, operation, data, cancel=None):
        self.calls.append((operation, data))
        if self.block:
            while not cancel.is_set():
                time.sleep(.01)
            raise RuntimeError("Cancelled")
        if operation == "extract":
            if self.change:
                Path(data["path"]).write_text(self.change)
            write_json(data["cache"], {"test": True})
            return {"histogram": [1., 0.], "summary": [6., .5, .8]}
        if operation == "compare":
            return {"alignment_found": True, "alignment_error": .1,
                    "normalized_alignment_error": .01,
                    "alignment_transform": {"scale": 1 if not data["estimate_scale"] else 2}}
        return {"preview": True}


def finish(library, job):
    library.thread.join(10)
    assert not library.thread.is_alive()
    return library.job(job["id"])


@pytest.fixture
def catalog(tmp_path):
    root = tmp_path / "parts ü"
    root.mkdir()
    worker = FakeWorker()
    library = Library(tmp_path / "local data", supervisor=worker)
    library.select(root)
    yield library, root, worker
    library.close()


def test_discovery_duplicates_rescan_changes_and_deletions(catalog):
    library, root, worker = catalog
    (root / "nested").mkdir()
    a, b = root / "nested" / "A.STEP", root / "copy space.StP"
    a.write_text("same")
    b.write_text("same")
    (root / "ignored.txt").write_text("ignore")
    if hasattr(Path, "symlink_to"):
        try:
            (root / "cycle").symlink_to(root, target_is_directory=True)
        except OSError:
            pass
    job = finish(library, library.start("scan"))
    assert job["status"] == "completed"
    assert library.state()["counts"] == {"ready": 2}
    assert len(worker.calls) == 1
    finish(library, library.start("scan"))
    assert len(worker.calls) == 1
    # Shared feature caches continue to work after their original source vanishes.
    a.unlink()
    a.parent.rmdir()
    finish(library, library.start("scan"))
    assert library.browse()["total"] == 1
    assert len(worker.calls) == 1
    b.write_text("changed")
    finish(library, library.start("scan"))
    assert len(worker.calls) == 2
    monkey_version = lib.EXTRACTION_VERSION
    try:
        lib.EXTRACTION_VERSION = "next-extraction"
        finish(library, library.start("scan"))
        assert len(worker.calls) == 3
    finally:
        lib.EXTRACTION_VERSION = monkey_version


def test_inaccessible_subtree_retained(catalog, monkeypatch):
    library, root, _ = catalog
    sub = root / "denied"
    sub.mkdir()
    (sub / "old.step").write_text("old")
    finish(library, library.start("scan"))
    real = lib.os.scandir
    def denied(path):
        if canonical(path) == canonical(sub):
            raise PermissionError("denied")
        return real(path)
    monkeypatch.setattr(lib.os, "scandir", denied)
    job = finish(library, library.start("scan"))
    assert job["error_count"] == 1
    assert library.state()["counts"] == {"ready": 1}


def test_cancel_conflicting_job_and_restart(catalog):
    library, root, worker = catalog
    (root / "part.step").write_text("first")
    worker.block = True
    job = library.start("scan")
    deadline = time.monotonic() + 3
    while not worker.calls and time.monotonic() < deadline:
        time.sleep(.01)
    with pytest.raises(ValueError, match="already running"):
        library.start("scan")
    with pytest.raises(ValueError, match="already running"):
        library.select(root)
    library.cancel()
    result = finish(library, job)
    assert result["status"] == "cancelled"
    assert result["coverage"].startswith("incomplete")
    with library.connect() as db:
        assert not db.execute("SELECT 1 FROM files WHERE status='pending'").fetchall()
    result["status"] = "running"
    library._save_job(result)
    reopened = Library(library.directory, supervisor=worker)
    assert reopened.active == canonical(root)
    assert reopened.job(job["id"])["status"] == "interrupted"


def test_source_changes_reject_extraction(catalog):
    library, root, worker = catalog
    (root / "part.step").write_text("original")
    worker.change = "changed in worker"
    job = finish(library, library.start("scan"))
    assert job["error_count"] == 1
    assert library.state()["counts"] == {"error": 1}
    assert list(library.cache_dir.iterdir()) == []


def test_search_coverage_ranking_query_exclusion_external_query(catalog):
    library, root, worker = catalog
    a, b = root / "a.step", root / "b.step"
    a.write_text("identical")
    b.write_text("identical")
    finish(library, library.start("scan"))
    result = finish(library, library.start("search", query=str(a), depth="thorough", match_size=True))
    assert result["coverage"] == "complete"
    assert result["eligible"] == result["completed"] == 1
    assert result["results"][0]["path"] == canonical(b)
    assert result["results"][0]["alignment_transform"]["scale"] == 1
    assert not next(data for op, data in worker.calls if op == "compare")["estimate_scale"]
    assert library.preview(result["id"], 0) == {"preview": True}
    external = root.parent / "external.step"
    external.write_text("different")
    result = finish(library, library.start("search", query=str(external), depth="fast"))
    assert result["eligible"] == result["completed"] == 2
    assert [r["relative"] for r in result["results"]] == ["a.step", "b.step"]
    assert result["coverage"] == "incomplete: fast shortlist"
    b.write_text("stale")
    result = finish(library, library.start("search", query=str(a), depth="thorough"))
    assert result["error_count"] == 1
    assert "failed" in result["coverage"]


def test_switch_retains_catalog(catalog):
    library, root, _ = catalog
    (root / "part.step").write_text("one")
    finish(library, library.start("scan"))
    other = root.parent / "other"
    other.mkdir()
    library.select(other)
    assert library.browse()["total"] == 0
    assert library.state()["job"] is None
    library.select(root)
    assert library.browse()["total"] == 1


def test_10000_entry_index_pagination_and_shortlist(catalog):
    import tracemalloc
    library, root, _ = catalog
    descriptor = {"histogram": [0., 1.] + [0.] * 158, "summary": [6., .5, .8]}
    with library.connect() as db:
        db.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?)", (
            (canonical(root), str(root / f"part {i:05}.step"), f"part {i:05}.step", canonical(root), str(i),
             lib.EXTRACTION_VERSION, "cache.json", json.dumps(descriptor), "ready", None) for i in range(10000)))
    page = library.browse(offset=9990, limit=500)
    assert page["total"] == 10000 and len(page["items"]) == 10
    assert len(library.browse(limit=500)["items"]) == 100
    tracemalloc.start()
    rows = library._eligible(canonical(root), "query")
    # Force each half of the union to prefer different candidates.
    for i, row in enumerate(rows):
        row["descriptor"] = {"histogram": [i / 10000, 1.] + [0.] * 158, "summary": [6 + (10000-i), .5, .8]}
    selected = shortlist(rows, {"descriptor": descriptor})
    assert len(selected) == 200
    assert selected[0]["relative"] == "part 00000.step"
    assert len(shortlist(rows, {"descriptor": descriptor}, thorough=True)) == 10000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 128 * 1024 * 1024


def test_api_filesystem_session_and_jobs(catalog):
    from fastapi.testclient import TestClient
    from shapematch_app.server import create_app
    library, root, _ = catalog
    (root / "hello.STEP").write_text("cad")
    app = create_app(library, token="test-session", port=8123)
    with TestClient(app, base_url="http://127.0.0.1:8123") as client:
        assert client.get("/").status_code == 200
        assert client.get("/app.js").status_code == 200
        assert client.get("/api/state").status_code == 403
        client.headers["X-ShapeMatch-Session"] = "test-session"
        assert client.get("/api/state", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/state", headers={"Host": "evil.example:8123"}).status_code == 403
        listing = client.get("/api/filesystem", params={"path": str(root)}).json()
        selected = client.get("/api/filesystem", params={"path": str(root / "hello.STEP"), "allow_file": True}).json()
        assert selected["selected_file"] == str(root / "hello.STEP")
        assert listing["items"][0]["name"] == "hello.STEP"
        assert listing["breadcrumbs"][-1]["path"] == str(root)
        assert client.post("/api/jobs/search", json={"query": 42}).status_code == 400
        job = client.post("/api/jobs/scan").json()
        finish(library, job)
        assert client.get("/api/files", params={"text": "hello"}).json()["total"] == 1


def test_missing_cache_is_regenerated(catalog):
    library, root, worker = catalog
    (root / "part.step").write_text("one")
    finish(library, library.start("scan"))
    cache = next(library.cache_dir.iterdir())
    cache.unlink()
    finish(library, library.start("scan"))
    assert len(worker.calls) == 2
    rows = library._eligible(canonical(root), "external")
    assert Path(rows[0]["cache"]).is_file()


def test_changed_during_comparison_is_not_accepted(catalog):
    library, root, worker = catalog
    a, b = root / "a.step", root / "b.step"
    a.write_text("query")
    b.write_text("candidate")
    finish(library, library.start("scan"))
    original = worker.run
    def change(operation, data, cancel=None):
        result = original(operation, data, cancel)
        if operation == "compare":
            Path(data["path"]).write_text("changed mid comparison")
        return result
    worker.run = change
    result = finish(library, library.start("search", query=str(a), depth="thorough"))
    assert result["results"] == []
    assert result["error_count"] == 1


def test_best_twenty_results_and_search_cancellation(catalog):
    library, root, worker = catalog
    for i in range(26):
        (root / f"{i:02}.step").write_text("same")
    finish(library, library.start("scan"))
    query = str(root / "00.step")
    result = finish(library, library.start("search", query=query, depth="thorough"))
    assert result["completed"] == result["eligible"] == 25
    assert len(result["results"]) == 20
    assert [r["relative"] for r in result["results"]] == [f"{i:02}.step" for i in range(1, 21)]
    worker.block = True
    original_calls = len(worker.calls)
    job = library.start("search", query=query, depth="thorough")
    deadline = time.monotonic() + 3
    while len(worker.calls) == original_calls and time.monotonic() < deadline:
        time.sleep(.01)
    library.cancel()
    result = finish(library, job)
    assert result["status"] == "cancelled"
    assert result["completed"] < result["eligible"]
