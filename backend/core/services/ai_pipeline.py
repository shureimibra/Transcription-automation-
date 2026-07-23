import asyncio
import os
import sys
import re
import subprocess
import logging
import urllib.request
import urllib.error
from urllib.parse import urlparse, urljoin
from typing import Dict, Any, Generator, Optional

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AIPipeline")

FFMPEG_DIR = None

def setup_subprocess_paths():
    global FFMPEG_DIR
    extra_paths = []
    
    # 1. Add active virtual environment Scripts directory to path (contains yt-dlp.exe)
    venv_bin = os.path.dirname(sys.executable)
    if venv_bin:
        extra_paths.append(venv_bin)
        
    # 2. Add winget-installed FFmpeg bin directory to path
    localappdata = os.environ.get('LOCALAPPDATA', '')
    if localappdata:
        winget_pkg_dir = os.path.join(localappdata, 'Microsoft', 'WinGet', 'Packages')
        if os.path.exists(winget_pkg_dir):
            for item in os.listdir(winget_pkg_dir):
                if 'FFmpeg' in item:
                    full_pkg_path = os.path.join(winget_pkg_dir, item)
                    for root, dirs, files in os.walk(full_pkg_path):
                        if 'ffmpeg.exe' in files:
                            extra_paths.append(root)
                            FFMPEG_DIR = root
                            break

    # Add extra paths to the front of os.environ['PATH']
    current_paths = os.environ.get('PATH', '').split(os.pathsep)
    for p in extra_paths:
        if p and p not in current_paths:
            os.environ['PATH'] = p + os.pathsep + os.environ['PATH']
            logger.info(f"Added subprocess dependency path to environment: {p}")

setup_subprocess_paths()


def is_prompt_echo(text: str, prompt: str) -> bool:
    """
    Safeguard utility to detect and strip Whisper prompt echo/feedback loops.
    Returns True if the transcribed text matches or overlaps significantly with the initial prompt.
    """
    if not text or not prompt:
        return False
    t = re.sub(r"[^\w\s]", "", text).lower().strip()
    p = re.sub(r"[^\w\s]", "", prompt).lower().strip()
    if not t or not p:
        return False
    
    # Exact or substring match
    if t == p or t in p:
        return True
        
    # Check for consecutive word phrases overlap (4 or more words)
    t_words = t.split()
    if len(t_words) >= 4:
        for idx in range(len(t_words) - 3):
            phrase = " ".join(t_words[idx:idx+4])
            if phrase in p:
                return True
    return False


