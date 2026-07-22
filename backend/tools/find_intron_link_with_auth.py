import asyncio
import json
import re
import sys
from pathlib import Path
from playwright.async_api import async_playwright

KEYWORDS = [
    r'transcrib',
    r'start',
    r'audio task',
    r'task',
    r'speech',
    r'play',
]
AUDIO_EXTENSIONS = [
    '.mp3', '.m4a', '.wav', '.aac', '.ogg', '.flac', '.mp4', '.webm', '.m3u8'
]


def normalize_url(value: str) -> str:
    return value.strip().strip('"\'')


def matches_audio_url(url: str) -> bool:
    return any(url.lower().split('?')[0].endswith(ext) for ext in AUDIO_EXTENSIONS)


def matches_keyword(text: str) -> bool:
    if not text:
        return False
    text = text.lower().strip()
    return any(re.search(k, text, re.IGNORECASE) for k in KEYWORDS)


async def gather_candidates(page):
    candidates = []
    anchors = await page.query_selector_all('a')
    for anchor in anchors:
        href = await anchor.get_attribute('href')
        text = await anchor.inner_text()
        if href:
            href = normalize_url(href)
            if matches_audio_url(href) or matches_keyword(text):
                candidates.append(('anchor', text, href))

    buttons = await page.query_selector_all('button, input[type=button], input[type=submit], [role="button"]')
    for button in buttons:
        text = await button.inner_text()
        href = await button.get_attribute('href')
        onclick = await button.get_attribute('onclick')
        if href:
            href = normalize_url(href)
            if matches_audio_url(href) or matches_keyword(text):
                candidates.append(('button-href', text, href))
        if onclick and matches_keyword(text):
            candidates.append(('button-onclick', text, onclick))

    return candidates


async def inspect_network(page, hits):
    def on_request(request):
        url = request.url
        if matches_audio_url(url):
            hits.append(('request', url, request.resource_type))

    def on_response(response):
        url = response.url
        if matches_audio_url(url):
            hits.append(('response', url, response.request.resource_type))

    page.on('request', on_request)
    page.on('response', on_response)


async def apply_auth(context, headers, cookies):
    if headers:
        await context.set_extra_http_headers(headers)
    if cookies:
        parsed = []
        if isinstance(cookies, str):
            for pair in cookies.split(';'):
                if '=' in pair:
                    name, value = pair.split('=', 1)
                    parsed.append({'name': name.strip(), 'value': value.strip(), 'url': 'https://speech.intron.health'})
        elif isinstance(cookies, dict):
            for name, value in cookies.items():
                parsed.append({'name': name, 'value': value, 'url': 'https://speech.intron.health'})
        if parsed:
            await context.add_cookies(parsed)


async def find_link(url: str, headers=None, cookies=None):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=['--disable-gpu', '--disable-dev-shm-usage'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 900},
        )
        await apply_auth(context, headers, cookies)
        page = await context.new_page()
        network_hits = []
        await inspect_network(page, network_hits)

        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        except Exception as exc:
            print('ERROR: page.goto failed:', exc)
            await browser.close()
            return 1

        await page.wait_for_timeout(5000)
        print('PAGE_TITLE:', await page.title())
        print('PAGE_URL:', page.url)

        candidates = await gather_candidates(page)
        if candidates:
            print('FOUND_CANDIDATES:')
            for kind, text, link in candidates:
                print(f'{kind}\t{text}\t{link}')

        if network_hits:
            print('AUDIO_NETWORK_HITS:')
            for kind, link, rtype in network_hits:
                print(f'{kind}\t{rtype}\t{link}')

        if candidates:
            for kind, text, link in candidates:
                if matches_audio_url(link) or link.startswith('http'):
                    print('BEST_LINK:', link)
                    await browser.close()
                    return 0

        clicked = False
        button_handles = await page.query_selector_all('button, input[type=button], input[type=submit], [role="button"]')
        for button in button_handles:
            text = await button.inner_text()
            if matches_keyword(text):
                try:
                    print('CLICKING_BUTTON:', text)
                    await button.click(timeout=20000)
                    await page.wait_for_timeout(5000)
                    clicked = True
                    break
                except Exception as e:
                    print('CLICK_ERR:', e)

        if clicked:
            page_url = page.url
            print('AFTER_CLICK_URL:', page_url)
            if matches_audio_url(page_url) or page_url.startswith('http'):
                print('BEST_LINK_AFTER_CLICK:', page_url)
                await browser.close()
                return 0

            candidates = await gather_candidates(page)
            if candidates:
                print('FOUND_CANDIDATES_AFTER_CLICK:')
                for kind, text, link in candidates:
                    print(f'{kind}\t{text}\t{link}')
                    if matches_audio_url(link) or link.startswith('http'):
                        print('BEST_LINK_AFTER_CLICK:', link)
                        await browser.close()
                        return 0

        print('NO_DIRECT_LINK_FOUND')
        await browser.close()
        return 1


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python find_intron_link_with_auth.py <url> [cookies_json_or_string] [headers_json]')
        sys.exit(1)

    url = sys.argv[1]
    cookies_arg = sys.argv[2] if len(sys.argv) > 2 else None
    headers_arg = sys.argv[3] if len(sys.argv) > 3 else None

    cookies = None
    headers = None
    if cookies_arg:
        try:
            cookies = json.loads(cookies_arg)
        except json.JSONDecodeError:
            cookies = cookies_arg
    if headers_arg:
        try:
            headers = json.loads(headers_arg)
        except json.JSONDecodeError:
            headers = None

    raise SystemExit(asyncio.run(find_link(url, headers=headers, cookies=cookies)))
