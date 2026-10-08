from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1100,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8765')
    page.locator('#explain').uncheck();page.locator('#hybrid').uncheck()
    assert page.locator('#sheets').is_checked()
    page.locator('#question').fill('Show Google Sheets row 12 from tab "Tracker" in workbook intelligence')
    with page.expect_response('**/api/ask') as response:page.locator('#ask').click()
    result=response.value.json()
    rows=[row for row in result['facts'] if row['view']=='google_sheets_row']
    assert len(rows)==1 and rows[0]['row_number']==12 and rows[0]['tab_title']=='Tracker',result
    assert rows[0]['source_url'].endswith('&range=A12') and 'last_read_utc' in rows[0]
    page.locator('#status').filter(has_text='Finished.').wait_for()
    assert 'last read' in page.locator('#results').inner_text().lower()
    page.locator('#sheets-status').filter(has_text='26 tabs').wait_for()
    assert page.locator('#sync-sheets').is_visible()
    assert not errors,errors
    runtime=Path(__file__).parent/'runtime';runtime.mkdir(exist_ok=True)
    page.screenshot(path=str(runtime/'sheets-ui.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    browser.close()
print('Sheets UI passed: exact workbook/tab/row, provenance link, sync control and mobile layout.')