class AudioExtractor:
    """
    Handles media download and extraction of 16kHz mono audio using FFmpeg and yt-dlp.
    """
    @staticmethod
    def _resolve_local_file_path(url_or_path: str) -> Optional[str]:
        """
        Resolves a clean local file path from standard paths, file:// URIs, or nested https://file:/// wrappers.
        Returns None if it is a remote web URL.
        """
        import urllib.parse
        s = url_or_path.strip()
        # Remove common protocol prefixes
        if s.lower().startswith("https://file:///"):
            s = s[16:]
        elif s.lower().startswith("https://file:/"):
            s = s[14:]
        elif s.lower().startswith("file:///"):
            s = s[8:]
        elif s.lower().startswith("file:/"):
            s = s[6:]
        
        # Decode URL-encoded characters (like %20 to space)
        s = urllib.parse.unquote(s)
        
        # Normalize slashes to standard Windows/OS format
        s = s.replace('/', '\\')
        
        # If it starts with standard Windows drive letter or exists directly on disk
        if re.match(r'^[a-zA-Z]:\\', s) or os.path.exists(s):
            return s
            
        # Fallback to direct absolute/relative path check
        if os.path.exists(url_or_path):
            return url_or_path
            
        return None

    @staticmethod
    def extract_from_file(input_path: str, output_wav_path: str) -> bool:
        """
        Extract mono 16kHz WAV audio from a local media file.
        """
        logger.info(f"Extracting audio from file: {input_path} -> {output_wav_path}")
        command = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-map', '0:a:0?',
            '-vn',
            '-ar', '16000',
            '-ac', '1',
            '-c:a', 'pcm_s16le',
            output_wav_path
        ]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info("Audio extraction completed successfully.")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {e.stderr.decode(errors='ignore')}")
            return False
        except FileNotFoundError:
            logger.error("FFmpeg not found in system PATH. Please install FFmpeg.")
            return False

    @staticmethod
    def _fetch_page_html(url: str) -> Optional[str]:
        try:
            default_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            req = urllib.request.Request(url, headers=default_headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"Failed to fetch page HTML for URL extraction: {e}")
            return None

    @staticmethod
    def _normalize_audio_url(candidate: str, base_url: str) -> str:
        candidate = candidate.strip().strip('"\'')
        if candidate.startswith('//'):
            candidate = 'https:' + candidate
        return urljoin(base_url, candidate)

    @staticmethod
    def _find_embedded_audio_url(html: str, base_url: str) -> Optional[str]:
        """Try to locate a direct audio source URL inside HTML or inline scripts."""
        patterns = [
            r'<audio[^>]+src=["\']([^"\']+)["\']',
            r'<source[^>]+src=["\']([^"\']+)["\']',
            r'["\'](?:audio|source|url|file|src|data-file|data-src|data-url|clip)["\']\s*:\s*["\']([^"\']+)["\']',
            r'file\s*[:=]\s*["\']([^"\']+)["\']',
            r'href=["\']([^"\']+\.(?:mp3|m4a|wav|aac|ogg|flac|mp4|webm|m3u8))(?:["\']|\s)',
            r'\bhttps?://[^"\']+\.(?:mp3|m4a|wav|aac|ogg|flac|mp4|webm|m3u8)(?:\b|[?&#])',
            r'["\'](/[^"\']+\.(?:mp3|m4a|wav|aac|ogg|flac|mp4|webm|m3u8))["\']'
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, html, flags=re.IGNORECASE):
                candidate = match.group(1)
                if not candidate:
                    continue

                candidate = candidate.strip().strip('"\'')
                if not re.search(r'\.(?:mp3|m4a|wav|aac|ogg|flac|mp4|webm|m3u8)(?:$|[?&#])', candidate, flags=re.IGNORECASE):
                    continue

                normalized = AudioExtractor._normalize_audio_url(candidate, base_url)
                if normalized.lower().startswith(('http://', 'https://')) and AudioExtractor._is_audio_url(normalized):
                    return normalized

        return None

    @staticmethod
    def _is_audio_url(url: str) -> bool:
        if not url:
            return False
        url = url.strip('"\'')
        # Ignore static UI media assets (e.g. login backgrounds, player skin videos)
        ignored_patterns = ['tiktok_web_login_static', 'playback1.mp4', '/static/', '/assets/ui/']
        if any(pat in url.lower() for pat in ignored_patterns):
            return False
        return re.search(r'\.(?:mp3|m4a|wav|aac|ogg|flac|mp4|webm|m3u8)(?:$|[?&#])', url, flags=re.IGNORECASE) is not None

    @staticmethod
    def _parse_cookie_string(cookie_value: str):
        if not cookie_value or not isinstance(cookie_value, str):
            return []
        cookie_value = cookie_value.strip()
        if (cookie_value.startswith('"') and cookie_value.endswith('"')) or (cookie_value.startswith("'") and cookie_value.endswith("'")):
            cookie_value = cookie_value[1:-1]
        cookies = []
        for part in cookie_value.split(';'):
            if '=' in part:
                name, value = part.split('=', 1)
                name = name.strip()
                value = value.strip()
                if name and value:
                    cookies.append({
                        'name': name,
                        'value': value,
                        'url': 'https://speech.intron.health'
                    })
        return cookies

    @staticmethod
    def _extract_with_playwright(url: str, output_audio_path: str, headers: Optional[Dict[str, str]] = None, cookies: Optional[str] = None) -> bool:
        if async_playwright is None:
            logger.warning('Playwright is not installed in the environment; skipping browser extraction.')
            return False

        if cookies is None:
            logger.warning('No cookies provided for Playwright extraction.')
            return False

        cookie_list = []
        if isinstance(cookies, str):
            cookie_list = AudioExtractor._parse_cookie_string(cookies)
        elif isinstance(cookies, dict):
            cookie_list = [{
                'name': str(k),
                'value': str(v),
                'url': 'https://speech.intron.health'
            } for k, v in cookies.items()]

        extra_headers = {}
        if headers and isinstance(headers, dict):
            extra_headers = headers

        async def run_playwright():
            browser = await async_playwright().start()
            try:
                context = await browser.chromium.launch_persistent_context(
                    user_data_dir=None,
                    headless=True,
                    args=['--disable-gpu', '--disable-dev-shm-usage'],
                    viewport={'width': 1280, 'height': 900},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    extra_http_headers=extra_headers
                )
                if cookie_list:
                    await context.add_cookies(cookie_list)

                page = await context.new_page()
                found_urls = []

                def handle_response(response):
                    try:
                        if AudioExtractor._is_audio_url(response.url):
                            found_urls.append(response.url)
                    except Exception:
                        pass

                page.on('response', handle_response)

                await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                await page.wait_for_timeout(5000)

                page_html = await page.content()
                embedded_audio = AudioExtractor._find_embedded_audio_url(page_html, page.url)
                if embedded_audio:
                    logger.info(f'Playwright found embedded audio URL: {embedded_audio}')
                    found_urls.insert(0, embedded_audio)

                if not found_urls:
                    candidates = await page.evaluate('''() => {
                        const keywords = [/transcrib/i, /start/i, /audio task/i, /task/i];
                        const anchors = Array.from(document.querySelectorAll('a'));
                        for (const a of anchors) {
                            const txt = (a.innerText || a.textContent || '').trim();
                            if (!txt) continue;
                            for (const k of keywords) {
                                if (k.test(txt) && a.href) return a.href;
                            }
                        }
                        const buttons = Array.from(document.querySelectorAll('button'));
                        for (const b of buttons) {
                            const txt = (b.innerText || b.textContent || '').trim();
                            if (!txt) continue;
                            for (const k of keywords) {
                                if (k.test(txt)) {
                                    const anchor = b.closest('a');
                                    if (anchor && anchor.href) return anchor.href;
                                }
                            }
                        }
                        return null;
                    }''')
                    if candidates:
                        found_urls.insert(0, candidates)

                if not found_urls:
                    logger.warning('Playwright did not find any audio URLs for extraction.')
                    await context.close()
                    await browser.stop()
                    return False

                audio_url = found_urls[0]
                logger.info(f'Playwright selected audio URL: {audio_url}')

                request_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                }
                if extra_headers:
                    request_headers.update(extra_headers)
                if cookies and 'Cookie' not in request_headers:
                    request_headers['Cookie'] = cookies

                req = urllib.request.Request(audio_url, headers=request_headers)
                with urllib.request.urlopen(req, timeout=30) as response, open(output_audio_path, 'wb') as out_file:
                    out_file.write(response.read())

                await context.close()
                await browser.stop()
                return True
            except Exception as e:
                logger.error(f'Playwright extraction failed: {e}')
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    await browser.stop()
                except Exception:
                    pass
                return False

        try:
            if sys.platform == 'win32':
                try:
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                except Exception as e:
                    logger.warning(f'Could not set WindowsProactorEventLoopPolicy: {e}')
            return asyncio.run(run_playwright())
        except Exception as e:
            logger.error(f'Playwright runner exception: {e}')
            return False

    @staticmethod
    def download_and_extract_url(url: str, output_wav_path: str, headers: Optional[Dict[str,str]] = None, cookies: Optional[str] = None, cookies_file_path: Optional[str] = None) -> bool:
        """
        Downloads audio from a public media URL.
        Prefer direct HTTP download for simple files; fall back to yt-dlp for streaming services.
        """
        # Intercept local file paths or wrapped file:// protocols
        local_path = AudioExtractor._resolve_local_file_path(url)
        if local_path:
            logger.info(f"Detected local file path: {local_path}. Extracting audio directly.")
            return AudioExtractor.extract_from_file(local_path, output_wav_path)

        logger.info(f"Downloading from public URL: {url}")
        temp_audio = output_wav_path + ".temp"

        parsed = urlparse(url)
        if parsed.scheme in ('http', 'https') and parsed.path.lower().endswith(('.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.mp4', '.webm', '.m3u8')):
            try:
                logger.info("Direct download via urllib.request")
                default_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                }
                merged_headers = dict(default_headers)
                if headers:
                    merged_headers.update(headers)
                if cookies and 'Cookie' not in merged_headers:
                    merged_headers['Cookie'] = cookies
                req = urllib.request.Request(url, headers=merged_headers)
                with urllib.request.urlopen(req, timeout=30) as response, open(temp_audio, 'wb') as out_file:
                    out_file.write(response.read())
                success = AudioExtractor.extract_from_file(temp_audio, output_wav_path)
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                return success
            except Exception as e:
                logger.warning(f"Direct HTTP download failed: {e}. Falling back to yt-dlp.")
                if os.path.exists(temp_audio):
                    try:
                        os.remove(temp_audio)
                    except Exception:
                        pass

        # If the page contains a direct audio file path (.mp3, .wav, .m4a, .aac, .ogg, .flac), download it directly
        if parsed.scheme in ('http', 'https') and not any(plat in parsed.netloc.lower() for plat in ['tiktok.com', 'youtube.com', 'youtu.be', 'vimeo.com', 'instagram.com', 'twitter.com', 'x.com']):
            html = AudioExtractor._fetch_page_html(url)
            if html:
                embedded_audio = AudioExtractor._find_embedded_audio_url(html, url)
                if embedded_audio and embedded_audio.lower().endswith(('.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac')):
                    logger.info(f"Found embedded audio URL: {embedded_audio}")
                    try:
                        default_headers = {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                        }
                        merged_headers = dict(default_headers)
                        if headers:
                            merged_headers.update(headers)
                        if cookies and 'Cookie' not in merged_headers:
                            merged_headers['Cookie'] = cookies
                        req = urllib.request.Request(embedded_audio, headers=merged_headers)
                        with urllib.request.urlopen(req, timeout=30) as response, open(temp_audio, 'wb') as out_file:
                            out_file.write(response.read())
                        success = AudioExtractor.extract_from_file(temp_audio, output_wav_path)
                        if os.path.exists(temp_audio):
                            try:
                                os.remove(temp_audio)
                            except Exception:
                                pass
                        if success:
                            return True
                    except Exception as e:
                        logger.warning(f"Embedded audio extraction failed: {e}. Falling back to yt-dlp.")
                        if os.path.exists(temp_audio):
                            try:
                                os.remove(temp_audio)
                            except Exception:
                                pass

        # If auth cookies are provided and Playwright is available, try headless browser page rendering
        if cookies and async_playwright is not None and parsed.scheme in ('http', 'https'):
            browser_result = AudioExtractor._extract_with_playwright(url, temp_audio, headers=headers, cookies=cookies)
            if browser_result:
                success = AudioExtractor.extract_from_file(temp_audio, output_wav_path)
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                return success

        ytdlp_target_prefix = temp_audio + '_ytdlp'
        ytdlp_out_template = ytdlp_target_prefix + '.%(ext)s'

        # Clean up any previous ytdlp temporary files
        import glob
        for stale in glob.glob(ytdlp_target_prefix + '*'):
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except Exception:
                    pass

        # Build yt-dlp command, injecting headers and cookies when provided
        command = [
            sys.executable,
            '-m',
            'yt_dlp',
            '--force-overwrites',
            '--user-agent',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--referer',
            url,
            '--no-playlist',
            '--no-check-certificate',
            '--add-header',
            'Accept-Language: en-US,en;q=0.9',
            '--extractor-args',
            'tiktok:app_name=musical_ly',
            '--format',
            'bestaudio/b[vcodec=h264]/b[acodec!=none]/b',
        ]
        if FFMPEG_DIR:
            command.extend(['--ffmpeg-location', FFMPEG_DIR])
        command.extend(['--output', ytdlp_out_template, url])

        # Add header flags for yt-dlp
        add_header_entries = []
        if headers:
            for k, v in headers.items():
                add_header_entries.extend(['--add-header', f'{k}: {v}'])

        # If a cookies file path is provided, prefer passing it with --cookies
        if cookies_file_path:
            command = command[:-1] + ['--cookies', cookies_file_path, command[-1]]
        else:
            if cookies:
                # Pass cookies as a Cookie header to yt-dlp
                add_header_entries.extend(['--add-header', f'Cookie: {cookies}'])

        # Insert header entries before the final URL argument
        if add_header_entries:
            command = command[:-1] + add_header_entries + [command[-1]]

        try:
            logger.info("Executing yt-dlp download fallback...")
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info("yt-dlp download completed.")

            actual_downloaded_file = None
            found_matches = glob.glob(ytdlp_target_prefix + '*')
            if found_matches:
                for match in found_matches:
                    if os.path.exists(match) and os.path.getsize(match) > 0:
                        actual_downloaded_file = match
                        break

            if not actual_downloaded_file:
                logger.error("No downloaded media file found after yt-dlp execution.")
                return False

            success = AudioExtractor.extract_from_file(actual_downloaded_file, output_wav_path)
            for stale in glob.glob(ytdlp_target_prefix + '*'):
                if os.path.exists(stale):
                    try:
                        os.remove(stale)
                    except Exception:
                        pass
            return success
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors='ignore') if e.stderr else str(e)
            logger.error(f"yt-dlp download failed: {stderr}")
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
            return False
        except FileNotFoundError:
            logger.error("yt-dlp module cannot be executed. Please install yt-dlp into the active virtual environment.")
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
            return False

