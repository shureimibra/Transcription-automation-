"""
End-to-end pipeline benchmark for one Eagle Web task.

    ..\.venv\Scripts\python.exe scripts\benchmark_task.py 4244
    ..\.venv\Scripts\python.exe scripts\benchmark_task.py 4244 --no-quality

Runs the real pipeline -- download, extract, ASR, diarization, Eagle
paragraph merging, Gemini translation, and (by default) the quality gate --
timing each stage, and writes the Eagle Web translation submission.

PARAMETERIZED BY TASK ID, deliberately. The scripts this replaces were
copy-pasted per task (run_benchmark_4130.py, test_37208.py, ...), which is
how eight of them each ended up carrying their own pasted session cookie.
One script, one credential source (EAGLE_COOKIE via eagle_credentials), no
literals.

Caches the downloaded media and the ASR result next to the repo so a re-run
measures only what changed; pass --fresh to force everything again. Cached
stages are reported as cached rather than being passed off as a measurement,
because a benchmark that silently reports a cache hit as a 0.2s ASR run is
worse than no benchmark.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transcription_proj.settings')

import django  # noqa: E402
django.setup()

from scripts.eagle_credentials import eagle_cookie, DEFAULT_USER_AGENT  # noqa: E402

HOST = 'https://translation.eaglewebcommerce.com'


class Stage:
    """One timed stage. `cached` is reported, never hidden."""

    def __init__(self, label):
        self.label = label
        self.seconds = 0.0
        self.cached = False
        self.note = ''

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.seconds = time.monotonic() - self._start
        return False

    def render(self, index):
        value = f'{self.seconds:.2f} seconds'
        if self.cached:
            value += '   (cached -- not measured this run)'
        if self.note:
            value += f'   [{self.note}]'
        return f'{index}. {self.label:<28} {value}'


def fetch_task_title(task_id, cookie):
    """Best-effort page title. A failure here must not fail the benchmark."""
    try:
        from curl_cffi import requests
        response = requests.get(
            f'{HOST}/tasks/{task_id}',
            headers={'Cookie': cookie, 'User-Agent': DEFAULT_USER_AGENT},
            impersonate='chrome120', timeout=60,
        )
        import re
        match = re.search(r'<title>(.*?)</title>', response.text, re.I | re.S)
        if match:
            return re.sub(r'\s+', ' ', match.group(1)).strip()
    except Exception as exc:
        print(f'  (title lookup failed: {exc})')
    return f'Eagle Web Task {task_id}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task_id')
    parser.add_argument('--target', default='French')
    parser.add_argument('--target-code', default='fr')
    parser.add_argument('--source-code', default='en')
    parser.add_argument('--translator', default='Shureimi Ibrahim')
    parser.add_argument('--profile', default='eagle_web')
    parser.add_argument('--no-quality', action='store_true')
    parser.add_argument('--fresh', action='store_true')
    args = parser.parse_args()

    cookie = eagle_cookie()
    task_id = args.task_id
    mp3 = BACKEND / f'task_{task_id}.mp3'
    wav = BACKEND / f'task_{task_id}.wav'
    asr_cache = BACKEND / f'diarized_{task_id}_cache.json'

    stages = []
    network = Stage('Network / task metadata')
    download = Stage('Audio download')
    extract = Stage('Audio extraction (ffmpeg)')
    asr = Stage('ASR + Diarization')
    merge = Stage('Eagle segment merging')
    translate = Stage('Gemini translation')
    quality = Stage('Quality gate (audit + repair)')

    print(f'=== Eagle Web task {task_id} ===')

    # ---------------------------------------------------------- metadata
    with network:
        title = fetch_task_title(task_id, cookie)
    print(f'  title: {title}')
    stages.append(network)

    # ---------------------------------------------------------- download
    with download:
        if mp3.exists() and mp3.stat().st_size > 100_000 and not args.fresh:
            download.cached = True
            download.note = f'{mp3.stat().st_size / 1e6:.1f} MB on disk'
        else:
            from curl_cffi import requests
            response = requests.get(
                f'{HOST}/api/user/tasks/{task_id}/source',
                headers={
                    'Cookie': cookie,
                    'User-Agent': DEFAULT_USER_AGENT,
                    'Referer': f'{HOST}/tasks/{task_id}',
                    'Accept': '*/*',
                },
                impersonate='chrome120', timeout=600,
            )
            response.raise_for_status()
            body = response.content
            if len(body) < 100_000:
                raise SystemExit(
                    f'Source download returned only {len(body)} bytes -- the '
                    f'session has probably expired. Refresh EAGLE_COOKIE.'
                )
            mp3.write_bytes(body)
            download.note = f'{len(body) / 1e6:.1f} MB'
    print('  ' + download.render(0)[3:])
    stages.append(download)

    # ----------------------------------------------------------- extract
    with extract:
        if wav.exists() and wav.stat().st_size > 100_000 and not args.fresh:
            extract.cached = True
        else:
            subprocess.run(
                ['ffmpeg', '-y', '-i', str(mp3), '-ar', '16000', '-ac', '1',
                 '-c:a', 'pcm_s16le', str(wav)],
                check=True, capture_output=True,
            )
    stages.append(extract)

    # --------------------------------------------------------------- ASR
    with asr:
        if asr_cache.exists() and not args.fresh:
            diarized = json.loads(asr_cache.read_text(encoding='utf-8'))
            asr.cached = True
            asr.note = f'{len(diarized)} segments'
        else:
            from core.services.ai_pipeline import WhisperTranscriber
            from core.services.diarization import SpeakerDiarizer

            transcriber = WhisperTranscriber()
            raw = list(transcriber.transcribe(str(wav), language=args.source_code))
            payload = [
                {'id': i, 'start': s['start'], 'end': s['end'], 'text': s['text']}
                for i, s in enumerate(raw)
            ]
            diarized = SpeakerDiarizer.diarize_segments(str(wav), payload)
            asr_cache.write_text(
                json.dumps(diarized, ensure_ascii=False), encoding='utf-8'
            )
            asr.note = f'{len(diarized)} segments'
    stages.append(asr)

    speakers = sorted({d.get('speaker_label') or '' for d in diarized})
    print(f'  segments: {len(diarized)}  speakers: {speakers}')

    # ------------------------------------------------------------- merge
    class Seg:
        __slots__ = ('start_time', 'end_time', 'speaker_label', 'text')

        def __init__(self, d):
            self.start_time = d['start']
            self.end_time = d['end']
            self.speaker_label = d.get('speaker_label') or ''
            self.text = d['text']

    from core.services.transcript_formatting import group_segments_into_eagle_paragraphs

    with merge:
        paragraphs = group_segments_into_eagle_paragraphs([Seg(d) for d in diarized])
    merge.note = f'{len(diarized)} -> {len(paragraphs)} paragraphs'
    stages.append(merge)

    # --------------------------------------------------------- translate
    from core.services.ai_assist import AIPostProcessor, TokenUsage

    processor = AIPostProcessor()
    usage = TokenUsage()
    translated = []
    degraded = False

    with translate:
        for batch in processor.iter_batches(
            paragraphs, operation='translate',
            target_language=args.target, source_language=args.source_code,
            use_cache=False,
        ):
            for result in batch.segments:
                translated.append(result.processed_text)
            usage = usage + batch.usage
            degraded = degraded or batch.degraded
    translate.note = f'{len(translated)} paragraphs'
    if degraded:
        translate.note += ', DEGRADED'
    stages.append(translate)

    # ------------------------------------------------------- quality gate
    verdict = None
    if not args.no_quality:
        from core.services.ai_assist.quality import build_engine

        engine = build_engine(args.profile, provider=processor.provider)
        pairs = [
            {'source': p.text, 'target': t}
            for p, t in zip(paragraphs, translated)
        ]
        with quality:
            verdict = engine.run(
                pairs,
                target_language=args.target,
                target_language_code=args.target_code,
                source_language=args.source_code,
                on_stage=lambda stage, payload: print(
                    f'    [{stage}] {payload.get("message", "")}'
                ),
            )
        translated = verdict.texts
        usage = usage + verdict.usage
        quality.note = f'{verdict.status.value}, {verdict.iterations} repair pass(es)'
        stages.append(quality)

    # ------------------------------------------------------------ output
    from core.services.export_formats import to_eagle_translation

    body_segments = [
        Seg({'start': p.start_time, 'end': p.end_time, 'text': text,
             'speaker_label': ''})
        for p, text in zip(paragraphs, translated)
    ]
    document = to_eagle_translation(
        body_segments, title=title,
        source_lang='English', target_lang=args.target,
        metadata={
            'title': title, 'client': 'Eagle Web',
            'translator': args.translator,
            'date': time.strftime('%d/%m/%Y'),
            'source_language': 'English', 'target_language': args.target,
        },
    ).decode('utf-8')

    total = sum(s.seconds for s in stages if not s.cached)
    report_lines = [
        '=' * 50,
        'PIPELINE BENCHMARK & EXECUTION TELEMETRY',
        '=' * 50,
    ]
    for index, stage in enumerate(stages, start=1):
        report_lines.append(stage.render(index))
    report_lines += [
        '-' * 50,
        f'TOTAL PIPELINE LATENCY:         {total:.2f} seconds (measured stages only)',
        f'TOKEN USAGE:                    Input: {usage.prompt_tokens:,} | '
        f'Output: {usage.completion_tokens:,} | Total: {usage.total_tokens:,}',
    ]
    if verdict is not None:
        counts = verdict.counts()
        report_lines += [
            f'QUALITY VERDICT:                {verdict.status.value.upper()}',
            f'  profile / domain              {verdict.profile_name} / '
            f'{verdict.domain_profile.domain if verdict.domain_profile else "n/a"}',
            f'  findings                      '
            f'{counts["critical"]} critical, {counts["high"]} high, '
            f'{counts["medium"]} medium, {counts["low"]} low',
        ]
        if verdict.reason:
            report_lines.append(f'  reason                        {verdict.reason}')
    report_lines += ['=' * 50, '', 'FINAL TRANSLATION OUTPUT (EAGLE WEB FORMAT):', '']

    report = '\n'.join(report_lines) + '\n' + document
    out = BACKEND / f'benchmark_{task_id}_result.txt'
    out.write_text(report, encoding='utf-8')

    if verdict is not None and verdict.issues:
        findings = BACKEND / f'benchmark_{task_id}_findings.json'
        findings.write_text(
            json.dumps(verdict.to_report(), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        print(f'  findings -> {findings.name}')

    print('\n' + report)
    print(f'\nWritten to {out}')


if __name__ == '__main__':
    main()
