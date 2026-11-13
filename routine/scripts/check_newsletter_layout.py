"""Render a local newsletter at desktop/mobile sizes and check for overflow."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--edition-date', default='27 October 2026')
    args = parser.parse_args()
    out = args.out.resolve()
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for label, width in [('desktop', 900), ('mobile', 390)]:
            page = browser.new_page(viewport={'width': width, 'height': 1000}, device_scale_factor=1)
            page.goto((out/'newsletter_preview.html').as_uri())
            page.screenshot(path=str(out/('email_'+label+'_top.png')))
            page.screenshot(path=str(out/('email_'+label+'.png')), full_page=True)
            result = page.evaluate('''() => ({width: innerWidth,
                contentWidth: document.documentElement.scrollWidth,
                links: [...document.querySelectorAll('a')].map(a => a.href),
                text: document.body.innerText})''')
            assert result['contentWidth'] <= width, 'Horizontal overflow at '+label
            assert all(url.startswith('https://') for url in result['links'])
            assert '\ufffd' not in result['text'], 'Broken text encoding'
            assert args.edition_date in result['text']
            checks.append(dict(viewport=label, width=width, content_width=result['contentWidth'], links=len(result['links']), passed=True))
            page.close()
        browser.close()
    (out/'email_layout_checks.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
    print(json.dumps(checks))


if __name__ == '__main__':
    main()
