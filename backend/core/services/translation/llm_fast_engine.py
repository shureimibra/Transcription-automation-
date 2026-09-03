import json
import logging
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import List, Optional

from .base import TranslationEngine

logger = logging.getLogger("AIPipeline")

_MARKER_RE = re.compile(r"\[\[SEG_(\d+)\]\]")


class MarkerIntegrityError(Exception):
    """
    Raised when an LLM's response doesn't preserve every [[SEG_NNN]] marker
    exactly (wrong count, reordered, renumbered, or missing). The caller
    (run_translation_pipeline) catches this and falls back to the standard
    per-batch engine for that chunk rather than guessing at a broken
    mapping between translated text and original segments.
    """


def build_marked_prompt(texts: List[str], source_lang: Optional[str], target_lang: str) -> str:
    """
    Builds one prompt covering many segments at once, each preceded by a
    unique [[SEG_NNN]] marker the model is instructed to preserve verbatim.
    Splitting the response on these markers afterwards is what lets many
    segments be translated in a single LLM call while still mapping each
    translated line back onto its own original segment's timing/speaker.
    """
    source_desc = source_lang or "the source language (detect it automatically)"
    lines = [
        f"Translate the following text from {source_desc} to {target_lang}.",
        "The text is split into numbered segments, each preceded by a marker",
        "like [[SEG_001]]. You MUST preserve every marker EXACTLY as written,",
        "in the same order, with nothing translated, removed, merged, or",
        "renumbered. Translate only the text after each marker. Return only",
        "the translated text with its markers -- no commentary, no extra text.",
        "",
    ]
    for i, text in enumerate(texts, start=1):
        lines.append(f"[[SEG_{i:03d}]]")
        lines.append(text)
    return "\n".join(lines)


def parse_marked_response(response: str, expected_count: int) -> Optional[List[str]]:
    """
    Splits an LLM response back into expected_count ordered segment texts.
    Returns None (signaling the caller to fall back) if marker integrity
    doesn't hold: wrong count, out-of-order, duplicated, or missing markers.
    """
    if expected_count == 0:
        return []

    parts = _MARKER_RE.split(response)
    # re.split with one capturing group yields: [prefix, num1, text1, num2, text2, ...]
    numbers = parts[1::2]
    texts = parts[2::2]

    if len(numbers) < expected_count or len(texts) < expected_count:
        return None

    expected_numbers = [f"{i:03d}" for i in range(1, expected_count + 1)]
    if numbers[:expected_count] != expected_numbers:
        return None

    return [t.strip() for t in texts[:expected_count]]


class LLMProvider(ABC):
    """One HTTP call in, one raw text response out -- no translation-specific
    logic here, that lives in LLMFastTranslationEngine."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    """
    Calls Gemini's REST API directly (no SDK dependency -- same urllib-based
    style already used throughout this codebase for HTTP calls).
    Configured via GEMINI_API_KEY (required) and GEMINI_MODEL (optional,
    defaults to a fast/cheap model suited to short translation prompts).
    """

    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str = "gemini-3.6-flash"):
        self.api_key = api_key
        self.model = model

    def generate(self, prompt: str) -> str:
        url = f"{self.API_BASE}/{self.model}:generateContent"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Gemini API error: {e.code} {e.read().decode(errors='ignore')}") from e
        return data["candidates"][0]["content"]["parts"][0]["text"]


class OllamaProvider(LLMProvider):
    """
    Calls a local Ollama server's REST API. Configured via OLLAMA_HOST
    (default http://localhost:11434) and OLLAMA_MODEL (required -- whichever
    model the user has pulled locally, e.g. 'llama3').
    """

    def __init__(self, host: str, model: str):
        self.host = host.rstrip("/")
        self.model = model

    def generate(self, prompt: str) -> str:
        url = f"{self.host}/api/generate"
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama request failed (is it running at {self.host}?): {e}") from e
        return data["response"]


class LLMFastTranslationEngine(TranslationEngine):
    """
    Optional 'Fast Translation Mode': translates many segments (a whole
    transcript, or large ~1500-3000 word chunks of one) in a single LLM
    call via embedded [[SEG_NNN]] markers, collapsing what would otherwise
    be dozens of model calls into a handful. Strictly opt-in and never the
    only path -- see build_fast_engine() and its use in
    run_transcription_pipeline's translation task, which catches
    MarkerIntegrityError (or any provider failure) and falls back to the
    standard M2M100/NLLB batch-per-N-segments engine for that chunk.
    """

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def translate_batch(self, texts: List[str], source_lang: Optional[str], target_lang: str) -> List[str]:
        if not texts:
            return []
        prompt = build_marked_prompt(texts, source_lang, target_lang)
        response = self._provider.generate(prompt)
        parsed = parse_marked_response(response, expected_count=len(texts))
        if parsed is None:
            raise MarkerIntegrityError(
                f"LLM response did not preserve all {len(texts)} segment markers intact."
            )
        return parsed

    def translate(self, text: str, source_lang: Optional[str], target_lang: str) -> str:
        return self.translate_batch([text], source_lang, target_lang)[0]


def build_fast_engine() -> Optional[LLMFastTranslationEngine]:
    """
    Builds the configured Fast Translation Mode engine, or None if it isn't
    configured -- entirely opt-in via environment variables, so existing
    behavior (standard M2M100/NLLB batching) is completely unchanged unless
    a provider is explicitly set up.

      TRANSLATION_FAST_MODE_PROVIDER   'gemini' | 'ollama' -- unset disables
                                        fast mode entirely (the default).
      GEMINI_API_KEY / GEMINI_MODEL    required for the 'gemini' provider.
      OLLAMA_HOST / OLLAMA_MODEL       required for the 'ollama' provider
                                        (host defaults to localhost:11434).
    """
    provider_name = os.environ.get("TRANSLATION_FAST_MODE_PROVIDER")
    if not provider_name:
        return None

    if provider_name == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("TRANSLATION_FAST_MODE_PROVIDER=gemini but GEMINI_API_KEY is not set; fast mode disabled.")
            return None
        model = os.environ.get("GEMINI_MODEL") or os.environ.get("AI_ASSIST_MODEL") or "gemini-3.6-flash"
        return LLMFastTranslationEngine(GeminiProvider(api_key, model))

    if provider_name == "ollama":
        model = os.environ.get("OLLAMA_MODEL")
        if not model:
            logger.warning("TRANSLATION_FAST_MODE_PROVIDER=ollama but OLLAMA_MODEL is not set; fast mode disabled.")
            return None
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        return LLMFastTranslationEngine(OllamaProvider(host, model))

    logger.warning(f"Unknown TRANSLATION_FAST_MODE_PROVIDER '{provider_name}'; fast mode disabled.")
    return None
