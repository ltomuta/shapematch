"""Opt-in browser regression: install Playwright/Chromium and set SHAPEMATCH_BROWSER_TESTS=1."""
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SHAPEMATCH_BROWSER_TESTS") != "1",
                                reason="Set SHAPEMATCH_BROWSER_TESTS=1 to run the offline browser check")


def test_offline_viewer_controls(overlay, tmp_path):
    from playwright.sync_api import sync_playwright
    from shapematch_html import write_html
    source, target, comparison = overlay
    source.name = '</script><script>window.CAD_FILENAME_EXECUTED=true</script>.stp'
    # Make switching placement observable in the rendered coordinates, rather
    # than allowing a label-only change to pass on coincident cube geometries.
    import numpy as np
    pose = comparison["alignment_transform"]
    pose["translation"] = (np.array(pose["translation"])
                           + target.scale * target.rotationMatrix @ np.array([2, 0, 0])).tolist()
    report = write_html(source, target, comparison, tmp_path / "viewer.html")
    errors, external = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--use-angle=swiftshader",
                                                                 "--enable-unsafe-swiftshader"])
        context = browser.new_context(offline=True, viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: external.append(request.url)
                if request.url.startswith(("http:", "https:")) else None)
        page.goto(report.as_uri())
        page.wait_for_function("document.body.dataset.ready === 'true'")
        assert page.locator("canvas").count() > 0
        assert page.locator("#source-name").inner_text() == 'A · '+source.name
        assert page.evaluate("window.CAD_FILENAME_EXECUTED") is None
        assert page.locator("#rms").inner_text() == "< 0.000001 mm"
        aligned_x = page.evaluate("document.getElementById('viewer').data[0].x[0]")
        page.locator("#source-opacity").evaluate("element => {element.value='20'; element.dispatchEvent(new Event('input', {bubbles:true}));}")
        page.wait_for_function("document.getElementById('viewer').data[0].opacity === .2")
        page.locator("#source-visible").uncheck()
        page.wait_for_function("document.getElementById('viewer').data[0].visible === false")
        page.locator("#source-visible").check()
        page.locator("#edges").check()
        page.wait_for_function("document.getElementById('viewer').data[2].visible === true")
        page.locator("#placement").select_option("original")
        assert page.locator("#rms").inner_text() == "—"
        page.wait_for_function("expected => Math.abs(document.getElementById('viewer').data[0].x[0]-expected)<1e-10",
                               arg=aligned_x-2)
        page.locator("#placement").select_option("aligned")
        assert page.locator("#rms").inner_text() == "< 0.000001 mm"
        page.wait_for_function("expected => Math.abs(document.getElementById('viewer').data[0].x[0]-expected)<1e-10",
                               arg=aligned_x)
        # Aligned cubes are disjoint in this fixture; the originals coincide.
        page.locator("#display-mode").select_option("boolean")
        page.wait_for_function("document.getElementById('viewer').data[5].visible === true")
        assert page.locator("#common-visible").is_disabled()
        assert 'Empty' in page.locator("#common-volume").inner_text()
        assert page.locator("#boolean-controls").is_visible()
        assert page.locator("#overlay-controls").is_hidden()
        page.locator("#palette").select_option("contrast")
        page.wait_for_function("document.getElementById('viewer').data[5].color === '#c33799'")
        page.locator("#only_a-visible").uncheck()
        page.wait_for_function("document.getElementById('viewer').data[5].visible === false")
        page.locator("#placement").select_option("original")
        page.wait_for_function("document.getElementById('viewer').data[4].visible === true")
        assert page.locator("#only_a-visible").is_disabled()
        assert page.locator("#only_b-visible").is_disabled()
        assert '1 mm³' in page.locator("#common-volume").inner_text()
        page.locator("#differences-only").click()
        page.wait_for_function("document.getElementById('viewer').data[4].visible === false")
        assert page.locator("#empty-state").is_visible()
        page.locator("#all-regions").click()
        page.wait_for_function("document.getElementById('viewer').data[4].visible === true")
        assert page.locator("#empty-state").is_hidden()
        page.locator("#common-opacity").evaluate("e => {e.value='70';e.dispatchEvent(new Event('input',{bubbles:true}));}")
        page.wait_for_function("document.getElementById('viewer').data[4].opacity === .7")
        page.locator("#top").click()
        page.wait_for_function("document.getElementById('viewer').layout.scene.camera.eye.z === 2.2")
        assert page.locator("#error").is_hidden()
        assert errors == []
        assert external == []
        # The responsive layout must keep the canvas usable on narrow screens.
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(200)
        assert page.locator("#viewer").bounding_box()["width"] <= 390
        browser.close()


def test_failed_booleans_are_unavailable_and_overlay_still_works(overlay, tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright
    from shapematch_geometry import GeometryError
    from shapematch_html import write_html

    def fail(*args, **kwargs):
        raise GeometryError("Native Boolean failure")

    monkeypatch.setattr("shapematch_boolean.solid_partitions", fail)
    report = write_html(*overlay, tmp_path / "failure.html")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--use-angle=swiftshader",
                                                                 "--enable-unsafe-swiftshader"])
        page = browser.new_page(offline=True)
        page.goto(report.as_uri())
        page.wait_for_function("document.body.dataset.ready === 'true'")
        assert page.evaluate("document.getElementById('viewer').data[0].visible") is True
        page.locator("#display-mode").select_option("boolean")
        assert page.locator("#common-volume").inner_text() == "Unavailable"
        assert page.locator("#common-visible").is_disabled()
        assert "Native Boolean failure" in page.locator("#boolean-status").inner_text()
        assert "unavailable" in page.locator("#empty-state").inner_text()
        page.locator("#display-mode").select_option("overlay")
        page.wait_for_function("document.getElementById('viewer').data[0].visible === true")
        assert page.locator("#empty-state").is_hidden()
        assert page.locator("#error").is_hidden()
        browser.close()


def test_offline_tolerance_changes_the_boolean_geometry(overlay, tmp_path):
    import numpy as np
    from playwright.sync_api import sync_playwright
    from shapematch_html import write_html
    source, target, comparison = overlay
    pose = comparison["alignment_transform"]
    pose["translation"] = (np.asarray(pose["translation"])
                           + target.scale * target.rotationMatrix @ np.array([.0005, 0, 0])).tolist()
    report = write_html(source, target, comparison, tmp_path / "tolerance.html")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--use-angle=swiftshader",
                                                                 "--enable-unsafe-swiftshader"])
        page = browser.new_page(offline=True)
        page.goto(report.as_uri())
        page.wait_for_function("document.body.dataset.ready === 'true'")
        page.locator("#display-mode").select_option("boolean")
        page.wait_for_function("document.getElementById('viewer').data[5].visible === true")
        rms = page.locator("#rms").inner_text()
        page.locator("#boolean-tolerance").select_option("0.001")
        page.wait_for_function("document.getElementById('viewer').data[5].visible === false")
        assert page.locator("#only_a-visible").is_disabled()
        assert page.locator("#only_b-visible").is_disabled()
        assert page.locator("#rms").inner_text() == rms
        page.locator("#boolean-tolerance").select_option("0")
        page.wait_for_function("document.getElementById('viewer').data[5].visible === true")
        assert not page.locator("#only_a-visible").is_disabled()
        assert page.locator("#error").is_hidden()
        browser.close()