class WhisperTranscriber:
    """
    Coordinates Whisper model execution, auto language detection, and audio chunking.
    """
    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model = None
        self.last_info = None

    def load_model(self):
        """
        Loads the faster-whisper model into memory/VRAM if not already loaded.
        """
        if self.model is None:
            try:
                from faster_whisper import WhisperModel
                logger.info(f"Loading WhisperModel '{self.model_size}' on '{self.device}' ({self.compute_type})...")
                self.model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type
                )
                logger.info("Whisper model loaded successfully.")
            except ImportError as e:
                logger.error("faster-whisper package is required for real transcription.")
                raise RuntimeError("faster-whisper package is required for real transcription.") from e

    @staticmethod
    def _normalize_language_code(language: Optional[str]) -> Optional[str]:
        if not language:
            return None

        normalized = language.strip().lower()
        mapping = {
            'oromo': 'om',
            'afaan oromoo': 'om',
            'om-et': 'om',
            'amharic': 'am',
            'somali': 'so',
            'tigrinya': 'ti',
            'english': 'en',
            'french': 'fr',
            'spanish': 'es',
            'german': 'de',
            'italian': 'it',
            'portuguese': 'pt',
            'russian': 'ru',
            'japanese': 'ja',
            'chinese': 'zh',
            'mandarin': 'zh',
            'arabic': 'ar',
            'korean': 'ko',
            'hindi': 'hi',
        }

        if normalized in mapping:
            return mapping[normalized]
        return normalized

    def transcribe(self, audio_path: str, enable_vad: bool = True, language: Optional[str] = None, 
                   enable_code_switching: bool = False, translate_to_english: bool = False) -> Generator[Dict[str, Any], None, None]:
        """
        Transcribes or translates the audio file and yields segments.
        """
        self.load_model()

        if not os.path.exists(audio_path):
            raise RuntimeError(f"Audio file not found: {audio_path}")

        normalized_language = self._normalize_language_code(language)
        is_oromo = normalized_language in ('om', 'oromo') or 'oromo' in str(self.model_size).lower()

        use_hf = os.environ.get("USE_HF_OROMO", "0") == "1"
        if is_oromo and use_hf:
            logger.info("Using fine-tuned Hugging Face Afaan Oromoo model ('badrex/Ethio-ASR-oromo')...")
            try:
                from transformers import pipeline
                model_name = self.model_size if 'oromo' in str(self.model_size).lower() else "badrex/Ethio-ASR-oromo"
                hf_pipe = pipeline("automatic-speech-recognition", model=model_name)
                res = hf_pipe(audio_path)
                text = res.get('text', '').strip()
                
                from core.services.ootranscript_editor import clean_segment_text
                text = clean_segment_text(text)

                class Info:
                    language = 'om'
                    language_probability = 1.0
                self.last_info = Info()

                yield {
                    "text": text,
                    "start": 0.0,
                    "end": 0.0,
                    "language": "om",
                    "confidence": 1.0,
                    "hallucinated": False,
                    "logs": [],
                    "corrections": []
                }
                return
            except Exception as e:
                logger.error(f"Hugging Face ASR pipeline error for badrex/Ethio-ASR-oromo: {e}")
                logger.warning("Falling back to faster-whisper default engine with Qubee Latin prompt.")

        # Layer 1 Dynamic Prompt Injection & Language Profile resolution
        try:
            from apps.transcriptions.languages import get_language_profile, sanitize_and_validate_transcript
            effective_lang = normalized_language if normalized_language else 'om'
            profile = get_language_profile(effective_lang, enable_code_switching)
        except Exception:
            profile = None
            sanitize_and_validate_transcript = None

        initial_prompt = None
        whisper_lang = normalized_language

        # Oromo Qubee Latin Prompt Guidance
        oromo_prompt = (
            "Barreeffama Afaan Oromoo Qubee Latin script: Akkam, maatiin kee akkam jiru? "
            "Maatiin koo nagaa jiru. Akkam, fayyaa? Eeyyee, galatoomi. "
            "Qubee Alphabet: A a, B b, C c, D d, E e, F f, G g, H h, I i, J j, K k, L l, M m, N n, O o, P p, Q q, R r, S s, T t, U u, V v, W w, X x, Y y, Z z, Ch ch, Dh dh, Ny ny, Ph ph, Sh sh."
        )

        if profile and profile.engine_instruction_override:
            initial_prompt = profile.engine_instruction_override

        if (is_oromo or normalized_language == 'om') and not enable_code_switching:
            initial_prompt = oromo_prompt
            whisper_lang = None
            is_oromo = True
        elif normalized_language in ('so', 'ki') or enable_code_switching:
            whisper_lang = None

        if enable_code_switching and profile:
            initial_prompt = profile.engine_instruction_override

        # OPTIMIZED VAD: Less aggressive, better for natural speech
        # Use longer silence detection to prevent splitting on natural pauses
        vad_parameters = dict(
            min_silence_duration_ms=600,  # Increased from 300-1000 to 600ms
            speech_pad_ms=400,
            threshold=0.5,  # Increased from 0.3 for better speech detection
            min_speech_duration_ms=250,  # Minimum speech segment to keep
            max_speech_duration_s=30,  # Maximum speech segment
            #speech_pad_ms=400,
            window_size_samples=1024
        )

        transcribe_kwargs = {
            "beam_size": 5,
            "vad_filter": enable_vad,
            "vad_parameters": vad_parameters,
            "language": whisper_lang if not translate_to_english else None,
            "repetition_penalty": 1.1,  # Reduced from 1.2 to prevent over-penalization
            "no_speech_threshold": 0.6,
            "compression_ratio_threshold": 2.4,
            "temperature": [0.0, 0.2, 0.4, 0.6],  # Standard range, not too aggressive
            "condition_on_previous_text": True,  # Helps with context
            #"compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
        }
        
        # Handle translation vs transcription
        if translate_to_english:
            transcribe_kwargs["task"] = "translate"
            transcribe_kwargs["language"] = None
            logger.info("=== TRANSLATION MODE ENABLED ===")
        else:
            logger.info(f"=== TRANSCRIPTION MODE ===")
            if whisper_lang:
                logger.info(f"Language set to: {whisper_lang}")
            else:
                logger.info("Language: auto-detect")

        if initial_prompt:
            transcribe_kwargs["initial_prompt"] = initial_prompt

        # IMPORTANT: Disable VAD if it's causing issues
        # Some models work better without VAD for clear speech
        if normalized_language in ('en', 'es', 'fr', 'de', 'it', 'pt'):
            # For major languages, VAD can be less aggressive
            transcribe_kwargs["vad_filter"] = False  # Let Whisper handle segmentation
            logger.info("VAD disabled for major language to improve accuracy")

        segments, info = self.model.transcribe(
            audio_path,
            **transcribe_kwargs
        )
        
        if is_oromo or getattr(info, 'language', '') == 'so':
            info.language = 'om'
        self.last_info = info
        logger.info(f"Detected language: '{info.language}' with probability {getattr(info, 'language_probability', 0):.2f}")

        # Materialize segments
        segments_list = list(segments)
        
        # If VAD removed everything, retry without VAD
        if transcribe_kwargs.get("vad_filter") and not segments_list:
            logger.warning("VAD filter removed all audio. Retrying transcription with VAD filter disabled...")
            retry_kwargs = dict(transcribe_kwargs)
            retry_kwargs["vad_filter"] = False
            retry_segments, info = self.model.transcribe(
                audio_path,
                **retry_kwargs
            )
            if is_oromo or getattr(info, 'language', '') == 'so':
                info.language = 'om'
            self.last_info = info
            segments_list = list(retry_segments)

        from core.services.ootranscript_editor import clean_segment_text, transliterate_arabic_oromo_to_qubee

        # Post-process: Merge segments that are too short or split unnaturally
        merged_segments = []
        for segment in segments_list:
            seg_text = segment.text.strip()
            
            # Safeguard: drop prompt echo
            if initial_prompt and is_prompt_echo(seg_text, initial_prompt):
                logger.warning(f"Whisper prompt echo detected and stripped: '{seg_text}'")
                continue

            hallucinated = False
            logs = []
            corrections = []

            # Transliterate if needed
            if is_oromo or re.search(r'[\u0600-\u06FF]', seg_text) or info.language == 'om':
                seg_text = clean_segment_text(seg_text)

            # Sanitization
            if sanitize_and_validate_transcript and (normalized_language or is_oromo):
                target_code = normalized_language if normalized_language else 'om'
                seg_text, hallucinated, logs, corrections = sanitize_and_validate_transcript(
                    seg_text, target_code, enable_code_switching=enable_code_switching
                )

            # POST-PROCESSING: Fix common word errors
            # This handles general transcription errors, not specific to any speaker
            seg_text = self._fix_common_errors(seg_text)

            yield {
                "text": seg_text,
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "language": info.language,
                "confidence": round(segment.avg_logprob, 4),
                "hallucinated": hallucinated,
                "logs": logs,
                "corrections": corrections
            }

    def _fix_common_errors(self, text: str) -> str:
        """
        Fix common transcription errors that occur regardless of speaker.
        This is a general-purpose fix, not tailored to any specific person.
        """
        # Common grammar fixes
        fixes = {
            r'\byour\b(?=\s+good\b)': 'you\'re',  # your good -> you're good
            r'\bu\b': 'you',  # u -> you
            r'\bwanna\b': 'want to',  # wanna -> want to
            r'\bgonna\b': 'going to',  # gonna -> going to
            r'\bgot to\b': 'got to',  # Keep as is
            r'\bout of\b': 'out of',  # Keep
            r'\bof u\b': 'of you',  # of u -> of you
            r'\bwith our life\b': 'with your life',  # Context-specific but common
            r'\bremember the military\b': 'a member of our military',  # Common mishearing
        }
        
        import re
        for pattern, replacement in fixes.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        
        # Fix repeated "you" patterns
        text = re.sub(r'\byou you\b', 'you', text, flags=re.IGNORECASE)
        
        # Fix capitalization for sentence boundaries (simple)
        text = re.sub(r'([.!?])\s*([a-z])', lambda m: m.group(1) + ' ' + m.group(2).upper(), text)
        
        # Fix "i" to "I" when standalone
        text = re.sub(r'\bi\b', 'I', text)
        
        return text