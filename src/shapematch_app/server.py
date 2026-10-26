"""Loopback-only HTTP application; each launch requires a fresh session token."""
from contextlib import asynccontextmanager
from importlib.resources import files
import os
from pathlib import Path
import secrets
import socket
import string
import threading
import webbrowser

from .library import Library, EXTENSIONS


def filesystem(path=None, offset=0, limit=200, allow_file=False):
    directory = Path(path).expanduser().resolve() if path else Path.home()
    selected_file = None
    if allow_file and directory.is_file() and directory.suffix.lower() in EXTENSIONS:
        selected_file, directory = str(directory), directory.parent
    if not directory.is_dir():
        raise ValueError("Enter an existing directory")
    entries, errors = [], []
    with os.scandir(directory) as stream:
        for entry in stream:
            try:
                if entry.is_dir() or (entry.is_file() and Path(entry.name).suffix.lower() in EXTENSIONS):
                    entries.append({"name": entry.name, "path": str(Path(entry.path).resolve()), "directory": entry.is_dir()})
            except OSError as exc:
                errors.append(str(exc))
    entries.sort(key=lambda e: (not e["directory"], e["name"].lower(), e["name"]))
    breadcrumbs = list(reversed(directory.parents)) + [directory]
    drives = [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").is_dir()] if os.name == "nt" else ["/"]
    return {"path": str(directory), "parent": str(directory.parent), "drives": drives,
            "breadcrumbs": [{"name": p.name or str(p), "path": str(p)} for p in breadcrumbs],
            "total": len(entries), "items": entries[offset:offset+limit], "errors": errors,
            "selected_file": selected_file}


def create_app(library=None, token=None, port=8000):
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import FileResponse, JSONResponse, Response
    except ImportError as exc:
        raise ValueError("Install the application extra: python -m pip install 'shapematch[app]'") from exc
    library = library or Library()
    token = token or secrets.token_urlsafe(32)
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    assets = files("shapematch_app").joinpath("static")

    @asynccontextmanager
    async def lifespan(app):
        yield
        library.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.library, app.state.token = library, token

    @app.middleware("http")
    async def session_access(request: Request, call_next):
        if request.headers.get("host") not in hosts:
            return JSONResponse({"detail": "Invalid local host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            return JSONResponse({"detail": "Invalid browser origin"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-shapematch-session", "")
            if not secrets.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse({"detail": "Open the session URL printed by shapematch app"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(OSError)
    async def filesystem_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    from .workers import WorkerError
    @app.exception_handler(WorkerError)
    async def worker_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(str(assets.joinpath("index.html")))

    @app.get("/app.js")
    def script():
        return FileResponse(str(assets.joinpath("app.js")), media_type="text/javascript")

    @app.get("/style.css")
    def style():
        return FileResponse(str(assets.joinpath("style.css")), media_type="text/css")

    @app.get("/plotly.js")
    def plotly():
        from plotly.offline import get_plotlyjs
        return Response(get_plotlyjs(), media_type="text/javascript")

    @app.get("/api/state")
    def state():
        return library.state()

    @app.post("/api/workers")
    def configure_workers(data: dict):
        library.supervisor.configure(data.get("workers"))
        return library.state()

    @app.get("/api/filesystem")
    def navigate(path: str = "", offset: int = 0, limit: int = 200, allow_file: bool = False):
        return filesystem(path, max(0, offset), min(200, max(1, limit)), allow_file)

    @app.get("/api/files")
    def browse(text: str = "", offset: int = 0, limit: int = 50):
        return library.browse(text, offset, limit)

    @app.post("/api/library")
    def select(data: dict):
        if not isinstance(data.get("path"), str):
            raise ValueError("Library path is required")
        return library.select(data["path"])

    @app.post("/api/jobs/scan")
    def scan():
        return library.start("scan")

    @app.post("/api/jobs/search")
    def search(data: dict):
        if not isinstance(data.get("query"), str) or not isinstance(data.get("match_size", False), bool):
            raise ValueError("Select a query and valid size option")
        return library.start("search", query=data["query"], depth=data.get("depth", "fast"), match_size=data.get("match_size", False))

    @app.post("/api/cancel")
    def cancel():
        library.cancel()
        return {"cancelled": True}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return library.job(job_id)

    @app.get("/api/jobs/{job_id}/preview/{index}")
    def preview(job_id: str, index: int, path: str | None = None):
        return library.preview(job_id, index, path)

    return app


def launch(port=0, workers=2, timeout=300, open_browser=True, data_dir=None):
    try:
        import uvicorn
        import platformdirs
        import plotly  # Verify the complete extra before opening a browser.
        if port < 0 or port > 65535:
            raise ValueError("Port must be between 0 and 65535")
        library = Library(data_dir, workers=workers, timeout=timeout)
        # Reserve the socket until Uvicorn owns it: no free-port race.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
            actual_port = listener.getsockname()[1]
            token = secrets.token_urlsafe(32)
            app = create_app(library, token, actual_port)
            url = f"http://127.0.0.1:{actual_port}/#session={token}"
            print(f"ShapeMatch local library: {url}", flush=True)
            config = uvicorn.Config(app, log_level="warning")
            server = uvicorn.Server(config)
            if open_browser:
                def browse_when_ready():
                    import time
                    while not server.started and not server.should_exit:
                        time.sleep(.05)
                    if server.started:
                        webbrowser.open(url)
                threading.Thread(target=browse_when_ready, daemon=True).start()
            try:
                server.run(sockets=[listener])
            finally:
                library.close()
        return 0
    except ImportError:
        print("Install the application extra: python -m pip install 'shapematch[app]'")
        return 1
    except (ValueError, OSError) as exc:
        print(f"shapematch app: {exc}")
        return 1
