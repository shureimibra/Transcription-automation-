import os
import sys
import re
import subprocess
import logging
import urllib.request
import urllib.error
from urllib.parse import urlparse, urljoin
from typing import Dict, Any, Generator, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AIPipeline")

def setup_subprocess_paths():
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
                            break

    # Add extra paths to the front of os.environ['PATH']
    current_paths = os.environ.get('PATH', '').split(os.pathsep)
    for p in extra_paths:
        if p and p not in current_paths:
            os.environ['PATH'] = p + os.pathsep + os.environ['PATH']
            logger.info(f"Added subprocess dependency path to environment: {p}")

setup_subprocess_paths()


class AudioExtractor:
    """
    Handles media download and extraction of 16kHz mono audio using FFmpeg and yt-dlp.
    """
    @staticmethod
    def extract_from_file(input_path: str, output_wav_path: str) -> bool:
        """
        Extract mono 16kHz WAV audio from a local media file.
        """
        logger.info(f"Extracting audio from file: {input_path} -> {output_wav_path}")
        command = [
            'ffmpeg', '-y',
            '-i', input_path,
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
    def _find_embedded_audio_url(html: str, base_url: str) -> Optional[str]:
        """Try to locate a direct audio source URL inside HTML or inline scripts."""
        # Prioritize common audio file references
        for pattern in [r"https?://[^\"']+\.mp3", r"https?://[^\"']+\.m4a", r"https?://[^\"']+\.wav", r"https?://[^\"']+\.aac", r"https?://[^\"']+\.ogg", r"https?://[^\"']+\.flac"]:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if match:
                return match.group(0)

        # Look for custom audio file paths used by the page and resolve relative URLs
        match = re.search(r"file\s*:\s*['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE)
        if match:
            return urljoin(base_url, match.group(1))

        match = re.search(r"['\"](\/audios\/[^'\"]+\.mp3[^'\"]*)['\"]", html, flags=re.IGNORECASE)
        if match:
            return urljoin(base_url, match.group(1))

        return None

    @staticmethod
    def download_and_extract_url(url: str, output_wav_path: str) -> bool:
        """
        Downloads audio from a public media URL.
        Prefer direct HTTP download for simple files; fall back to yt-dlp for streaming services.
        """
        logger.info(f"Downloading from public URL: {url}")
        temp_audio = output_wav_path + ".temp"

        parsed = urlparse(url)
        if parsed.scheme in ('http', 'https') and parsed.path.lower().endswith(('.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac')):
            try:
                logger.info("Direct download via urllib.request")
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                })
                with urllib.request.urlopen(req, timeout=30) as response, open(temp_audio, 'wb') as out_file:
                    out_file.write(response.read())
                success = AudioExtractor.extract_from_file(temp_audio, output_wav_path)
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                return success
            except Exception as e:
                logger.warning(f"Direct HTTP download failed: {e}. Falling back to yt-dlp.")
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)

        # If the page contains an embedded audio file path, download it directly
        if parsed.scheme in ('http', 'https'):
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                })
                with urllib.request.urlopen(req, timeout=30) as response:
                    html = response.read().decode('utf-8', errors='ignore')
                embedded_audio = AudioExtractor._find_embedded_audio_url(html, url)
                if embedded_audio:
                    logger.info(f"Found embedded audio URL: {embedded_audio}")
                    req = urllib.request.Request(embedded_audio, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    })
                    with urllib.request.urlopen(req, timeout=30) as response, open(temp_audio, 'wb') as out_file:
                        out_file.write(response.read())
                    success = AudioExtractor.extract_from_file(temp_audio, output_wav_path)
                    if os.path.exists(temp_audio):
                        os.remove(temp_audio)
                    return success
            except Exception as e:
                logger.warning(f"Embedded audio extraction failed: {e}. Falling back to yt-dlp.")
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)

        command = [
            sys.executable,
            '-m',
            'yt_dlp',
            '--user-agent',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--no-playlist',
            '--format',
            'bestaudio/best',
            '--output',
            temp_audio,
            url
        ]

        try:
            logger.info("Executing yt-dlp download fallback...")
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info("yt-dlp download completed.")

            success = AudioExtractor.extract_from_file(temp_audio, output_wav_path)
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
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

    def transcribe(self, audio_path: str, enable_vad: bool = True) -> Generator[Dict[str, Any], None, None]:
        """
        Transcribes the audio file and yields segments as they are processed.
        """
        self.load_model()

        # Core Faster-Whisper Pipeline
        if not os.path.exists(audio_path):
            raise RuntimeError(f"Audio file not found: {audio_path}")

        segments, info = self.model.transcribe(
            audio_path,
            beam_size=5,
            vad_filter=enable_vad,
            vad_parameters=dict(min_speech_duration_ms=250)
        )
        
        logger.info(f"Detected language: '{info.language}' with probability {info.language_probability:.2f}")

        for segment in segments:
            yield {
                "text": segment.text.strip(),
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "language": info.language,
                "confidence": round(segment.avg_logprob, 4)
            }

# Quick test routine
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ai_pipeline.py <input_audio_or_video_file_or_url>")
        sys.exit(1)
        
    source = sys.argv[1]
    out_wav = "extracted_audio.wav"
    
    if source.startswith("http://") or source.startswith("https://"):
        success = AudioExtractor.download_and_extract_url(source, out_wav)
    else:
        success = AudioExtractor.extract_from_file(source, out_wav)
        
    if success:
        transcriber = WhisperTranscriber(model_size="tiny")
        for chunk in transcriber.transcribe(out_wav):
            print(f"[{chunk['start']:.2f}s - {chunk['end']:.2f}s] {chunk['text']}")
        
        if os.path.exists(out_wav):
            os.remove(out_wav)
    else:
        print("Failed to process audio extraction.")
