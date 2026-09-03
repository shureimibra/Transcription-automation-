import logging
from typing import List, Optional

from .base import TranslationEngine

logger = logging.getLogger("AIPipeline")

# Our platform codes -> NLLB's FLORES-200 "code_Script" codes. Covers every
# language in core.services.languages.PLATFORM_LANGUAGES that has a known
# FLORES-200 equivalent (both the common source languages our ASR engines
# produce, and the target languages routed to NLLB because M2M100 lacks them).
# A code missing from this map will raise a clear error rather than silently
# mistranslate -- extend this table if that happens for a language you need.
FLORES_CODE_MAP = {
    # Whisper-set source languages
    "en": "eng_Latn", "zh": "zho_Hans", "de": "deu_Latn", "es": "spa_Latn", "ru": "rus_Cyrl",
    "ko": "kor_Hang", "fr": "fra_Latn", "ja": "jpn_Jpan", "pt": "por_Latn", "tr": "tur_Latn",
    "pl": "pol_Latn", "ca": "cat_Latn", "nl": "nld_Latn", "ar": "arb_Arab", "sv": "swe_Latn",
    "it": "ita_Latn", "id": "ind_Latn", "hi": "hin_Deva", "fi": "fin_Latn", "vi": "vie_Latn",
    "he": "heb_Hebr", "uk": "ukr_Cyrl", "el": "ell_Grek", "ms": "zsm_Latn", "cs": "ces_Latn",
    "ro": "ron_Latn", "da": "dan_Latn", "hu": "hun_Latn", "ta": "tam_Taml", "no": "nob_Latn",
    "th": "tha_Thai", "ur": "urd_Arab", "hr": "hrv_Latn", "bg": "bul_Cyrl", "lt": "lit_Latn",
    "mi": "mri_Latn", "ml": "mal_Mlym", "cy": "cym_Latn", "sk": "slk_Latn", "te": "tel_Telu",
    "fa": "pes_Arab", "lv": "lvs_Latn", "bn": "ben_Beng", "sr": "srp_Cyrl", "az": "azj_Latn",
    "sl": "slv_Latn", "kn": "kan_Knda", "et": "est_Latn", "mk": "mkd_Cyrl", "eu": "eus_Latn",
    "is": "isl_Latn", "hy": "hye_Armn", "ne": "npi_Deva", "mn": "khk_Cyrl", "bs": "bos_Latn",
    "kk": "kaz_Cyrl", "sq": "als_Latn", "sw": "swh_Latn", "gl": "glg_Latn", "mr": "mar_Deva",
    "pa": "pan_Guru", "si": "sin_Sinh", "km": "khm_Khmr", "sn": "sna_Latn", "yo": "yor_Latn",
    "so": "som_Latn", "af": "afr_Latn", "oc": "oci_Latn", "ka": "kat_Geor", "be": "bel_Cyrl",
    "tg": "tgk_Cyrl", "sd": "snd_Arab", "gu": "guj_Gujr", "am": "amh_Ethi", "lo": "lao_Laoo",
    "uz": "uzn_Latn", "fo": "fao_Latn", "ht": "hat_Latn", "ps": "pbt_Arab", "tk": "tuk_Latn",
    "nn": "nno_Latn", "mt": "mlt_Latn", "sa": "san_Deva", "lb": "ltz_Latn", "my": "mya_Mymr",
    "tl": "tgl_Latn", "mg": "plt_Latn", "as": "asm_Beng", "tt": "tat_Cyrl", "ln": "lin_Latn",
    "ha": "hau_Latn", "ba": "bak_Cyrl", "jw": "jav_Latn", "su": "sun_Latn", "yue": "yue_Hant",
    # MMS-set / gap languages routed here as translation targets
    "kik": "kik_Latn", "nus": "nus_Latn", "ful": "fuv_Latn",
    "ak": "twi_Latn", "bm": "bam_Latn", "ee": "ewe_Latn", "fon": "fon_Latn",
    "ig": "ibo_Latn", "lg": "lug_Latn", "luo": "luo_Latn", "mos": "mos_Latn",
    "nso": "nso_Latn", "ny": "nya_Latn", "om": "gaz_Latn", "rw": "kin_Latn",
    "sg": "sag_Latn", "ti": "tir_Ethi", "ts": "tso_Latn", "umb": "umb_Latn",
    "wo": "wol_Latn", "xh": "xho_Latn", "zu": "zul_Latn", "tn": "tsn_Latn",
}


class NLLBEngine(TranslationEngine):
    """
    Meta NLLB-200 (facebook/nllb-200-distilled-600M): CC-BY-NC-4.0
    (NON-COMMERCIAL). Used only as a fallback for target languages M2M100
    doesn't cover -- primarily African languages M2M100 lacks entirely
    (Oromo, Kikuyu, Nuer, Tigrinya, Kinyarwanda, Chichewa, ...). This is the
    same explicit, flagged licensing tradeoff already accepted for MMS in
    the transcription engine layer -- do not route additional languages
    here without revisiting the license question.
    """

    MODEL_ID = "facebook/nllb-200-distilled-600M"
    _model = None
    _tokenizer = None

    @classmethod
    def _load(cls):
        if cls._model is None:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            logger.info(f"Loading translation engine '{cls.MODEL_ID}' (first use; non-commercial license)...")
            cls._tokenizer = AutoTokenizer.from_pretrained(cls.MODEL_ID)
            cls._model = AutoModelForSeq2SeqLM.from_pretrained(cls.MODEL_ID)
            cls._model.eval()
            logger.info("NLLB translation engine loaded.")
        return cls._model, cls._tokenizer

    @staticmethod
    def _flores_code(code: str) -> str:
        if code not in FLORES_CODE_MAP:
            raise ValueError(f"No FLORES-200 code mapping for language '{code}'. Add one to FLORES_CODE_MAP.")
        return FLORES_CODE_MAP[code]

    def translate(self, text: str, source_lang: Optional[str], target_lang: str) -> str:
        if not text or not text.strip():
            return text

        import torch
        model, tokenizer = self._load()
        tokenizer.src_lang = self._flores_code(source_lang) if source_lang else "eng_Latn"
        encoded = tokenizer(text, return_tensors="pt")
        target_code = self._flores_code(target_lang)
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                forced_bos_token_id=tokenizer.convert_tokens_to_ids(target_code),
            )
        return tokenizer.batch_decode(generated, skip_special_tokens=True)[0]

    def translate_batch(self, texts: List[str], source_lang: Optional[str], target_lang: str) -> List[str]:
        indices = [i for i, t in enumerate(texts) if t and t.strip()]
        if not indices:
            return list(texts)

        import torch
        model, tokenizer = self._load()
        tokenizer.src_lang = self._flores_code(source_lang) if source_lang else "eng_Latn"
        batch = [texts[i] for i in indices]
        encoded = tokenizer(batch, return_tensors="pt", padding=True)
        target_code = self._flores_code(target_lang)
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                forced_bos_token_id=tokenizer.convert_tokens_to_ids(target_code),
            )
        decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)

        results = list(texts)
        for idx, translated in zip(indices, decoded):
            results[idx] = translated
        return results
