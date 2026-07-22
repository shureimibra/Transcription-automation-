import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from functools import lru_cache

@dataclass
class LanguageProfile:
    code: str
    name: str
    allowed_chars_regex: str
    forbidden_characters: List[str] = field(default_factory=list)
    mandatory_patterns_regex: Optional[str] = None
    engine_instruction_override: str = ""

# Universal Registry of Language Profiles covering global & indigenous languages
LANGUAGE_PROFILES: Dict[str, LanguageProfile] = {
    # 1. Kikuyu (Gĩkũyũ)
    'ki': LanguageProfile(
        code='ki',
        name='Kikuyu (Gĩkũyũ)',
        allowed_chars_regex=r"^[a-zA-ZĩĨũŨ\s\.,\?!'-]+$",
        forbidden_characters=['f', 'p', 'q', 's', 'v', 'x', 'z', 'F', 'P', 'Q', 'S', 'V', 'X', 'Z'],
        mandatory_patterns_regex=r"(mb|nd|ng|nj|ny|th|a|e|i|ĩ|o|u|ũ)",
        engine_instruction_override=(
            "Nĩ wega mũno, ũhoro waku? Mbaka, ndeto, ng'ombe, njĩra, nyũmba, thĩna, mũndũ, gũkene."
        )
    ),
    # 2. Oromo (Afaan Oromoo)
    'om': LanguageProfile(
        code='om',
        name='Oromo (Afaan Oromoo)',
        allowed_chars_regex=r"^[a-zA-Z'\s\.,\?!-]+$",
        forbidden_characters=[],
        mandatory_patterns_regex=r"(ch|dh|ph|sh|ny|aa|ee|ii|oo|uu)",
        engine_instruction_override=(
            "Akkam, fayyaa? Eeyyee, galatoomi. Challa, dhugaa, phaphas, shani, nyata, xiyyaara, qabeenya, cabsuu, barreeffama."
        )
    ),
    # 3. Somali (Soomaali)
    'so': LanguageProfile(
        code='so',
        name='Somali (Soomaali)',
        allowed_chars_regex=r"^[a-zA-Z'\s\.,\?!-]+$",
        forbidden_characters=['p', 'v', 'z', 'P', 'V', 'Z'],
        mandatory_patterns_regex=r"(aa|ee|ii|oo|uu|bb|dd|gg|ll|mm|nn|rr|ss)",
        engine_instruction_override=(
            "Qoraalka iyo hadalka Af-Soomaaliga: Kaaga mahadcelinayaa, waa hagaag, soo dhowow. "
            "Wadanka Soomaaliya, magaalada Muqdisho, wararka maanta, maxaa cusub?"
        )
    ),
    # 4. Cyrillic / Russian
    'ru': LanguageProfile(
        code='ru',
        name='Russian (Русский)',
        allowed_chars_regex=r"^[\u0400-\u04FF\s\.,\?!'-]+$",
        forbidden_characters=list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        mandatory_patterns_regex=r"^[\u0400-\u04FF\s\.,\?!'-]+$",
        engine_instruction_override=(
            "Translate/Transcribe this audio strictly into Russian Cyrillic script (А-Я, а-я). "
            "Zero tolerance for Latin character bleed into Cyrillic words."
        )
    ),
    # 5. Arabic Script (Arabic, Persian, Urdu)
    'ar': LanguageProfile(
        code='ar',
        name='Arabic (العربية)',
        allowed_chars_regex=r"^[\u0600-\u06FF\s\.,\?!'-]+$",
        forbidden_characters=list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        mandatory_patterns_regex=r"^[\u0600-\u06FF\s\.,\?!'-]+$",
        engine_instruction_override=(
            "Translate/Transcribe this audio strictly into Arabic script. "
            "Must flow right-to-left (RTL). Do NOT mix Latin characters into pure Arabic script words."
        )
    ),
    # 6. Chinese (Mandarin/Cantonese)
    'zh': LanguageProfile(
        code='zh',
        name='Chinese (中文)',
        allowed_chars_regex=r"^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\s]+$",
        forbidden_characters=list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        mandatory_patterns_regex=r"[\u4e00-\u9fff]",
        engine_instruction_override=(
            "Translate/Transcribe this audio strictly into Chinese Hanzi logographic characters. "
            "Do NOT use individual alphabetic letters or spaces between characters."
        )
    ),
    # 7. German
    'de': LanguageProfile(
        code='de',
        name='German (Deutsch)',
        allowed_chars_regex=r"^[a-zA-ZäöüÄÖÜß\s\.,\?!'-]+$",
        forbidden_characters=[],
        mandatory_patterns_regex=r"[äöüÄÖÜßA-Z]",
        engine_instruction_override=(
            "Translate/Transcribe this audio strictly into German. "
            "Capitalize all nouns. Use umlauts (ä, ö, ü) and Eszett (ß)."
        )
    ),
    # 8. French
    'fr': LanguageProfile(
        code='fr',
        name='French (Français)',
        allowed_chars_regex=r"^[a-zA-ZàâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ'\s\.,\?!-]+$",
        forbidden_characters=[],
        mandatory_patterns_regex=r"[éèàùçàâäéèêëîïôöùûüç']",
        engine_instruction_override=(
            "Translate/Transcribe this audio strictly into French. "
            "Enforce proper accents (é, è, à, ù, ç) and elisions (l', d', j')."
        )
    ),
    # 9. Turkish
    'tr': LanguageProfile(
        code='tr',
        name='Turkish (Türkçe)',
        allowed_chars_regex=r"^[a-zA-ZçğıöşüÇĞİÖŞÜ\s\.,\?!'-]+$",
        forbidden_characters=['q', 'w', 'x', 'Q', 'W', 'X'],
        mandatory_patterns_regex=r"[çğıöşüÇĞİÖŞÜ]",
        engine_instruction_override=(
            "Translate/Transcribe this audio strictly into Turkish. "
            "Forbidden letters in native words: q, w, x. Preserve valid agglutinative compound words."
        )
    ),
    # 10. Spanish
    'es': LanguageProfile(
        code='es',
        name='Spanish (Español)',
        allowed_chars_regex=r"^[a-zA-ZáéíóúñÁÉÍÓÚÑüÜ\s\.,\?!¡¿'-]+$",
        forbidden_characters=[],
        mandatory_patterns_regex=r"[áéíóúñÁÉÍÓÚÑ]",
        engine_instruction_override=(
            "Translate/Transcribe this audio strictly into Spanish. "
            "Use proper accents (á, é, í, ó, ú, ñ) and inverted punctuation (¡, ¿)."
        )
    ),
    # 11. Amharic
    'am': LanguageProfile(
        code='am',
        name='Amharic (አማርኛ)',
        allowed_chars_regex=r"^[\u1200-\u137F\s\.,\?!'-]+$",
        forbidden_characters=list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        mandatory_patterns_regex=r"^[\u1200-\u137F\s\.,\?!'-]+$",
        engine_instruction_override=(
            "Translate/Transcribe this audio strictly into Amharic Ge'ez script (አማርኛ). "
            "Zero tolerance for Latin alphabet bleed."
        )
    ),
    # 12. English
    'en': LanguageProfile(
        code='en',
        name='English',
        allowed_chars_regex=r"^[a-zA-Z\s\.,\?!'-]+$",
        forbidden_characters=[],
        mandatory_patterns_regex=r"^[a-zA-Z\s\.,\?!'-]+$",
        engine_instruction_override="Translate/Transcribe this audio strictly into English."
    )
}

