"""
Transcript export formatting: TXT, DOCX, SRT, VTT.

Every function here takes a plain list of segment-like objects (anything
with .start_time, .end_time, .speaker_label, .text -- both TranscriptSegment
and TranslatedSegment satisfy this), a title, and whether diarization was
enabled for the job, and returns raw bytes. This is what lets exporting a
translated version reuse the exact same formatting code as exporting the
original: only which segments are passed in differs, never the formatting
logic itself.

TXT/DOCX ("normal transcript" formats) group consecutive segments into
human-readable paragraphs via core.services.transcript_formatting -- a
paragraph is a presentation concept, not an ASR concept (see that module's
docstring). SRT/VTT deliberately do NOT group: precise per-segment timing
is the entire point of those formats, so they always render one cue per
original TranscriptSegment, unchanged from before this module gained
paragraph grouping.

diarization_enabled is the authoritative gate for whether ANY speaker label
is shown, in every format -- never whether a given row's speaker_label
happens to be non-empty. This matters for jobs transcribed before the fake
15-second-alternation placeholder was removed (see tasks.py): their rows
may still literally contain "Speaker 0"/"Speaker 1" text, which must stay
hidden for a diarization-off job exactly like a genuinely empty label
would, without needing a data migration to blank out old rows.
"""
import io
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional

try:
    from backend.core.services.transcript_formatting import (
        group_segments_into_paragraphs,
        group_segments_into_eagle_paragraphs,
    )
except ModuleNotFoundError:
    from core.services.transcript_formatting import (
        group_segments_into_paragraphs,
        group_segments_into_eagle_paragraphs,
    )


def _format_time_hms(seconds: float) -> str:
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _format_time_range(start: float, end: float) -> str:
    return f"[{_format_time_hms(start)} - {_format_time_hms(end)}]"


def apply_speaker_aliases(segments: Iterable, aliases: Optional[Dict[str, str]]) -> List[SimpleNamespace]:
    """
    Returns lightweight stand-ins for `segments` with speaker_label
    substituted through `aliases` (raw label -> display name), leaving
    start_time/end_time/text untouched while preserving speaker_id.
    """
    alias_map = aliases or {}
    return [
        SimpleNamespace(
            start_time=seg.start_time,
            end_time=seg.end_time,
            speaker_id=getattr(seg, 'speaker_id', getattr(seg, 'speaker_label', '')) or '',
            speaker_label=alias_map.get(
                getattr(seg, 'speaker_label', ''),
                getattr(seg, 'speaker_label', '')
            ) if getattr(seg, 'speaker_label', '') else '',
            text=seg.text,
        )
        for seg in segments
    ]


def _mm_ss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_txt(segments: Iterable, title: str, diarization_enabled: bool = False) -> bytes:
    paragraphs = group_segments_into_eagle_paragraphs(segments) if diarization_enabled else group_segments_into_paragraphs(segments, False)
    blocks = []
    for para in paragraphs:
        time_hdr = _format_time_range(para.start_time, para.end_time)
        spk = getattr(para, 'speaker_label', '')
        if diarization_enabled and spk:
            blocks.append(f"{time_hdr}\n{spk}:\n{para.text}")
        else:
            blocks.append(f"{time_hdr}\n{para.text}")
    return ("\n\n".join(blocks) + "\n" if blocks else "").encode("utf-8")


