import argparse
import os
import tempfile
import sys
# Add backend directory to sys.path if needed
tools_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(tools_dir, '..'))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from core.services.ai_pipeline import AudioExtractor, WhisperTranscriber
    from core.services.ootranscript_editor import clean_transcript
except ModuleNotFoundError:
    from backend.core.services.ai_pipeline import AudioExtractor, WhisperTranscriber
    from backend.core.services.ootranscript_editor import clean_transcript

parser = argparse.ArgumentParser(description='Test audio extraction and transcription for a URL.')
parser.add_argument('url', help='URL to extract audio from')
parser.add_argument('--model_size', default='tiny', choices=['tiny', 'small', 'base', 'medium', 'large'], help='Whisper model size to use')
parser.add_argument('--language', default=None, help='Optional language override for the Whisper transcription')
args = parser.parse_args()
URL = args.url

out_wav = os.path.abspath(os.path.join(tempfile.gettempdir(), 'test_link_extract.wav'))
print('Output WAV:', out_wav)

success = AudioExtractor.download_and_extract_url(URL, out_wav)
if not success:
    print('Initial audio extraction failed; attempting Playwright render...')
    # Try headless Playwright rendering to discover audio URL (no cookies)
    try:
        from playwright.async_api import async_playwright
        import asyncio

        async def fetch_with_playwright():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                found_urls = []

                def handle_response(response):
                    try:
                        url = response.url
                        if AudioExtractor._is_audio_url(url):
                            found_urls.append(url)
                    except Exception:
                        pass

                page.on('response', handle_response)
                await page.goto(URL, wait_until='domcontentloaded', timeout=60000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                embedded = AudioExtractor._find_embedded_audio_url(html, page.url)
                if embedded:
                    found_urls.insert(0, embedded)
                # Evaluate DOM for audio/source tags or data attributes that may contain URLs
                dom_candidate = await page.evaluate('''() => {
                    const elems = Array.from(document.querySelectorAll('audio, source, [data-src], [data-audio], [data-file]'));
                    for (const el of elems) {
                        const src = el.src || el.getAttribute('src') || el.getAttribute('data-src') || el.getAttribute('data-audio') || el.getAttribute('data-file') || (el.dataset && (el.dataset.src || el.dataset.audio || el.dataset.file));
                        if (src) return src;
                    }
                    // search scripts for urls
                    const scripts = Array.from(document.querySelectorAll('script'));
                    for (const s of scripts) {
                        const txt = s.innerText || '';
                        const m = txt.match(/https?:\\/\\/[^\"'\\s]+\\.(?:mp3|m4a|wav|aac|ogg|flac|mp4|webm|m3u8)/i);
                        if (m) return m[0];
                    }
                    return null;
                }''')
                if dom_candidate:
                    found_urls.insert(0, dom_candidate)
                await context.close()
                await browser.close()
                return found_urls

        urls = asyncio.run(fetch_with_playwright())
        if not urls:
            print('Playwright did not find audio URLs.')
            sys.exit(2)
        audio_url = urls[0]
        print('Playwright found audio URL:', audio_url)
        # download audio directly
        import urllib.request
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(audio_url, headers=headers)
        temp_audio = out_wav + '.temp'
        with urllib.request.urlopen(req, timeout=30) as resp, open(temp_audio, 'wb') as f:
            f.write(resp.read())
        # extract with ffmpeg
        success = AudioExtractor.extract_from_file(temp_audio, out_wav)
        if os.path.exists(temp_audio):
            os.remove(temp_audio)
        if not success:
            print('Extraction from downloaded audio failed')
            sys.exit(2)
    except Exception as e:
        print('Playwright attempt failed:', e)
        sys.exit(2)

print('Extraction succeeded, transcribing...')
transcriber = WhisperTranscriber(model_size=args.model_size)
segments = []
for chunk in transcriber.transcribe(out_wav, enable_vad=True, language=args.language):
    segments.append({'speaker': 'Speaker 1', 'start_time': chunk['start'], 'text': chunk['text']})

print('Raw segments:')
for s in segments:
    print(s)

cleaned = clean_transcript({'segments': segments}, verbatim=False)
print('\nCLEANED TRANSCRIPT:\n')
print(cleaned)

# cleanup
try:
    os.remove(out_wav)
except Exception:
    pass
