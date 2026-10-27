"""Opt-in browser workflow against a real loopback server and deterministic CAD stub."""
import os
import re
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
                payload = {"source": {"name": "query", "original": [[3,0,0],[4,0,0],[3,1,0]],
                                   "aligned": [[0,0,0],[1,0,0],[0,1,0]], "triangles": [[0,1,2]]},
                        "target": {"name": "result", "vertices": [[0,0,0],[1,0,0],[0,1,0]], "triangles": [[0,1,2]]}}
                if data.get("include_booleans"):
                    region = {"vertices": [[0,0,0],[1,0,0],[0,1,0]], "triangles": [[0,1,2]], "volume": .5}
                    placements = {pose: {"regions": {key: region for key in ("common", "only_a", "only_b")}, "error": None}
                                  for pose in ("aligned", "original")}
                    payload.update(boolean=placements, boolean_tolerance=0.,
                                   boolean_variants=[{"tolerance": value, "placements": placements} for value in (.001, .01, .1)])
                return payload
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
                expect(page.locator("#comparison")).to_be_visible()
                page.locator("#display-mode").select_option("boolean")
                expect(page.locator("#boolean-controls")).to_be_visible()
                expect(page.locator("#overlay-controls")).not_to_be_visible()
                expect(page.locator("#boolean-status")).to_contain_text("Select a search result")
                page.locator("#display-mode").select_option("overlay")
                expect(page.locator("#overlay-controls")).to_be_visible()
                expect(page.locator("#boolean-controls")).not_to_be_visible()
                page.locator("#settings-tab").click()
                page.locator("#folder").click()
                page.locator("#path").fill(str(root))
                page.locator("#path-form button").click()
                expect(page.locator("#breadcrumbs")).to_contain_text(root.name)
                page.locator("#pick-folder").click()
                page.locator("#rescan").click()
                expect(page.locator("#search-progress")).to_contain_text("scan: completed")
                expect(page.locator("#counts")).to_contain_text("2 ready")
                page.locator("#search-tab").click()
                page.locator("#autocomplete").fill("a.STEP")
                page.locator("#suggestions button").first.click()
                expect(page.locator("#query")).to_contain_text(re.compile("a.step", re.I))
                expect(page.locator("#autocomplete")).to_have_value(re.compile("a.step", re.I))
                expect(page.locator("#suggestions button")).to_have_count(0)
                page.locator("#autocomplete").fill("b.stp")
                expect(page.locator("#query")).to_contain_text("None")
                page.locator("#autocomplete").fill("a.STEP")
                page.locator("#suggestions button").first.click()
                page.locator("#depth").select_option("thorough")
                page.locator("#match-size").check()
                page.locator("#search").click()
                expect(page.locator("#search-progress")).to_contain_text("search: completed")
                expect(page.locator("#search-progress")).to_contain_text("1 eligible")
                page.locator("#results button").first.click()
                page.wait_for_function("document.getElementById('viewer').data?.length === 2")
                box = page.locator("#comparison").bounding_box()
                assert box["width"] > page.locator("aside").bounding_box()["width"]
                page.screenshot(path=".test-tmp/search-desktop.png", full_page=True)
                page.locator("#source-opacity").evaluate("e=>{e.value=20;e.dispatchEvent(new Event('input'))}")
                page.wait_for_function("document.getElementById('viewer').data[0].opacity === .2")
                page.locator("#source-visible").uncheck()
                page.wait_for_function("document.getElementById('viewer').data[0].visible === false")
                page.locator("#target-visible").uncheck()
                page.wait_for_function("document.getElementById('viewer').data[1].visible === false")
                page.locator("#placement").select_option("original")
                page.wait_for_function("document.getElementById('viewer').data[0].x[0] === 3")
                page.locator("#display-mode").select_option("boolean")
                page.wait_for_function("document.getElementById('viewer').data?.length === 3")
                expect(page.locator("#common-volume")).to_contain_text("0.50000 mm³")
                page.locator("#common-visible").uncheck()
                page.wait_for_function("document.getElementById('viewer').data[0].visible === false")
                page.locator("#boolean-tolerance").select_option("0.01")
                expect(page.locator("#boolean-status")).to_contain_text("Only query: blue")
                page.locator("#placement").select_option("aligned")
                page.locator("#common-visible").check()
                for width, height in [(1280, 720), (1366, 768), (1920, 1080)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(200)
                    for selector in ("#comparison", "#viewer", "#search", "#search-cancel", ".results-panel"):
                        bounds = page.locator(selector).bounding_box()
                        assert bounds["y"] >= 0
                        assert bounds["y"] + bounds["height"] <= height
                    assert page.locator("#viewer").bounding_box()["height"] > 180
                    assert page.evaluate("document.documentElement.scrollHeight <= innerHeight")
                page.locator("#results").evaluate("""element => {
                    for (let i = 0; i < 40; i++) element.append(element.firstElementChild.cloneNode(true));
                }""")
                assert page.locator(".results-panel .table-wrap").evaluate("e => e.scrollHeight > e.clientHeight")
                page.set_viewport_size({"width":1366,"height":768})
                page.wait_for_timeout(200)
                page.screenshot(path=".test-tmp/search-boolean.png", full_page=True)
                page.locator("#display-mode").select_option("overlay")
                page.wait_for_function("document.getElementById('viewer').data?.length === 2")
                page.locator("#external").click()
                page.locator("#path").fill(str(tmp_path))
                page.locator("#path-form button").click()
                page.locator("#entries button", has_text="external.step").click()
                expect(page.locator("#query")).to_contain_text("external.step")
                page.locator("#depth").select_option("fast")
                page.locator("#search").click()
                expect(page.locator("#search-progress")).to_contain_text("2 eligible")
                expect(page.locator("#search-progress")).to_contain_text("incomplete: fast shortlist")
                page.locator("#external").click()
                page.locator("#path").fill(str(tmp_path / "external.step"))
                page.locator("#path-form button").click()
                expect(page.locator("#picker")).not_to_be_visible()
                expect(page.locator("#query")).to_contain_text("external.step")
                page.locator("#upload-file").set_input_files(str(tmp_path / "external.step"))
                expect(page.locator("#query")).to_have_text("external.step")
                expect(page.locator("#autocomplete")).to_have_value("external.step")
                page.locator("#search").click()
                expect(page.locator("#search-progress")).to_contain_text("2 eligible")
                page.locator("#drop-zone").evaluate("""element => {
                    const transfer = new DataTransfer();
                    transfer.items.add(new File(['dropped cad'], 'dropped.STP'));
                    element.dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true}));
                }""")
                expect(page.locator("#query")).to_have_text("dropped.STP")
                page.set_viewport_size({"width":390,"height":844})
                assert page.locator("main").bounding_box()["width"] <= 390
                page.screenshot(path=".test-tmp/search-mobile.png", full_page=True)
                assert errors == []
                assert external == []
                browser.close()
        finally:
            server.should_exit = True
            thread.join(10)
            library.close()