def to_docx(segments: Iterable, title: str, diarization_enabled: bool = False) -> bytes:
    from docx import Document
    doc = Document()
    doc.add_heading(title, level=1)
    paragraphs = group_segments_into_eagle_paragraphs(segments) if diarization_enabled else group_segments_into_paragraphs(segments, False)
    for para in paragraphs:
        p = doc.add_paragraph()
        time_hdr = _format_time_range(para.start_time, para.end_time)
        p.add_run(f"{time_hdr}\n")
        spk = getattr(para, 'speaker_label', '')
        if diarization_enabled and spk:
            r1 = p.add_run(f"{spk}:\n")
            r1.bold = True
        p.add_run(para.text)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def to_srt(segments: Iterable, title: str, diarization_enabled: bool = False) -> bytes:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(seg.start_time)} --> {_srt_timestamp(seg.end_time)}")
        text = f"{seg.speaker_label}: {seg.text}" if (diarization_enabled and getattr(seg, 'speaker_label', '')) else seg.text
        lines.append(text)
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def to_vtt(segments: Iterable, title: str, diarization_enabled: bool = False) -> bytes:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_timestamp(seg.start_time)} --> {_vtt_timestamp(seg.end_time)}")
        text = f"{seg.speaker_label}: {seg.text}" if (diarization_enabled and getattr(seg, 'speaker_label', '')) else seg.text
        lines.append(text)
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _eagle_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _format_audio_length(seconds: float) -> str:
    s = int(round(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d} Hours"
    return f"{m:02d}:{sec:02d} Minutes"


def _count_words(text: str) -> int:
    import re
    return len(re.findall(r'\S+', text))


def mark_mid_sentence_open(text: str) -> str:
    """
    Open with an ellipsis when the recording began mid-utterance.

    EAGLE CHOPS LONG RECORDINGS INTO PARTS, so part two starts wherever part
    one stopped -- half a clause in. The accepted reference on file opens

        ...has given us that doctrine.

    and the leading ellipsis is what tells a reader that the missing words
    are missing from the AUDIO, not from the transcriber's attention. Without
    it the document simply looks like it starts with a mistake.

    THE TEST IS THE FIRST LETTER. A block that begins lower-case in a format
    where every sentence is capitalised began mid-sentence by definition --
    the same signal the capitalization check reads, which is why that check
    is LOW rather than an error. Nothing else is inspected: guessing from
    meaning would be guessing.

    Left alone when the text already starts with an ellipsis, when it starts
    with a capital, or when it starts with a digit or punctuation, since none
    of those is evidence either way.
    """
    stripped = (text or '').lstrip()
    if not stripped or stripped.startswith('...') or stripped.startswith('…'):
        return text
    first = stripped[0]
    return '...' + stripped if first.isalpha() and first.islower() else text


def to_eagle_transcription(
    segments: Iterable,
    title: str,
    diarization_enabled: bool = False,
    metadata: Optional[Dict] = None,
    total_duration: Optional[float] = None
) -> bytes:
    """
    Renders Eagle Web final transcription submission format:
    Header with metadata (Title, Client, Transcriber, Date, Audio Length),
    followed by per-segment speaker labels and millisecond timestamps [HH:MM:SS.mmm --> HH:MM:SS.mmm],
    ending with [END OF RECORDING --> HH:MM:SS.mmm].
    """
    import datetime
    meta = metadata or {}
    job_title = meta.get('title') or title or "Transcription Job"
    client = meta.get('client') or "Eagle Web"
    transcriber = meta.get('transcriber') or meta.get('presenter') or meta.get('author') or "Transcriber"
    date_str = meta.get('date') or datetime.date.today().strftime('%d/%m/%Y')

    seg_list = list(segments)
    calc_duration = total_duration if (total_duration is not None and total_duration > 0) else max((float(s.end_time) for s in seg_list), default=0.0)
    audio_len_str = _format_audio_length(calc_duration)

    lines = [
        f"Transcription Job: {job_title}",
        f"Client: {client}",
        # CAPITAL B, matching every accepted submission -- and matching
        # "Translated By" a few lines down, which was already right. The
        # preflight header check looks for "Transcribed By:" and would have
        # reported our own renderer as malformed.
        f"Transcribed By: {transcriber}",
        f"Date: {date_str}",
        f"Audio Length: {audio_len_str}",
        "",
    ]

    paragraphs = group_segments_into_eagle_paragraphs(seg_list)
    for index, para in enumerate(paragraphs):
        raw_spk = getattr(para, 'speaker_label', '') or ''
        if not raw_spk:
            spk_label = "Male_1"
        elif raw_spk.lower() in ('speaker 1', 'speaker 0'):
            spk_label = "Male_1"
        elif raw_spk.lower().startswith('speaker '):
            idx_part = raw_spk.split()[-1]
            spk_label = f"Male_{idx_part}"
        else:
            spk_label = raw_spk

        start_ts = _eagle_timestamp(float(para.start_time))
        end_ts = _eagle_timestamp(float(para.end_time))
        body = para.text.strip()
        # ONLY THE FIRST BLOCK. A later block that begins lower-case is a
        # continuation of the sentence above it, which is normal and needs no
        # marker. The ellipsis means "the audio started here", and the audio
        # can only start once.
        if index == 0:
            body = mark_mid_sentence_open(body)
        lines.append(f"{spk_label}:")
        lines.append(f"[{start_ts} --> {end_ts}]")
        lines.append(body)
        lines.append("")

    lines.append(f"[END OF RECORDING --> {_eagle_timestamp(calc_duration)}]")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def to_eagle_translation(
    segments: Iterable,
    title: str,
    source_lang: str = "English",
    target_lang: str = "French",
    metadata: Optional[Dict] = None
) -> bytes:
    """
    Renders Eagle Web final translation submission format:
    Header with metadata (Title, Client, Translated By, Date, Source/Target Language),
    followed by clean translated paragraphs without timestamps, and word count.
    """
    import datetime
    meta = metadata or {}
    # NO TITLE ON A TRANSLATION HEADER. Eagle shows the task id beside the
    # document, so repeating a filename is noise -- and an empty value must
    # render as "Translation Job:" rather than "Translation Job: " with a
    # trailing space.
    job_title = meta.get('title') or title or ""
    client = meta.get('client') or "Eagle Web"
    translator = meta.get('translator') or meta.get('transcriber') or meta.get('author') or "Translator"
    date_str = meta.get('date') or datetime.date.today().strftime('%d/%m/%Y')
    src_l = meta.get('source_language') or source_lang or "English"
    tgt_l = meta.get('target_language') or target_lang or "French"

    seg_list = list(segments)
    # Group into clean paragraphs
    paragraphs = group_segments_into_paragraphs(seg_list, diarization_enabled=False)
    para_texts = [p.text.strip() for p in paragraphs if p.text.strip()]
    if not para_texts and seg_list:
        para_texts = [s.text.strip() for s in seg_list if s.text.strip()]

    combined_text = "\n\n".join(para_texts)
    # The recording may begin mid-sentence; say so the way accepted work does.
    combined_text = mark_mid_sentence_open(combined_text)
    word_count = _count_words(combined_text)

    lines = [
        (f"Translation Job: {job_title}" if job_title else "Translation Job:"),
        f"Client: {client}",
        f"Translated By: {translator}",
        f"Date: {date_str}",
        "",
        f"Source Language: {src_l}",
        f"Target Language: {tgt_l}",
        "",
        "Translation :",
        "",
        combined_text,
        "",
        f"(Number Of Words: {word_count:,})",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def to_json(segments: Iterable, title: str, diarization_enabled: bool = False) -> bytes:
    import json
    data = []
    for seg in segments:
        raw_spk = getattr(seg, 'speaker_id', getattr(seg, 'speaker_label', '')) or ''
        disp_spk = getattr(seg, 'speaker_label', '') or ''
        data.append({
            'start': round(float(seg.start_time), 3),
            'end': round(float(seg.end_time), 3),
            'speaker_id': raw_spk if diarization_enabled else '',
            'speaker': disp_spk if diarization_enabled else '',
            'text': seg.text,
        })
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def to_csv(segments: Iterable, title: str, diarization_enabled: bool = False) -> bytes:
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['start', 'end', 'speaker_id', 'speaker', 'text'])
    for seg in segments:
        raw_spk = getattr(seg, 'speaker_id', getattr(seg, 'speaker_label', '')) or ''
        disp_spk = getattr(seg, 'speaker_label', '') or ''
        writer.writerow([
            round(float(seg.start_time), 3),
            round(float(seg.end_time), 3),
            raw_spk if diarization_enabled else '',
            disp_spk if diarization_enabled else '',
            seg.text,
        ])
    return output.getvalue().encode("utf-8")


EXPORTERS = {
    "txt": (to_txt, "text/plain", "txt"),
    "docx": (to_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "srt": (to_srt, "application/x-subrip", "srt"),
    "vtt": (to_vtt, "text/vtt", "vtt"),
    "json": (to_json, "application/json", "json"),
    "csv": (to_csv, "text/csv", "csv"),
    "eagle": (to_eagle_transcription, "text/plain", "txt"),
}
