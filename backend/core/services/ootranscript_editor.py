import re
from typing import List, Dict, Optional

# Lightweight Oromo transcription post-processor following provided GoTranscript rules.
FILLER_WORDS = set([
    "eee", "mmm", "uhh", "uh", "um", "mm", "hmm",
    "sigalee miti", "egaa", "akkasumas", "eeyyee", "tole"
])

SOUND_TAGS = {
    'kofla', 'harka dhawuu', 'qufaa', 'wal-keessa dubbachuu', 'hafuura baafachuu',
    'sagalee muuziqaa', 'sagalee duubaa', 'sagalee cite'
}

NUM_WORDS = {
    '1': 'tokko', '2': 'lama', '3': 'sadii', '4': 'afur', '5': 'shan',
    '6': 'jaha', '7': 'torba', '8': 'saddeet', '9': 'sagal'
}

SPELLING_CORRECTIONS = {
    'tiyyaara': 'xiyyaara',
    'tiyyara': 'xiyyaara',
    'tiyara': 'xiyyaara'
}


def _remove_fillers(text: str) -> str:
    # remove common filler words (case-insensitive) and reduce repeated short tokens
    tokens = text.split()
    out = []
    for t in tokens:
        tl = t.strip().lower().strip(',.?"')
        if tl in FILLER_WORDS:
            continue
        # collapse stutter like "baay'ee, baay'ee" -> keep up to two
        if len(out) and out[-1].lower().strip(',.?"') == tl:
            # allow at most two repetitions
            if out[-1].count(tl) >= 2:
                continue
        out.append(t)
    return ' '.join(out)


def _normalize_sound_tags(text: str) -> str:
    # map bracketed/unbracketed sound events to accepted tags
    def repl(match):
        inner = match.group(1).strip().lower()
        if inner in SOUND_TAGS:
            return f'[{inner}]'
        # if contains digits like inaudible timestamps, preserve as-is
        if 'inaudible' in inner or 'unintelligible' in inner:
            return f'[{inner}]'
        # fallback to [sagalee duubaa]
        return '[sagalee duubaa]'

    text = re.sub(r"\[(.*?)\]", repl, text)
    return text


def _apply_spelling_corrections(text: str) -> str:
    for k, v in SPELLING_CORRECTIONS.items():
        pattern = re.compile(re.escape(k), flags=re.IGNORECASE)
        text = pattern.sub(v, text)
    return text


def _format_numbers(text: str) -> str:
    # replace standalone digits 1-9 with Oromo words
    def numrepl(m):
        s = m.group(0)
        return NUM_WORDS.get(s, s)

    return re.sub(r'(?<!\d)([1-9])(?!\d)', numrepl, text)


def _fix_punctuation_and_caps(text: str) -> str:
    # Ensure sentences start with capital and end with . or ?; replace ! with .
    text = text.replace('!', '.')
    # Split on sentence enders (., ?) while keeping delimiters
    parts = re.split(r'([\.\?])\s*', text)
    out = []
    for i in range(0, len(parts), 2):
        sentence = parts[i].strip()
        sep = parts[i+1] if i+1 < len(parts) else ''
        if not sentence:
            continue
        sentence = sentence[0].upper() + sentence[1:] if len(sentence) > 1 else sentence.upper()
        # ensure no exclamation marks
        if sep == '!':
            sep = '.'
        if not sep:
            sep = '.'
        out.append(sentence + sep)
    return ' '.join(out)


ARABIC_OROMO_PHRASES = [
    (r'\bافانا\b|\bأفانا\b|\bافان\b', 'Afaan'),
    (r'\bرو مو\b|\bرومو\b', 'Oromoo'),
    (r'\bبیک تا\b|\bبيكت\b', 'beektaa'),
    (r'\bایی\b|\bايي\b', 'Eeyyee'),
    (r'\bنم\b', 'nama'),
    (r'\bبیک\b', 'beeka'),
    (r'\bاکم\b|\bأكم\b|\bایک تم\b', 'akkam'),
    (r'\bان\b', 'Ani'),
    (r'\bاگما\b', 'akkasuma'),
    (r'\bگلت\b', 'galatoomi'),
    (r'\bحواقى\b|\bحواقي\b', 'Waaqayyo'),
    (r'\bحتی\b', 'haati'),
    (r'\bاشن\b', 'isheen'),
    (r'\bماتن\b', 'maatiin'),
    (r'\bکے\b', 'kee'),
    (r'\bجرو\b|\bجروت\b', 'jiru'),
    (r'\bسنگا\b|\bنگا\b', 'nagaa'),
    (r'\bاب بان\b', 'abbaan'),
    (r'\bجرا\b', 'jira'),
    (r'\bحرب بھی\b', 'harrabnis'),
    (r'\bطولی\b', 'tole'),
    (r'\bگویا\b', 'guyyaa'),
    (r'\bگا رسی\b', 'gaarii'),
    (r'\bفاتاو\b', "haata'u"),
    (r'\bصفیس\b', 'nagaadhaan'),
    (r'\bکو\b', 'koo'),
]

