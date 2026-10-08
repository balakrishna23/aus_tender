"""Browser check of semantic controls, evidence and exact constraints."""
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1100,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8765')
    assert page.locator('#hybrid').is_checked()
    page.locator('#explain').uncheck()
    page.locator('#question').fill('Show contracts ending within 90 days above $100k, excluding equipment and assessments')
    page.locator('#search-topic').fill('cloud migration')
    with page.expect_response('**/api/ask',timeout=60000) as response:
        page.locator('#ask').click()
    result=response.value.json()
    assert result['retrieval_status'][0]['mode']=='hybrid',result['retrieval_status']
    contracts=[r for r in result['facts'] if r['view']=='renewals']
    assert contracts and all(r['current_value']>100000 and 0<=r['days_until_end']<=90 for r in contracts)
    assert contracts[0]['description']=='Azure Migration Project Services'
    page.locator('#status').filter(has_text='Finished.').wait_for()
    assert 'hybrid' in page.locator('#results').inner_text()
    assert not errors,errors
    runtime=Path(__file__).parent/'runtime';runtime.mkdir(exist_ok=True)
    page.screenshot(path=str(runtime/'hybrid-ui.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    browser.close()
print('Hybrid UI passed: semantic result, exact constraints, desktop/mobile, no JavaScript errors.')
