"""
Canonical language capability registry for the platform.

This is the single source of truth for language handling: the API exposes it
so the frontend dropdown always reflects what the *platform* (not just
Whisper) can actually transcribe, and every engine is validated against it.
Adding a language here plus routing it to an engine in ENGINE_OVERRIDES is
the entire integration surface -- nothing else needs to change.

Engine strategy: Whisper (mature, MIT-licensed, huge community) is the
default for every language it covers well. Meta MMS (CTC-based, fast,
1,100+ languages) is the fallback for languages Whisper doesn't support or
was directly observed underperforming on (Swahili/Somali). MMS is
CC-BY-NC-4.0 (non-commercial) -- an explicit, accepted tradeoff for African-
language coverage, not something to extend casually.

UBC-NLP's Simba-W was evaluated and rejected as a default engine: despite
strong published benchmark claims, real-world testing in this project found
it impractically slow on CPU (a single short clip took 1+ hour, confirmed
via CPU-time deltas to be genuinely computing, not stalled) because it runs
on plain HuggingFace `transformers` rather than an optimized inference
backend, and uses slow autoregressive decoding rather than MMS's single-pass
CTC. Its code remains available as a working plugin
(core.services.engines.simba_engine.SimbaEngine) for anyone who wants it,
but nothing routes to it by default. This keeps the architecture on stable,
production-proven engines while remaining pluggable -- a stronger open-
source African-language model can be added later by updating this file
alone, exactly like Simba itself was added and then removed.
"""
from typing import Dict

# code -> display name, for every language Whisper was trained with a
# dedicated language token for (verified against faster_whisper.tokenizer._LANGUAGE_CODES).
WHISPER_LANGUAGES: Dict[str, str] = {
    "en": "English", "zh": "Chinese", "de": "German", "es": "Spanish", "ru": "Russian",
    "ko": "Korean", "fr": "French", "ja": "Japanese", "pt": "Portuguese", "tr": "Turkish",
    "pl": "Polish", "ca": "Catalan", "nl": "Dutch", "ar": "Arabic", "sv": "Swedish",
    "it": "Italian", "id": "Indonesian", "hi": "Hindi", "fi": "Finnish", "vi": "Vietnamese",
    "he": "Hebrew", "uk": "Ukrainian", "el": "Greek", "ms": "Malay", "cs": "Czech",
    "ro": "Romanian", "da": "Danish", "hu": "Hungarian", "ta": "Tamil", "no": "Norwegian",
    "th": "Thai", "ur": "Urdu", "hr": "Croatian", "bg": "Bulgarian", "lt": "Lithuanian",
    "la": "Latin", "mi": "Maori", "ml": "Malayalam", "cy": "Welsh", "sk": "Slovak",
    "te": "Telugu", "fa": "Persian", "lv": "Latvian", "bn": "Bengali", "sr": "Serbian",
    "az": "Azerbaijani", "sl": "Slovenian", "kn": "Kannada", "et": "Estonian", "mk": "Macedonian",
    "br": "Breton", "eu": "Basque", "is": "Icelandic", "hy": "Armenian", "ne": "Nepali",
    "mn": "Mongolian", "bs": "Bosnian", "kk": "Kazakh", "sq": "Albanian", "sw": "Swahili",
    "gl": "Galician", "mr": "Marathi", "pa": "Punjabi", "si": "Sinhala", "km": "Khmer",
    "sn": "Shona", "yo": "Yoruba", "so": "Somali", "af": "Afrikaans", "oc": "Occitan",
    "ka": "Georgian", "be": "Belarusian", "tg": "Tajik", "sd": "Sindhi", "gu": "Gujarati",
    "am": "Amharic", "yi": "Yiddish", "lo": "Lao", "uz": "Uzbek", "fo": "Faroese",
    "ht": "Haitian Creole", "ps": "Pashto", "tk": "Turkmen", "nn": "Nynorsk", "mt": "Maltese",
    "sa": "Sanskrit", "lb": "Luxembourgish", "my": "Myanmar", "bo": "Tibetan", "tl": "Tagalog",
    "mg": "Malagasy", "as": "Assamese", "tt": "Tatar", "haw": "Hawaiian", "ln": "Lingala",
    "ha": "Hausa", "ba": "Bashkir", "jw": "Javanese", "su": "Sundanese", "yue": "Cantonese",
}

# Languages covered only by Meta MMS (facebook/mms-1b-all, CC-BY-NC-4.0 --
# NON-COMMERCIAL) -- languages Whisper has no token for at all, verified
# against MMS's own published language list (huggingface.co/facebook/mms-1b-all).
MMS_LANGUAGES: Dict[str, str] = {
    "kik": "Kikuyu", "nus": "Nuer", "ful": "Fulfulde",
    "ak": "Twi (Akuapem)", "bm": "Bambara", "ee": "Ewe", "fon": "Fon",
    "ig": "Igbo", "lg": "Luganda", "luo": "Luo", "mos": "Mossi",
    "nso": "Northern Sotho", "ny": "Chichewa", "om": "Oromo", "rw": "Kinyarwanda",
    "sg": "Sango", "ti": "Tigrinya", "ts": "Tsonga", "umb": "Umbundu",
    "wo": "Wolof", "xh": "Xhosa", "zu": "Zulu",
}
# Requested but NOT supported by any installed engine, verified directly
# against MMS's language list (not just assumed): Kalenjin (only
# dialect-adjacent MMS codes exist, not the 'kln' macrolanguage), Dinka,
# Kongo, Luba-Katanga, Swati, Southern Sotho, Tswana, Asante Twi, Central
# Atlas Tamazight, Venda. Deliberately left out of PLATFORM_LANGUAGES rather
# than silently mapped to the wrong language or forced through Simba just to
# have an answer -- is_supported() correctly returns False for these, and
# the pipeline reports "not yet supported" instead of mistranscribing.