ARABIC_CHAR_MAP = {
    'ا': 'a', 'أ': 'a', 'إ': 'i', 'آ': 'aa', 'ب': 'b', 'ت': 't', 'ث': 's',
    'ج': 'j', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'z', 'ر': 'r', 'ز': 'z',
    'س': 's', 'ش': 'sh', 'ص': 's', 'ض': 'd', 'ط': 't', 'ظ': 'z', 'ع': 'a',
    'غ': 'g', 'ف': 'f', 'ق': 'q', 'ك': 'k', 'ک': 'k', 'گ': 'g', 'ل': 'l',
    'م': 'm', 'ن': 'n', 'ه': 'h', 'و': 'oo', 'ي': 'ii', 'ى': 'aa', 'ئ': 'e',
    'ء': "'", 'پ': 'p', 'چ': 'ch', '؟': '?', '،': ','
}


def transliterate_arabic_oromo_to_qubee(text: str) -> str:
    """
    Transliterates ASR outputs generated in Arabic script into clean Qubee Latin alphabet text.
    """
    if not re.search(r'[\u0600-\u06FF]', text):
        return text

    # First apply known word/phrase-level phonetic mappings
    for pattern, replacement in ARABIC_OROMO_PHRASES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # If any residual Arabic characters remain, map char-by-char
    if re.search(r'[\u0600-\u06FF]', text):
        chars = []
        for ch in text:
            chars.append(ARABIC_CHAR_MAP.get(ch, ch))
        text = ''.join(chars)

    # Clean up duplicate whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def clean_segment_text(raw: str, verbatim: bool = False) -> str:
    text = raw.strip()
    # Transliterate any Arabic script output into Qubee Latin script
    text = transliterate_arabic_oromo_to_qubee(text)
    if not verbatim:
        text = _remove_fillers(text)
    text = _normalize_sound_tags(text)
    text = _apply_spelling_corrections(text)
    text = _format_numbers(text)
    text = _fix_punctuation_and_caps(text)
    # enforce no exclamation marks anywhere per GoTranscript rules
    text = text.replace('!', '.')
    return text


def render_transcript_from_segments(segments: List[Dict], verbatim: bool = False) -> str:
    lines: List[str] = []
    for seg in segments:
        speaker = seg.get('speaker') or seg.get('speaker_label') or 'Speaker'
        # speaker label must be bold and followed by a colon and single space
        speaker_label = f"**{speaker}:**"
        raw = seg.get('text', '')
        cleaned = clean_segment_text(raw, verbatim=verbatim)
        # break long paragraphs: limit 500 chars
        paragraphs = []
        while len(cleaned) > 500:
            # find last sentence end before 500
            idx = cleaned.rfind('.', 0, 500)
            if idx == -1:
                idx = 500
            paragraphs.append(cleaned[:idx+1].strip())
            cleaned = cleaned[idx+1:].strip()
        if cleaned:
            paragraphs.append(cleaned)
        for p in paragraphs:
            lines.append(f"{speaker_label} {p}")
    return '\n\n'.join(lines)


def clean_transcript(data: Dict, verbatim: bool = False) -> str:
    """
    data may contain either:
      - 'text': a single raw transcript string
      - 'segments': a list of {speaker, start_time, text}

    Returns the polished Afaan Oromoo transcript text only.
    """
    if 'segments' in data and isinstance(data['segments'], list):
        return render_transcript_from_segments(data['segments'], verbatim=verbatim)
    text = data.get('text', '')
    cleaned = clean_segment_text(text, verbatim=verbatim)
    # if speakers are not provided, return cleaned text as-is
    return cleaned
