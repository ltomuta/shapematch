"""Local SQLite catalog, incremental scans and bounded background search jobs."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import heapq
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
import uuid

from shapematch_cache import VERSION, source_hash
from .workers import Supervisor

# Algorithm/import changes must invalidate both successful and failed catalogs.
EXTRACTION_VERSION = f"native-features-{VERSION}-2"
EXTENSIONS = {".stp", ".step"}


def canonical(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def discover(root, cancel):
    """Yield direct-directory snapshots; never follow junctions or directory links.

    Deletion reconciliation uses only successfully enumerated direct parents.
    A denied descendant therefore never inherits its ancestor's success.
    """
    pending = [Path(root)]
    while pending and not cancel.is_set():
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                entries = list(entries)
            files, children = [], []
            for entry in entries:
                if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                    continue
                path = Path(entry.path)
                if path.is_dir() and getattr(path, "is_junction", lambda: False)():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    # Python 3.11 lacks is_junction(); Windows reparse-point
                    # directories cover junctions and other redirecting entries.
                    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                        continue
                    children.append(path)
                elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in EXTENSIONS:
                    files.append(canonical(path))
            yield canonical(directory), files, None
            pending.extend(sorted(children, reverse=True))
        except OSError as exc:
            yield canonical(directory), [], str(exc)


def shortlist(rows, query, thorough=False):
    """Bounded heaps: union of top 100 radial histograms and 100 summaries."""
    if thorough:
        return rows

    def histogram(row):
        a, b = row["descriptor"]["histogram"], query["descriptor"]["histogram"]
        return sum((x-y)**2 for x, y in zip(a, b)), row["relative"]

    def summary(row):
        a, b = row["descriptor"]["summary"], query["descriptor"]["summary"]
        return sum((math.log(max(x, 1e-15) / max(y, 1e-15)))**2 for x, y in zip(a, b)), row["relative"]

    selected = {r["path"]: r for r in heapq.nsmallest(100, rows, key=histogram)}
    selected.update({r["path"]: r for r in heapq.nsmallest(100, rows, key=summary)})
    return sorted(selected.values(), key=lambda r: r["relative"])


class Library:
    def __init__(self, data_dir=None, workers=2, timeout=300, supervisor=None):
        if data_dir is None:
            from platformdirs import user_data_path
            data_dir = user_data_path("ShapeMatch", appauthor=False)
        self.directory = Path(data_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.directory / "features"
        self.cache_dir.mkdir(exist_ok=True)
        self.database = self.directory / "library.sqlite3"
        self.supervisor = supervisor or Supervisor(workers, timeout)
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = None
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS files (
                    library TEXT, path TEXT, relative TEXT, parent TEXT, hash TEXT,
                    version TEXT, cache TEXT, descriptor TEXT, status TEXT, error TEXT,
                    PRIMARY KEY(library,path));
                CREATE INDEX IF NOT EXISTS file_status ON files(library,status,version);
                CREATE TABLE IF NOT EXISTS features (
                    hash TEXT, version TEXT, cache TEXT, descriptor TEXT,
                    PRIMARY KEY(hash,version));
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT);
            ''')
            for row in db.execute("SELECT id,data FROM jobs").fetchall():
                job = json.loads(row["data"])
                if job["status"] == "running":
                    job.update(status="interrupted", coverage="incomplete: interrupted on restart")
                    db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(job), row["id"]))
            db.execute("UPDATE files SET status='interrupted',error='Scan interrupted; rescan to retry' WHERE status='pending'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def active(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='active'").fetchone()
            return row[0] if row else None

    def select(self, path):
        path = canonical(path)
        if not Path(path).is_dir():
            raise ValueError("Select an existing library folder")
        with self.lock:
            self._idle()
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('active',?)", (path,))
        return self.state()

    def browse(self, text="", offset=0, limit=50):
        offset, limit = max(0, offset), min(max(1, limit), 100)
        # instr uses literal substrings, so SQL wildcard characters in paths work.
        with self.connect() as db:
            params = (self.active, text.lower())
            total = db.execute("SELECT count(*) FROM files WHERE library=? AND instr(lower(relative),?)>0", params).fetchone()[0]
            rows = db.execute("SELECT path,relative,status,error,descriptor FROM files WHERE library=? AND instr(lower(relative),?)>0 ORDER BY relative LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            descriptor = json.loads(item.pop("descriptor") or "{}")
            item["warnings"] = descriptor.get("warnings", [])
            items.append(item)
        return {"total": total, "offset": offset, "items": items}

    def rescan_if_outdated(self):
        """Refresh the selected catalog once on launch after extractor changes.

        Retry failures as well as successes; mixing old/new descriptors would
        make the fast-search ranking inconsistent. Unselected libraries refresh
        when explicitly rescanned, without modifying their existing records.
        """
        with self.lock:
            if self.thread and self.thread.is_alive():
                return None
            root = self.active
            if not root or not Path(root).is_dir():
                return None
            with self.connect() as db:
                stale = db.execute("SELECT 1 FROM files WHERE library=? AND (version IS NULL OR version!=?) LIMIT 1",
                                   (root, EXTRACTION_VERSION)).fetchone()
            if stale:
                return self.start("scan")
        return None

    def state(self):
        active = self.active
        with self.connect() as db:
            counts = {r[0]: r[1] for r in db.execute("SELECT status,count(*) FROM files WHERE library=? GROUP BY status", (active,))}
            latest = db.execute("SELECT data FROM jobs ORDER BY rowid DESC LIMIT 1").fetchone()
        job = json.loads(latest[0]) if latest else None
        if job and job["library"] != active:
            job = None
        return {"library": active, "counts": counts, "job": job,
                "workers": self.supervisor.workers, "max_workers": os.cpu_count() or 1,
                "active_workers": self.supervisor.progress() if hasattr(self.supervisor, "progress") else []}

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("Unknown job")
        return json.loads(row[0])

    def _save_job(self, job):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?)", (job["id"], json.dumps(job, allow_nan=False)))

    def _idle(self):
        if self.thread and self.thread.is_alive():
            raise ValueError("A scan or search is already running")

    def start(self, kind, **options):
        if kind not in ("scan", "search"):
            raise ValueError("Expected scan or search")
        with self.lock:
            self._idle()
            root = self.active
            if root is None:
                raise ValueError("Select a library first")
            if kind == "search":
                options["query"] = canonical(options.get("query", ""))
                if not Path(options["query"]).is_file() or Path(options["query"]).suffix.lower() not in EXTENSIONS:
                    raise ValueError("Select a STEP query file")
                if options.get("depth", "fast") not in ("fast", "thorough"):
                    raise ValueError("Search depth must be fast or thorough")
            job = {"id": uuid.uuid4().hex, "kind": kind, "status": "running", "library": root,
                   "completed": 0, "total": 0, "errors": [], "error_count": 0, "results": [],
                   "coverage": "incomplete: running", "options": options, "started": time.time()}
            self.cancel_event = threading.Event()
            self._save_job(job)
            self.thread = threading.Thread(target=self._execute, args=(job,), daemon=True)
            response = json.loads(json.dumps(job))
            self.thread.start()
            return response

    def cancel(self):
        self.cancel_event.set()

    def close(self):
        self.cancel()
        if self.thread:
            self.thread.join(timeout=5)

    def _error(self, job, path, exc):
        job["error_count"] += 1
        # Keep the progress payload bounded; per-file scan errors remain in SQLite.
        if len(job["errors"]) < 100:
            job["errors"].append({"path": path, "error": str(exc)})

    def _execute(self, job):
        try:
            if job["kind"] == "scan":
                self._scan(job)
            else:
                self._search(job)
            job["status"] = "cancelled" if self.cancel_event.is_set() else "completed"
        except Exception as exc:
            self._error(job, job["library"], exc)
            job["status"] = "cancelled" if self.cancel_event.is_set() else "failed"
        finally:
            incomplete = []
            if job["kind"] == "search" and job["options"].get("depth", "fast") == "fast":
                incomplete.append("fast shortlist")
            if job["status"] != "completed":
                incomplete.append(job["status"])
            if job["error_count"]:
                incomplete.append("failed parts or directories")
            job["coverage"] = "incomplete: " + ", ".join(incomplete) if incomplete else "complete"
            if job["kind"] == "scan":
                with self.connect() as db:
                    db.execute("UPDATE files SET status='interrupted',error='Scan interrupted; rescan to retry' WHERE library=? AND status='pending'", (job["library"],))
            job["finished"] = time.time()
            self._save_job(job)

    def _parallel(self, items, function):
        """Submit at most worker-count tasks, even for a 10,000-entry library."""
        iterator = iter(items)
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:
            pending = {}
            def submit():
                if not self.cancel_event.is_set():
                    item = next(iterator, None)
                    if item is not None:
                        pending[pool.submit(function, item)] = item
            exhausted = False
            def fill():
                nonlocal exhausted
                while not exhausted and not self.cancel_event.is_set() and len(pending) < self.supervisor.workers:
                    count = len(pending)
                    submit()
                    exhausted = len(pending) == count
            fill()
            while pending:
                done, _ = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
                for future in done:
                    item = pending.pop(future)
                    try:
                        yield item, future.result(), None
                    except Exception as exc:
                        yield item, None, exc
                fill()

    def _ensure(self, path, digest=None):
        digest = digest or source_hash(path)
        with self.connect() as db:
            row = db.execute("SELECT * FROM features WHERE hash=? AND version=?", (digest, EXTRACTION_VERSION)).fetchone()
        if row and Path(row["cache"]).is_file():
            if source_hash(path) != digest:
                raise ValueError("Source changed while checking cache")
            return {"path": path, "hash": digest, "cache": row["cache"], "descriptor": json.loads(row["descriptor"])}
        # Unique temporary outputs prevent simultaneous duplicate extractions from
        # overwriting a cache another worker is reading. Final cache is immutable.
        cache = str(self.cache_dir / f"{digest}-{uuid.uuid4().hex}.json")
        try:
            descriptor = self.supervisor.run("extract", {"path": path, "hash": digest, "cache": cache}, self.cancel_event)
            if source_hash(path) != digest:
                raise ValueError("Source changed during extraction")
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO features VALUES (?,?,?,?)", (digest, EXTRACTION_VERSION, cache, json.dumps(descriptor)))
            return {"path": path, "hash": digest, "cache": cache, "descriptor": descriptor}
        except Exception:
            Path(cache).unlink(missing_ok=True)
            raise

    def _scan(self, job):
        job["phase"] = "discovering"
        root = job["library"]
        discovered = []
        successful = set()
        for directory, files, error in discover(root, self.cancel_event):
            if error:
                self._error(job, directory, error)
            else:
                successful.add(directory)
                with self.connect() as db:
                    existing = db.execute("SELECT path FROM files WHERE library=? AND parent=?", (root, directory)).fetchall()
                    file_set = set(files)
                    for row in existing:
                        if row[0] not in file_set:
                            db.execute("DELETE FROM files WHERE library=? AND path=?", (root, row[0]))
                discovered.extend(files)
            job["total"] = len(discovered)
            self._save_job(job)
        if self.cancel_event.is_set():
            return
        # For a vanished subtree, deletion is safe only if its first missing
        # ancestor was under a directory that we successfully enumerated.
        with self.connect() as db:
            old = db.execute("SELECT path,parent FROM files WHERE library=?", (root,)).fetchall()
            discovered_set = set(discovered)
            for row in old:
                if row["path"] in discovered_set:
                    continue
                child = Path(row["parent"])
                while canonical(child.parent) not in successful and child.parent != child:
                    child = child.parent
                if canonical(child.parent) in successful:
                    try:
                        child.lstat()
                    except FileNotFoundError:
                        db.execute("DELETE FROM files WHERE library=? AND path=?", (root, row["path"]))
                    except OSError:
                        pass  # Permission failures never establish deletion.
        grouped = {}
        job["phase"] = "hashing"
        job["hashed"] = 0
        self._save_job(job)
        for path in discovered:
            if self.cancel_event.is_set():
                break
            relative = str(Path(path).relative_to(root))
            try:
                digest = source_hash(path)
                grouped.setdefault(digest, []).append(path)
                status, error = "pending", None
            except OSError as exc:
                digest, status, error = None, "error", str(exc)
                self._error(job, path, exc)
                job["completed"] += 1
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,NULL,NULL,?,?)",
                           (root, path, relative, canonical(Path(path).parent), digest, EXTRACTION_VERSION, status, error))
            job["hashed"] += 1
            if job["hashed"] % 20 == 0:
                self._save_job(job)
        job["phase"] = "extracting"
        self._save_job(job)
        for (digest, paths), feature, error in self._parallel(grouped.items(), lambda item: self._ensure(item[1][0], item[0])):
            for path in paths:
                file_error = error
                if file_error is None:
                    try:
                        if source_hash(path) != digest:
                            raise ValueError("Source changed during scan")
                    except (ValueError, OSError) as exc:
                        file_error = exc
                if file_error:
                    self._error(job, path, file_error)
                with self.connect() as db:
                    db.execute("UPDATE files SET status=?,error=?,cache=?,descriptor=? WHERE library=? AND path=?",
                               ("error" if file_error else "ready", str(file_error) if file_error else None,
                                feature["cache"] if feature else None, json.dumps(feature["descriptor"]) if feature else None, root, path))
                job["completed"] += 1
            self._save_job(job)

    def _eligible(self, root, query):
        result = []
        with self.connect() as db:
            rows = db.execute("SELECT * FROM files WHERE library=? AND status='ready' AND version=? AND path!=? ORDER BY relative", (root, EXTRACTION_VERSION, query))
            for row in rows:
                item = dict(row)
                item["descriptor"] = json.loads(item["descriptor"])
                result.append(item)
        return result

    def _search(self, job):
        options = job["options"]
        query = self._ensure(options["query"])
        rows = self._eligible(job["library"], query["path"])
        candidates = shortlist(rows, query, options.get("depth") == "thorough")
        job.update(eligible=len(rows), total=len(candidates), query=query, phase="comparing")
        self._save_job(job)
        def compare(row):
            if source_hash(row["path"]) != row["hash"]:
                raise ValueError("Indexed source changed; rescan library")
            result = self.supervisor.run("compare", {"query": query["path"], "query_cache": query["cache"],
                "path": row["path"], "cache": row["cache"], "estimate_scale": not options.get("match_size", False)}, self.cancel_event)
            if source_hash(row["path"]) != row["hash"] or source_hash(query["path"]) != query["hash"]:
                raise ValueError("Source changed during comparison")
            return {"path": row["path"], "relative": row["relative"], "hash": row["hash"], "cache": row["cache"], **result}
        for row, result, error in self._parallel(candidates, compare):
            job["completed"] += 1
            if error:
                self._error(job, row["path"], error)
            elif result["alignment_found"]:
                job["results"].append(result)
                job["results"].sort(key=lambda r: (r["normalized_alignment_error"], r["alignment_error"], r["relative"]))
                del job["results"][20:]
            else:
                self._error(job, row["path"], "No alignment found")
            self._save_job(job)

    def preview(self, job_id, index, path=None):
        job = self.job(job_id)
        if path is not None:
            index = next((i for i, r in enumerate(job["results"]) if r["path"] == path), -1)
        if job["kind"] != "search" or index < 0 or index >= len(job["results"]):
            raise ValueError("Select a search result")
        query, row = job["query"], job["results"][index]
        for item in (query, row):
            if source_hash(item["path"]) != item["hash"]:
                raise ValueError("Source changed; rescan and search again")
        result = self.supervisor.run("preview", {"query": query["path"], "query_cache": query["cache"],
            "path": row["path"], "cache": row["cache"], "comparison": row})
        for item in (query, row):
            if source_hash(item["path"]) != item["hash"]:
                raise ValueError("Source changed during preview")
        return result
