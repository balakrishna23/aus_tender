"""Regression: long research-source URLs must fit the mobile newsletter."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("newsletter_renderer", ROOT / "routine/scripts/render_newsletter_preview.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class MobileCoverageLayout(unittest.TestCase):
    def test_long_source_url_does_not_expand_mobile_viewport(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is unavailable")
        browsers = Path("/opt/aus_tender/.browsers")
        env = {"PLAYWRIGHT_BROWSERS_PATH": str(browsers)} if browsers.is_dir() else {}
        with patch.dict(os.environ, env), sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                self.skipTest("Chromium is unavailable")
            with tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary)
                package = {
                    "subject": "Layout regression", "greeting": "Hello", "introduction": "Preview",
                    "footer": "Internal", "preheader": "Preview", "edition_label": "14 November 2026",
                    "from": "sender@example.invalid", "to": "recipient@example.invalid",
                    "sections": [{"title": "Coverage and access", "items": [{
                        "heading": "Checked today", "paragraphs": [
                            "Supplemental research failures: https://example.invalid/" + "x" * 500
                        ], "links": []}]}],
                }
                with contextlib.redirect_stdout(io.StringIO()):
                    renderer.render(package, out)
                browser = playwright.chromium.launch(headless=True)
                try:
                    for width in (390, 900):
                        page = browser.new_page(viewport={"width": width, "height": 1000})
                        page.goto((out / "newsletter_preview.html").as_uri())
                        content = page.evaluate("document.documentElement.scrollWidth")
                        self.assertLessEqual(content, width)
                        page.close()
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main()
