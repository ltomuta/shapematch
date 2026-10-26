"""Smoke-test the installed wheel's app command and assets, without touching user data."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "server.log"
        with output.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen([sys.executable, "-m", "shapematch", "app", "--no-browser",
                                        "--data-dir", str(Path(directory) / "data")],
                                       cwd=directory, stdout=stream, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 20
                url = None
                while time.monotonic() < deadline:
                    text = output.read_text(encoding="utf-8")
                    for line in text.splitlines():
                        if line.startswith("ShapeMatch local library: "):
                            url = line.split(": ", 1)[1]
                    if url or process.poll() is not None:
                        break
                    time.sleep(.05)
                assert url, output.read_text(encoding="utf-8")
                base, token = url.split("#session=")
                # Bypass environment HTTP proxies only for the explicit loopback
                # target, as required for a local application smoke check.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for path, expected in [("", b"Local STEP library"), ("app.js", b"showPreview"), ("style.css", b"box-sizing"), ("plotly.js", b"plotly")]:
                    for attempt in range(100):
                        try:
                            with opener.open(base+path, timeout=2) as response:
                                assert expected in response.read()
                            break
                        except OSError:
                            if attempt == 99:
                                raise
                            time.sleep(.05)
                request = urllib.request.Request(base+"api/state", headers={"X-ShapeMatch-Session": token})
                with opener.open(request, timeout=2) as response:
                    assert json.load(response)["library"] is None
                print("Installed app command, session API and bundled assets: OK")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