# Whisper-covered languages routed to MMS instead: Whisper has no token for
# most African languages, and for the two it does (Swahili/Somali) this
# project directly observed poor Whisper output (see Phase 3 testing).
WHISPER_TO_MMS_OVERRIDES = {"am", "ha", "ln", "mg", "sn", "so", "sw", "yo"}

# code -> engine name ('whisper' | 'mms'). Whisper is the implicit default
# for any code not listed here. Simba is intentionally absent from this
# table (see module docstring) but its engine implementation is kept
# available for anyone who wants to opt back into it.
ENGINE_OVERRIDES: Dict[str, str] = {code: "mms" for code in MMS_LANGUAGES}
ENGINE_OVERRIDES.update({code: "mms" for code in WHISPER_TO_MMS_OVERRIDES})

# Full platform catalog: every language the platform can transcribe, and
# which engine actually handles it. This -- not WHISPER_LANGUAGES -- is what
# the API and frontend dropdown expose: the dropdown represents platform
# capability, not Whisper's capability alone.
PLATFORM_LANGUAGES: Dict[str, Dict[str, str]] = {}
for _code, _name in WHISPER_LANGUAGES.items():
    PLATFORM_LANGUAGES[_code] = {"name": _name, "engine": ENGINE_OVERRIDES.get(_code, "whisper")}
for _code, _name in MMS_LANGUAGES.items():
    PLATFORM_LANGUAGES[_code] = {"name": _name, "engine": "mms"}


def is_supported(language_code: str) -> bool:
    """Whether the platform (any engine) can handle this language code."""
    return language_code in PLATFORM_LANGUAGES


def engine_for(language_code: str) -> str:
    """Which engine name handles this language code. Defaults to 'whisper'."""
    return PLATFORM_LANGUAGES.get(language_code, {}).get("engine", "whisper")


# --- Translation capability matrix ---------------------------------------
# Separate from the transcription matrix above: translation is an optional,
# on-demand post-processing stage (see core.services.translation), never
# part of the transcription pipeline itself. facebook/m2m100_418M (MIT) is
# the default target-language engine; Meta NLLB-200 (CC-BY-NC-4.0 --
# NON-COMMERCIAL, an explicit accepted tradeoff, same as MMS) is the
# fallback for target languages M2M100 doesn't cover. Verified directly
# against each model's own published language list, not assumed.
#
# Languages confirmed genuinely unsupported by either translation engine
# (left out of TRANSLATION_LANGUAGES rather than silently mistranslated):
# Latin ('la' -- a dead language, not in FLORES-200), Tibetan ('bo'),
# Hawaiian ('haw').
_NLLB_ONLY_CODES = {
    "mi", "te", "eu", "tg", "fo", "tk", "nn", "mt", "sa", "as", "tt", "yue",
    "kik", "nus", "ful", "ak", "bm", "ee", "fon", "luo", "mos", "nso", "ny",
    "om", "rw", "sg", "ti", "ts", "umb",
}

TRANSLATION_LANGUAGES: Dict[str, Dict[str, str]] = {}
for _code, _name in PLATFORM_LANGUAGES.items():
    if _code in ("la", "bo", "haw"):
        continue  # confirmed unsupported by either translation engine
    _engine = "nllb" if _code in _NLLB_ONLY_CODES else "m2m100"
    TRANSLATION_LANGUAGES[_code] = {"name": _name["name"], "engine": _engine}


def is_translatable(language_code: str) -> bool:
    """Whether the platform can translate INTO this language code."""
    return language_code in TRANSLATION_LANGUAGES


def translation_engine_for(language_code: str) -> str:
    """Which translation engine handles this target language. Defaults to 'm2m100'."""
    return TRANSLATION_LANGUAGES.get(language_code, {}).get("engine", "m2m100")


def validate_against_whisper() -> None:
    """Self-check: fails loudly at import time if WHISPER_LANGUAGES ever drifts
    from what faster-whisper actually supports, instead of silently breaking
    Whisper-routed transcription."""
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    engine_codes = set(_LANGUAGE_CODES)
    catalog_codes = set(WHISPER_LANGUAGES)
    if engine_codes != catalog_codes:
        missing = engine_codes - catalog_codes
        extra = catalog_codes - engine_codes
        raise RuntimeError(
            f"WHISPER_LANGUAGES is out of sync with faster-whisper. "
            f"Missing from catalog: {missing or 'none'}. Extra in catalog: {extra or 'none'}."
        )
