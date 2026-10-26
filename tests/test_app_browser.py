"""Opt-in browser workflow against a real loopback server and deterministic CAD stub."""
import os
import socket
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SHAPEMATCH_BROWSER_TESTS") != "1",
                                reason="Set SHAPEMATCH_BROWSER_TESTS=1 for browser checks")


def test_local_library_browser_workflow(tmp_path):
    import uvicorn
    from playwright.sync_api import sync_playwright, expect
    from shapematch_app.library import Library
    from shapematch_app.server import create_app
    from test_app import FakeWorker

    class BrowserWorker(FakeWorker):
        def run(self, operation, data, cancel=None):
            if operation == "preview":
                return {"source": {"name": "query", "original": [[3,0,0],[4,0,0],[3,1,0]],
                                   "aligned": [[0,0,0],[1,0,0],[0,1,0]], "triangles": [[0,1,2]]},
                        "target": {"name": "result", "vertices": [[0,0,0],[1,0,0],[0,1,0]], "triangles": [[0,1,2]]}}
            return super().run(operation, data, cancel)

    root = tmp_path / "Library ü with spaces"
    root.mkdir()
    (root / "a.STEP").write_text("cad a")
    (root / "nested").mkdir()
    (root / "nested" / "b.stp").write_text("cad b")
    (tmp_path / "external.step").write_text("external")
    library = Library(tmp_path / "data", supervisor=BrowserWorker())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(library, "browser-session", port), log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        try:
            with sync_playwright() as playwright:
                executable = os.environ.get("SHAPEMATCH_CHROMIUM")
                browser = playwright.chromium.launch(executable_path=executable, headless=True,
                    args=["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
                page = browser.new_page(viewport={"width":1280,"height":1000})
                errors, external = [], []
                page.on("pageerror", lambda e: errors.append(str(e)))
                def request_route(route):
                    if route.request.url.startswith(f"http://127.0.0.1:{port}/"):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()
                page.route("**/*", request_route)
                page.goto(f"http://127.0.0.1:{port}/#session=browser-session")
                page.wait_for_function("document.body.dataset.ready === 'true'")
                assert page.evaluate("location.hash") == ""
                page.locator("#folder").click()
                page.locator("#path").fill(str(root))
                page.locator("#path-form button").click()
                expect(page.locator("#breadcrumbs")).to_contain_text(root.name)
                page.locator("#pick-folder").click()
                page.locator("#rescan").click()
                expect(page.locator("#progress")).to_contain_text("scan: completed")
                expect(page.locator("#counts")).to_contain_text("2 ready")
                page.locator("#autocomplete").fill("a.STEP")
                page.locator("#suggestions button").first.click()
                expect(page.locator("#query")).to_contain_text("a.STEP")
                page.locator("#depth").select_option("thorough")
                page.locator("#match-size").check()
                page.locator("#search").click()
                expect(page.locator("#progress")).to_contain_text("search: completed")
                expect(page.locator("#progress")).to_contain_text("1 eligible")
                page.locator("#results button").first.click()
                page.wait_for_function("document.getElementById('viewer').data?.length === 2")
                page.locator("#source-opacity").evaluate("e=>{e.value=20;e.dispatchEvent(new Event('input'))}")
                page.wait_for_function("document.getElementById('viewer').data[0].opacity === .2")
                page.locator("#source-visible").uncheck()
                page.wait_for_function("document.getElementById('viewer').data[0].visible === false")
                page.locator("#target-visible").uncheck()
                page.wait_for_function("document.getElementById('viewer').data[1].visible === false")
                page.locator("#placement").select_option("original")
                page.wait_for_function("document.getElementById('viewer').data[0].x[0] === 3")
                page.locator("#external").click()
                page.locator("#path").fill(str(tmp_path))
                page.locator("#path-form button").click()
                page.locator("#entries button", has_text="external.step").click()
                expect(page.locator("#query")).to_contain_text("external.step")
                page.locator("#depth").select_option("fast")
                page.locator("#search").click()
                expect(page.locator("#progress")).to_contain_text("2 eligible")
                expect(page.locator("#progress")).to_contain_text("incomplete: fast shortlist")
                page.locator("#external").click()
                page.locator("#path").fill(str(tmp_path / "external.step"))
                page.locator("#path-form button").click()
                expect(page.locator("#picker")).not_to_be_visible()
                expect(page.locator("#query")).to_contain_text("external.step")
                page.set_viewport_size({"width":390,"height":844})
                assert page.locator("main").bounding_box()["width"] <= 390
                assert errors == []
                assert external == []
                browser.close()
        finally:
            server.should_exit = True
            thread.join(10)
            library.close()
