"""One-off authenticated runtime inspection for the supplied task URL."""
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transcription_proj.settings')

import django
django.setup()

from core.services.dynamic_discovery.pool import POOL
from core.services.dynamic_discovery.resolver import _cookies_for

URL = 'https://translation.eaglewebcommerce.com/tasks/3927'


def inspect(browser):
    context = browser.new_context(viewport={'width': 1280, 'height': 720})
    try:
        context.add_cookies(_cookies_for(URL, os.environ['EAGLE_COOKIE']))
        page = context.new_page()
        seen = set()

        page.on('pageerror', lambda error: print(f'PAGEERROR {error}'))
        page.on('console', lambda message: (
            print(f'CONSOLE {message.type} {message.text}')
            if message.type in ('error', 'warning') else None
        ))

        def request(request):
            key = (request.resource_type, request.method, request.url)
            if key not in seen:
                seen.add(key)
                print(f'REQUEST  {request.resource_type:12} {request.method:6} {request.url}')

        page.on('request', request)

        def response(response):
            resource = response.request.resource_type
            content_type = response.headers.get('content-type', '')
            if resource in ('fetch', 'xhr', 'media', 'websocket') or content_type.startswith(('audio/', 'video/', 'application/vnd.apple', 'application/dash')):
                print(f'RESPONSE {response.status:3} {resource:12} {content_type:35} {response.url}')

        page.on('response', response)

        def route(route):
            if route.request.resource_type in ('image', 'font', 'media'):
                route.abort()
            else:
                route.continue_()

        page.route('**/*', route)
        page.goto(URL, wait_until='commit', timeout=15_000)
        page.wait_for_timeout(30_000)
        print('FINAL_URL', page.url)
        print('TITLE', page.title())
        print('TEXT', (page.locator('body').inner_text() or '')[:3000])
    finally:
        context.close()


POOL.submit(inspect, timeout=50)
