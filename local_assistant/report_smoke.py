"""Check the standalone handover report layout without uploading it."""
from pathlib import Path
from playwright.sync_api import sync_playwright

root=Path(__file__).parent
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1280,'height':1000})
    page.goto((root/'reports'/'refinement-report.html').resolve().as_uri())
    assert 'Weights were not trained.' in page.inner_text('body')
    assert '54 passed' in page.inner_text('body')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(root/'runtime'/'refinement-report-preview.png'))
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    browser.close()
print('Standalone report checked at desktop and mobile widths.')