COMMON_ENGLISH_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for", "not", "on", "with", "he", "as", "you", 
    "do", "at", "this", "but", "his", "by", "from", "they", "we", "say", "her", "she", "or", "an", "will", "my", "one", 
    "all", "would", "there", "their", "what", "so", "up", "out", "if", "about", "who", "get", "which", "go", "me", "when", 
    "make", "can", "like", "time", "no", "just", "him", "know", "take", "people", "into", "year", "your", "good", "some", 
    "could", "them", "see", "other", "than", "then", "now", "look", "only", "come", "its", "over", "think", "also", "back", 
    "after", "use", "two", "how", "our", "work", "first", "well", "way", "even", "new", "want", "because", "any", "these", 
    "give", "day", "most", "us", "important", "feel", "free", "battle", "battles", "video", "audio", "url", "link", 
    "download", "transcribe", "system", "file", "upload", "header", "cookies", "api", "project", "code", "switching",
    "technical", "terms", "phone", "computer", "web", "internet", "program", "developer", "user", "job", "status",
    "segment", "validation", "framework", "registry", "regex", "profile", "loop", "test", "verify", "walkthrough",
    "artifact", "checkpoint", "repetition", "penalty", "accuracy", "model", "engine", "daphne", "diaspora", "government",
    "school", "university", "class", "business", "company", "office", "meeting", "management", "service", "support",
    "process", "development", "design", "team", "member", "group", "social", "media", "tiktok", "youtube", "facebook",
    "twitter", "instagram", "whatsapp", "telegram", "message", "chat", "call", "voice", "record", "microphone", "speaker",
    "sound", "music", "noise", "silence", "room", "hiss", "static", "device", "application", "software"
}

def is_valid_english_word(word: str) -> bool:
    clean_word = re.sub(r"[^\w']", "", word).lower()
    if clean_word in COMMON_ENGLISH_WORDS:
        return True
    if re.match(r"^[a-z']+$", clean_word):
        if any(v in clean_word for v in 'aeiouy'):
            return True
    return False

