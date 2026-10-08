"""Optional UI check using the project's existing Playwright installation."""
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width': 1100, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765')
    page.locator('[data-q="What data do we have?"]').click()
    page.locator('#explain').uncheck()
    page.locator('#hybrid').uncheck()
    page.locator('#ask').click()
    page.locator('#status').filter(has_text='Finished.').wait_for(timeout=15000)
    assert page.locator('#results details').count() >= 1
    assert 'Query date:' in page.locator('#results').inner_text()
    assert not errors, errors
    page.screenshot(path=str(Path(__file__).parent/'runtime'/'pilot-ui.png'), full_page=True)
    page.locator('#question').fill('Which cyber contracts end within 90 days? Choose 3 for ICT labour hire, excluding equipment and one-off assessments.')
    page.locator('#ask').click()
    page.locator('#status').filter(has_text='Finished.').wait_for(timeout=15000)
    assert 'cyber' in page.locator('#results').inner_text()
    assert page.locator('#results a').filter(has_text='Open contract notice').count()>0
    page.locator('#results .answer a').first.click()
    assert '#evidence-E1' in page.url
    page.locator('#question').fill('Check CN4281051')
    page.locator('#verify').check()
    page.locator('#ask').click()
    page.locator('#status').filter(has_text='Finished.').wait_for(timeout=45000)
    text=page.locator('#results').inner_text()
    assert 'Official API check for CN4281051: matched' in text
    assert '[object Object]' not in text
    assert not errors,errors
    page.screenshot(path=str(Path(__file__).parent/'runtime'/'verified-source-ui.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    browser.close()
    print('Desktop and mobile UI smoke checks passed; no JavaScript errors.')
