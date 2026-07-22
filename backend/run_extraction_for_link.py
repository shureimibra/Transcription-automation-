import sys
import os
from pathlib import Path

if len(sys.argv) < 2:
    print('Usage: python run_extraction_for_link.py <url>')
    sys.exit(1)

url = sys.argv[1]
output_wav = os.path.abspath('intron_test_output.wav')

# Ensure we can import AudioExtractor
try:
    from core.services.ai_pipeline import AudioExtractor
except Exception:
    try:
        from backend.core.services.ai_pipeline import AudioExtractor
    except Exception as e:
        print('IMPORT_ERROR', e)
        sys.exit(2)

print('Attempting download and extract for:', url)
ok = AudioExtractor.download_and_extract_url(url, output_wav)
print('RESULT', ok, 'output:', output_wav if ok else 'N/A')
if ok:
    print('WAV_EXISTS', Path(output_wav).exists(), Path(output_wav).stat().st_size)