# 1. Profile Caching Layer for high-throughput validation
@lru_cache(maxsize=256)
def get_language_profile(lang_code: str, enable_code_switching: bool = False) -> LanguageProfile:
    """
    Returns the LanguageProfile for the given language code slug with LRU in-memory caching.
    Falls back to a dynamic generic profile if the language is not explicitly defined in the registry.
    """
    code = lang_code.strip().lower() if lang_code else 'en'
    
    if code in LANGUAGE_PROFILES:
        base_profile = LANGUAGE_PROFILES[code]
    else:
        name_title = code.upper()
        base_profile = LanguageProfile(
            code=code,
            name=f"Language ({name_title})",
            allowed_chars_regex=r"^[\w\s\.,\?!'-]+$",
            forbidden_characters=[],
            engine_instruction_override=f"Translate/Transcribe this audio strictly into {name_title}."
        )

    if enable_code_switching:
        override_prompt = (
            f"This is a high-fidelity transcript in native {base_profile.name} "
            f"that naturally mixes in English loanwords, technical terms, and phrases mid-sentence."
        )
        return LanguageProfile(
            code=base_profile.code,
            name=base_profile.name,
            allowed_chars_regex=base_profile.allowed_chars_regex,
            forbidden_characters=base_profile.forbidden_characters,
            mandatory_patterns_regex=base_profile.mandatory_patterns_regex,
            engine_instruction_override=override_prompt
        )

    return base_profile

@lru_cache(maxsize=512)
def get_compiled_regex(pattern: str) -> re.Pattern:
    """In-memory compilation cache for regular expressions."""
    return re.compile(pattern)


def sanitize_and_validate_transcript(
    raw_text: str, 
    lang_code: str, 
    enable_code_switching: bool = False
) -> Tuple[str, bool, List[str], List[Dict[str, Any]]]:
    """
    LAYER 3: GENERALIZED WORD-BY-WORD REGEX SANITIZATION ENGINE
    
    Parses the raw transcript word-by-word against the active LanguageProfile parameters.
    Sanitizes invalid characters, flags hallucinations, and generates detailed diagnostic logs
    along with a structured word correction diff map.

    Returns:
        (clean_text: str, hallucinated: bool, logs: List[str], corrections: List[Dict[str, Any]])
    """
    if not raw_text or not raw_text.strip():
        return "", False, [], []

    profile = get_language_profile(lang_code, enable_code_switching)
    forbidden_set = set(profile.forbidden_characters)
    allowed_pattern = get_compiled_regex(profile.allowed_chars_regex)
    logs: List[str] = []
    corrections: List[Dict[str, Any]] = []
    hallucinated = False

    # Common Whisper English hallucination phrases to strip in non-English transcriptions
    ENGLISH_HALLUCINATIONS = [
        r"feel free\.?", r"thank you for watching\.?", r"subtitles by.*", r"amara\.org",
        r"like and subscribe\.?", r"subscribe to my channel\.?", r"thanks for watching\.?",
        r"john battles\.?", r"we are important for my.*"
    ]

    if lang_code != 'en':
        for hall_pat in ENGLISH_HALLUCINATIONS:
            if re.search(hall_pat, raw_text, re.IGNORECASE):
                hallucinated = True
                logs.append(f"[ASR HALLUCINATION FILTER] Stripped English Whisper artifact pattern '{hall_pat}' for {profile.name}")
                raw_text = re.sub(hall_pat, "", raw_text, flags=re.IGNORECASE).strip()

    if not raw_text.strip():
        return "", hallucinated, logs, corrections

    words = raw_text.split()
    clean_words = []

    for idx, orig_word in enumerate(words):
        # Code-switching bypass check
        if enable_code_switching and is_valid_english_word(orig_word):
            clean_words.append(orig_word)
            continue

        word = orig_word
        reasons = []

        # 1. Check for forbidden character violations
        word_chars = set(word)
        intersect = word_chars.intersection(forbidden_set)
        if intersect:
            hallucinated = True
            reason_msg = f"Forbidden character(s) {sorted(list(intersect))} for {profile.name}"
            reasons.append(reason_msg)
            logs.append(f"[ORTHOGRAPHY VIOLATION] Word '{orig_word}' contains {reason_msg}")
            word = ''.join(ch for ch in word if ch not in forbidden_set)

        # 2. Check if word satisfies allowed script character regex
        if word and not allowed_pattern.match(word):
            hallucinated = True
            reason_msg = f"Characters outside allowed script for {profile.name}"
            reasons.append(reason_msg)
            logs.append(f"[SCRIPT BLEED VIOLATION] Word '{orig_word}' contains {reason_msg}")
            clean_char_list = [ch for ch in word if allowed_pattern.match(ch)]
            word = ''.join(clean_char_list)

        # 3. Structural pattern check if configured
        if profile.mandatory_patterns_regex and word:
            mandatory_pat = get_compiled_regex(profile.mandatory_patterns_regex)
            if not mandatory_pat.search(word):
                logs.append(f"[STRUCTURAL NOTICE] Word '{word}' lacks mandatory pattern for {profile.name}")

        if word != orig_word:
            corrections.append({
                "index": idx,
                "original": orig_word,
                "cleaned": word,
                "reason": " | ".join(reasons)
            })

        if word.strip():
            clean_words.append(word)

    clean_text = ' '.join(clean_words)
    clean_text = re.sub(r'\s+([,\.\?!])', r'\1', clean_text).strip()

    return clean_text, hallucinated, logs, corrections
