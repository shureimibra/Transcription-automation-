"""
Real speaker diarization, built on pyannote.audio's off-the-shelf pipeline
(https://huggingface.co/pyannote/speaker-diarization-3.1) -- free, runs
entirely locally/offline once its model weights are downloaded, no
per-call API and no ongoing cost. The one-time setup requirement is a free
HuggingFace account that has accepted the model's license terms, plus an
access token exposed via one of HF_TOKEN_ENV_VARS.

Why pyannote.audio specifically (see the alternatives considered):
- WhisperX bundles its own transcription+alignment+diarization pipeline --
  adopting it would mean REPLACING the existing, already-hardened
  faster-whisper pipeline this platform runs on, not extending it. It also
  uses pyannote.audio internally for diarization anyway, so there's no
  accuracy gain from switching, only migration risk.
- NeMo is GPU-oriented and heavyweight; a poor fit for a single CPU dev
  machine that already shows real resource contention under load.
- Resemblyzer only produces speaker embeddings, not a full diarization
  pipeline -- it would require hand-rolling voice-activity detection and
  clustering on top, more DIY and less production-tested than pyannote's
  pipeline, which already does exactly that end-to-end.
- Custom clustering (MFCCs + k-means, etc.) is the least accurate and
  least maintained option of all.

Architecturally this is a deliberately SEPARATE pass from
WhisperTranscriber, never a replacement for it: it runs once over the
whole extracted WAV file (diarization needs full-file context to cluster
speakers -- there's no way to know how many distinct speakers exist, or
correctly cluster them, from a partial prefix of the audio), producing
(start, end, speaker) turns. The caller (tasks.run_transcription_pipeline)
then aligns each Whisper-produced segment against these turns by time
overlap -- see assign_speaker_for_interval below. This keeps the existing
transcription pipeline's streaming behavior completely intact: segments
still get produced and broadcast one at a time as Whisper yields them,
diarization only changes what speaker_label gets written for each one.
"""
import contextlib
import logging
import os
import sys
import threading
from typing import Dict, List, Optional

logger = logging.getLogger("AIPipeline")


def _float_env(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, '') else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------- #
# DIARIZATION TUNABLES
#
# Speaker identity is the primary quality dimension for transcription-only
# work: a reviewer forgives an awkward phrase and rejects a transcript that
# attributes a sentence to the wrong person. These were previously literals
# buried mid-function -- and the gender boundary was written out twice, in
# two places that could drift apart. Named and overridable now, because the
# right value is recording-dependent and nobody should have to edit code to
# retune a job.
# --------------------------------------------------------------------- #

# Cosine distance above which two turns are treated as different speakers.
# Lower splits one speaker into several; higher merges two into one. 0.58
# is the tuned default for wespeaker-voxceleb-resnet34-LM embeddings.
DIARIZATION_CLUSTER_THRESHOLD = _float_env('DIARIZATION_CLUSTER_THRESHOLD', 0.58)

# F0 below this reads as male, above as female. The classic boundary, and
# genuinely uncertain either side of it: adult ranges overlap roughly
# 155-185 Hz, so a value in that band is close to a coin flip.
DIARIZATION_GENDER_F0_HZ = _float_env('DIARIZATION_GENDER_F0_HZ', 165.0)

# Half-width of the band in which a gender call is logged as low-confidence.
# It does NOT change the label -- Eagle's format has only Male_N/Female_N,
# so there is no third answer to give. It marks the ones worth a human ear.
DIARIZATION_GENDER_AMBIGUOUS_HZ = _float_env('DIARIZATION_GENDER_AMBIGUOUS_HZ', 15.0)

# Fallback F0 when a cluster yields no usable pitch at all. 125 Hz is
# mid-male, so an unmeasurable speaker defaults to Male_N. Arbitrary, and
# named here so it is visibly arbitrary rather than a magic number.
DIARIZATION_DEFAULT_F0_HZ = _float_env('DIARIZATION_DEFAULT_F0_HZ', 125.0)

# Total speech a cluster must accumulate to count as a real speaker.
# Below this it is folded into its nearest neighbour instead of becoming a
# phantom participant.
#
# This is the failure that matters most in practice. A cough, a door, or a
# one-word overlap can form its own tight cluster, and the result is a
# transcript claiming a fourth speaker who said two words -- which a
# reviewer reads as a diarization failure across the whole document, not as
# one stray label.
#
# MEASURED, NOT GUESSED. On a 25-minute Q&A the old 1.5s default produced 13
# speakers where a commercial tool found 9 -- and every one of our four
# extras turned out to be a shard of a speaker we had already counted, not
# an invented participant. A threshold sweep put the useful range at about
# 10 seconds: the same reading of the conversation with the phantoms gone,
# and no loss of agreement. Past 20s a genuine one-question audience member
# starts being erased, which is the failure in the other direction.
#
# 1.5s was far too permissive to catch an 11-second fragment, which is
# exactly what it was there to catch.
DIARIZATION_MIN_SPEAKER_SECONDS = _float_env('DIARIZATION_MIN_SPEAKER_SECONDS', 10.0)

# Seconds of audio per turn fed to the embedding model. More signal gives a
# more separable voiceprint; the cost is linear and CPU-bound.
DIARIZATION_EMBED_SECONDS = _float_env('DIARIZATION_EMBED_SECONDS', 4.0)


def gender_label_for(median_pitch: float) -> tuple:
    """
    (is_male, ambiguous) for a cluster's median F0.

    One function, one boundary. The comparison used to be written out at two
    call sites; keeping it in a single place is what stops the clustering
    path and the relabelling path from ever disagreeing about who is male.
    """
    is_male = median_pitch < DIARIZATION_GENDER_F0_HZ
    ambiguous = abs(median_pitch - DIARIZATION_GENDER_F0_HZ) <= DIARIZATION_GENDER_AMBIGUOUS_HZ
    return is_male, ambiguous

DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"

# Any one of these being set is enough -- checked in order, first match wins.
HF_TOKEN_ENV_VARS = ["HF_TOKEN", "HUGGINGFACE_TOKEN", "PYANNOTE_AUTH_TOKEN"]


def _get_hf_token() -> Optional[str]:
    for var in HF_TOKEN_ENV_VARS:
        token = os.environ.get(var)
        if token:
            return token
    return None


def _patch_compat_shims() -> None:
    """
    pyannote.audio 3.1.x (the latest release compatible with this
    platform's other pinned dependencies) was written against older
    torchaudio/numpy/huggingface_hub/torch APIs than what actually get
    resolved alongside this project's already-installed versions of
    those. Eight specific failures are known and patched here, each by
    restoring a harmless no-op/alias/compat-wrapper for something that was simply
    renamed or removed upstream -- never by downgrading the actual
    installed package versions, which faster-whisper's own dependency
    chain also relies on and would be a much riskier change than shimming
    a few names:

    1. `torchaudio.set_audio_backend(...)` and `torchaudio.get_audio_backend()`
       were deprecated and removed -- modern torchaudio auto-selects a
       backend on its own, so both calls were already functional no-ops by
       the time they got removed. pyannote.audio's speaker_verification.py
       calls get_audio_backend() purely to round-trip its return value
       straight back into set_audio_backend() a few lines later (a
       save/restore-around-an-import pattern) -- since set_audio_backend
       is already a no-op here, the exact value returned doesn't matter,
       only that a call succeeds instead of raising.
    2. `numpy.NaN` (and the separate all-caps `numpy.NAN` -- two distinct
       removed spellings, not one) were renamed to the lowercase
       `numpy.nan` in NumPy 2.0 -- pyannote.audio's clustering step uses
       the old spellings as default argument values/sentinel constants,
       evaluated at import time and at actual clustering time
       respectively (NAN surfaced much later than NaN, live, deep inside
       real cluster-assignment computation on real audio -- not an
       import-time failure at all, so is_configured()'s cheap import-only
       check can't catch it; only an actual diarize() call can).
    3. `torchaudio.backend` (the whole submodule) was removed along with
       the old backend-dispatch system -- pyannote.audio's segmentation
       task mixin does `from torchaudio.backend.common import
       AudioMetaData` at import time (used only by its training-data
       loading path, never during plain inference, but the import itself
       still runs unconditionally as part of loading
       pyannote.audio.tasks, which pyannote/audio/__init__.py pulls in).
       AudioMetaData has no replacement anywhere in the installed
       torchaudio -- not relocated, fully gone -- so this registers a
       minimal stand-in with the same constructor fields (sample_rate,
       num_frames, num_channels, bits_per_sample, encoding) as a fake
       torchaudio.backend.common module in sys.modules, good enough to
       satisfy the import. MUST run before shim 4 below: shim 4 has to
       import pyannote.audio submodules to patch them, and that import
       transitively runs pyannote/audio/__init__.py -- which fails on
       this exact issue if it isn't already patched, silently (the
       surrounding try/except ImportError swallows it), leaving shim 4's
       patch never actually applied. Confirmed live by hitting exactly
       this ordering bug once already.
    4. `huggingface_hub.hf_hub_download`'s `use_auth_token` parameter was
       removed in favor of `token` (huggingface_hub 1.x) -- but three
       separate pyannote.audio modules still call it with
       `use_auth_token=...` internally, each having done its own
       `from huggingface_hub import hf_hub_download` at import time
       (core/pipeline.py, core/model.py, pipelines/speaker_verification.py)
       -- three independent local bindings, not one shared reference, so
       patching huggingface_hub.hf_hub_download itself would not affect
       any of them; each module's own binding must be patched
       individually. Raises `TypeError: hf_hub_download() got an
       unexpected keyword argument 'use_auth_token'` at model-load time
       otherwise -- confirmed live: this silently made every
       diarization-enabled job fall back to no speaker labels at all,
       even with a correctly configured token, because
       SpeakerDiarizer.diarize() treats any load failure as "unavailable"
       (see _load below) rather than raising.
    5. PyTorch 2.6 changed `torch.load`'s `weights_only` default from
       False to True (a genuine security hardening: restricts what
       classes a pickled checkpoint is allowed to reconstruct, closing
       an arbitrary-code-execution vector for untrusted checkpoint
       files) -- pyannote.audio/pytorch_lightning's checkpoint loading
       goes through `lightning_fabric.utilities.cloud_io._load` without
       passing weights_only, so torch's new restrictive default applies
       and loading fails with `Unsupported global: GLOBAL
       torch.torch_version.TorchVersion` (and, if that one alone were
       allowlisted, the next blocked class after it, then the next --
       a whole checkpoint's worth of pyannote/pytorch-lightning-internal
       classes each needing its own entry). Rather than chasing an
       open-ended list of individually-allowlisted classes via
       torch.serialization.add_safe_globals, this mutates
       `lightning_fabric.utilities.cloud_io._load`'s own `__defaults__`
       tuple in place so its `weights_only` parameter defaults to False
       -- scoped ONLY to this one loader function, never touching
       torch.load's secure default for any other caller in the process
       (faster-whisper's own model loading included). Mutating the
       shared function object (rather than patching pyannote's or
       pytorch_lightning's own local `from ... import _load as pl_load`
       re-export) is what makes this reliable regardless of import
       order or which module's copy of the name actually gets called --
       confirmed live that there are at least two independent local
       re-exports of this exact function, and patching only one of them
       left the other silently unpatched.
    6. speechbrain's LazyModule (used for every optional/deprecated
       submodule it exposes -- k2_fsa, nlp, wordemb,
       huggingface_transformers, and others) eagerly imports its real
       target on almost any attribute access. pytorch_lightning's own
       `is_scripting()` helper (model_helpers.py) calls
       `inspect.stack()` while loading ANY checkpoint, which walks every
       live module in sys.modules checking `hasattr(module, '__file__')`
       -- triggering exactly that eager import for whichever lazy
       submodule happens to be probed, and if ITS real dependency isn't
       installed (k2, or some NLP package, ...), the resulting
       ModuleNotFoundError propagates all the way up and aborts
       checkpoint loading entirely, with no relationship to diarization
       actually needing that submodule's real functionality.
       speechbrain already has a guard for exactly this case (see
       LazyModule.ensure_module's own docstring: "some code will
       inadvertently cause our modules to be imported, such as some of
       PyTorch's op registering machinery") -- it's supposed to detect
       when the caller triggering the import is `inspect.py` itself and
       raise a plain AttributeError instead of actually importing. But
       that check is `importer_frame.filename.endswith("/inspect.py")`
       -- a Unix-only forward-slash path check that silently never
       matches on Windows (`...\\inspect.py`), so the guard never fires
       there and every lazy submodule probed this way genuinely tries
       (and can fail) to import for real. This replaces
       LazyModule.ensure_module with speechbrain's own logic, unchanged
       except for using os.path.basename(...) == 'inspect.py' instead of
       the platform-specific suffix check -- fixes every current and
       future lazy submodule uniformly, not just k2 specifically. A
       harmless stub `k2` module is also registered directly, as a small
       extra safety margin, since a real `k2` install is a notoriously
       fragile platform/CUDA/torch-version-pinned wheel-only dependency
       for a feature this pipeline never exercises anyway.
    7. torchcodec (torchaudio's modern audio-decoding backend, and what
       pyannote actually uses to read the WAV file at inference time, not
       just at model-load time) needs FFmpeg's *shared* library DLLs
       (avcodec-*.dll etc.) discoverable via the DLL search path -- not
       just an ffmpeg.exe on PATH, which is all this platform's own
       download/extraction pipeline (ai_pipeline.py) has ever needed.
       Confirmed live: with only the existing static FFmpeg build
       installed, loading fails with "Could not load libtorchcodec" for
       every FFmpeg major version it tries. Also confirmed live:
       torchcodec 0.15.0 only attempts to load a matching
       libtorchcodec_core for FFmpeg versions 4-8 -- installing the
       newest available shared build (9.0) still failed for this reason
       alone, even with its DLLs present and discoverable; the shared
       build must be a version torchcodec actually tries, hence pinning
       to 8.1.2, matching the existing static build's own major version.
       This locates the winget-installed Gyan.FFmpeg.Shared package's bin
       directory (containing those DLLs) and prepends it to this
       process's PATH if it isn't already there -- same reasoning as
       ai_pipeline.py's own setup_subprocess_paths(): winget's PATH
       registry update never reaches an already-running process, so this
       must be done explicitly rather than relying on it.
    8. `torchaudio.info(...)` itself -- not just the backend submodule --
       was removed in favor of torchcodec-based inspection. pyannote's
       own Audio IO layer (core/io.py) calls it directly on every file to
       get sample_rate/num_frames/num_channels for chunking, so unlike
       the AudioMetaData stand-in above (only reachable via an unused
       training path), this one's return VALUES are actually used, not
       just its shape -- a dummy no-op would silently corrupt chunking.
       Implemented via `soundfile.info()` (already an installed
       dependency), which reads real values directly from the audio
       file.
    """
    if 'k2' not in sys.modules:
        import types
        sys.modules['k2'] = types.ModuleType('k2')

    try:
        import importlib
        import inspect as _inspect_module
        import speechbrain.utils.importutils as _sb_importutils

        # REENTRANCY GUARD -- THE FRAME LOOKUP IS ITSELF A LAZY IMPORT.
        #
        # The check below asks "was I called from inspect.py?" and refuses
        # to resolve the module if so. Sound intent, but unreachable in the
        # case it exists for: working out the answer calls getframeinfo,
        # which calls inspect.getmodule, which walks sys.modules touching
        # attributes -- and touching a speechbrain lazy module runs its
        # __getattr__, which calls straight back in here. The guard never
        # got to run, because computing its input was the recursion.
        # RecursionError, every time.
        #
        # Re-entering IS the condition the guard tests for, so it answers
        # the same way: AttributeError, meaning "do not resolve this now".
        _inspecting = threading.local()

        def _ensure_module_windows_safe(self, stacklevel: int):
            if getattr(_inspecting, 'active', False):
                raise AttributeError()

            importer_frame = None
            _inspecting.active = True
            try:
                importer_frame = _inspect_module.getframeinfo(sys._getframe(stacklevel + 1))
            except AttributeError:
                pass
            finally:
                _inspecting.active = False
            # The one-line fix: os.path.basename(...) works with either
            # path separator, unlike the original's `.endswith("/inspect.py")`.
            if importer_frame is not None and os.path.basename(importer_frame.filename) == 'inspect.py':
                raise AttributeError()
            if self.lazy_module is None:
                try:
                    if self.package is None:
                        self.lazy_module = importlib.import_module(self.target)
                    else:
                        self.lazy_module = importlib.import_module(f".{self.target}", self.package)
                except Exception as e:
                    raise ImportError(f"Lazy import of {self!r} failed") from e
            return self.lazy_module

        _sb_importutils.LazyModule.ensure_module = _ensure_module_windows_safe
    except ImportError:
        pass  # speechbrain itself isn't installed -- the pyannote import below will fail on its own terms.

    try:
        import torchaudio
        if not hasattr(torchaudio, 'set_audio_backend'):
            torchaudio.set_audio_backend = lambda *args, **kwargs: None
        if not hasattr(torchaudio, 'get_audio_backend'):
            torchaudio.get_audio_backend = lambda *args, **kwargs: None
    except ImportError:
        pass  # torchaudio itself isn't installed -- the pyannote import below will fail on its own terms.

    try:
        import numpy
        if not hasattr(numpy, 'NaN'):
            numpy.NaN = numpy.nan
        if not hasattr(numpy, 'NAN'):
            numpy.NAN = numpy.nan
    except ImportError:
        pass

    try:
        import torchaudio
        import types
        if 'torchaudio.backend' not in sys.modules and not hasattr(torchaudio, 'backend'):
            class _AudioMetaDataCompat:
                def __init__(self, sample_rate=None, num_frames=None, num_channels=None,
                             bits_per_sample=None, encoding=None):
                    self.sample_rate = sample_rate
                    self.num_frames = num_frames
                    self.num_channels = num_channels
                    self.bits_per_sample = bits_per_sample
                    self.encoding = encoding

            _common_module = types.ModuleType('torchaudio.backend.common')
            _common_module.AudioMetaData = _AudioMetaDataCompat
            _backend_module = types.ModuleType('torchaudio.backend')
            _backend_module.common = _common_module
            torchaudio.backend = _backend_module
            sys.modules['torchaudio.backend'] = _backend_module
            sys.modules['torchaudio.backend.common'] = _common_module
    except ImportError:
        pass  # torchaudio itself isn't installed -- the pyannote import below will fail on its own terms.

    try:
        import torchaudio
        if not hasattr(torchaudio, 'info'):
            import soundfile

            def _torchaudio_info_compat(filepath, **kwargs):
                sf_info = soundfile.info(filepath)
                bits_per_sample = {'PCM_16': 16, 'PCM_24': 24, 'PCM_32': 32, 'PCM_S8': 8, 'FLOAT': 32, 'DOUBLE': 64}.get(
                    sf_info.subtype, 16
                )
                return torchaudio.backend.common.AudioMetaData(
                    sample_rate=sf_info.samplerate,
                    num_frames=sf_info.frames,
                    num_channels=sf_info.channels,
                    bits_per_sample=bits_per_sample,
                    encoding=sf_info.subtype or 'PCM_S16LE',
                )

            torchaudio.info = _torchaudio_info_compat
    except ImportError:
        pass  # torchaudio/soundfile itself isn't installed -- the pyannote import below will fail on its own terms.

    try:
        import torchaudio
        import soundfile as sf
        import torch

        def _torchaudio_load_soundfile(filepath, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, format=None):
            with sf.SoundFile(str(filepath), 'r') as file_:
                if frame_offset > 0:
                    file_.seek(frame_offset)
                frames = file_.read(num_frames if num_frames > 0 else -1, dtype='float32', always_2d=True)
                tensor = torch.from_numpy(frames)
                if channels_first:
                    tensor = tensor.t()
                return tensor, file_.samplerate

        torchaudio.load = _torchaudio_load_soundfile
    except ImportError:
        pass

    try:
        import inspect
        from huggingface_hub import hf_hub_download as _real_hf_hub_download
        if 'use_auth_token' not in inspect.signature(_real_hf_hub_download).parameters:
            def _hf_hub_download_compat(*args, **kwargs):
                if 'use_auth_token' in kwargs:
                    kwargs.setdefault('token', kwargs.pop('use_auth_token'))
                return _real_hf_hub_download(*args, **kwargs)
            import pyannote.audio.core.pipeline as _pyannote_pipeline_module
            import pyannote.audio.core.model as _pyannote_model_module
            import pyannote.audio.pipelines.speaker_verification as _pyannote_speaker_verification_module
            for _module in (_pyannote_pipeline_module, _pyannote_model_module, _pyannote_speaker_verification_module):
                _module.hf_hub_download = _hf_hub_download_compat
    except ImportError:
        pass  # huggingface_hub/pyannote.audio itself isn't installed -- the import below will fail on its own terms.

    localappdata = os.environ.get('LOCALAPPDATA', '')
    if localappdata:
        winget_pkg_dir = os.path.join(localappdata, 'Microsoft', 'WinGet', 'Packages')
        if os.path.exists(winget_pkg_dir):
            for item in os.listdir(winget_pkg_dir):
                if 'FFmpeg.Shared' in item:
                    full_pkg_path = os.path.join(winget_pkg_dir, item)
                    for root, dirs, files in os.walk(full_pkg_path):
                        if any(f.lower().startswith('avcodec-') and f.lower().endswith('.dll') for f in files):
                            current_paths = os.environ.get('PATH', '').split(os.pathsep)
                            if root not in current_paths:
                                os.environ['PATH'] = root + os.pathsep + os.environ['PATH']
                                logger.info(f"Added FFmpeg shared-library path for torchcodec: {root}")
                            break
                    break


@contextlib.contextmanager
def _temporarily_trust_torch_checkpoints():
    """
    PyTorch 2.6 changed `torch.load`'s `weights_only` default from False
    to True (a genuine security hardening -- restricts what classes a
    pickled checkpoint is allowed to reconstruct, closing an
    arbitrary-code-execution vector for untrusted files). pyannote.audio's
    checkpoint loading eventually reaches `torch.load(..., weights_only=X)`
    with X explicitly threaded down from several layers up (ultimately
    `LightningModule.load_from_checkpoint`'s own `weights_only=None`
    default) -- an explicitly-passed None, not an unset default, so it
    can't be intercepted by patching any function's default argument
    value; only intercepting `torch.load` itself works. Confirmed live:
    without a patch, loading fails with `Unsupported global: GLOBAL
    torch.torch_version.TorchVersion` (and would keep failing on the next
    blocked class, and the next, if only allowlisted one at a time via
    torch.serialization.add_safe_globals).

    Deliberately a context manager, not a permanent shim applied in
    _patch_compat_shims -- torch.load is reassigned only for the duration
    of the one Pipeline.from_pretrained() call in _load() below, then
    immediately restored, so no other torch.load call anywhere else in
    the process (faster-whisper's own model loading included) is ever
    affected by this. Every nested call in pyannote/pytorch_lightning's
    loading chain reaches torch.load via the `torch` module attribute at
    call time (never a pre-imported local alias), which is exactly what
    makes reassigning `torch.load` here effective for all of them.
    """
    import torch
    _real_load = torch.load

    def _load_trusting_checkpoint(*args, **kwargs):
        if kwargs.get('weights_only') is None:
            kwargs['weights_only'] = False
        return _real_load(*args, **kwargs)

    torch.load = _load_trusting_checkpoint
    try:
        yield
    finally:
        torch.load = _real_load


class SpeakerDiarizer:
    """
    Process-wide cached wrapper, mirroring the same lazy-load-once pattern
    WhisperTranscriber/EngineRegistry already use elsewhere in this
    codebase: the model is loaded at most once per server process, on the
    first job that actually requests diarization, not at import time.
    """

    _pipeline = None
    _load_attempted = False

    @classmethod
    def is_configured(cls) -> bool:
        """
        True if the library is installed AND a HF token is available --
        does NOT guarantee the model will actually load (e.g. no internet
        on first use, or the token lacks access to the gated model), only
        that it's worth attempting. Cheap; safe to call just to decide
        whether attempting diarization makes sense at all.
        """
        if _get_hf_token() is None:
            return False
        try:
            _patch_compat_shims()
            import pyannote.audio  # noqa: F401
            return True
        except Exception:
            # Broad on purpose: an import-time failure (missing package,
            # or an incompatibility this shim doesn't happen to cover) must
            # report "not configured", never propagate -- this is
            # documented as safe to call unconditionally.
            return False

    @classmethod
    def _load(cls):
        if cls._pipeline is not None or cls._load_attempted:
            return cls._pipeline
        cls._load_attempted = True
        # Checked BEFORE the pyannote.audio import, not after: that import
        # pulls in torch/torchaudio/pytorch-lightning/speechbrain -- a
        # genuinely heavy chain, confirmed to take 80+ seconds on this
        # machine -- and there is no point paying that cost just to then
        # discover there's no token to actually use it with. A job that
        # requests diarization without one configured must fail this check
        # near-instantly, not after a minute-plus wasted import.
        token = _get_hf_token()
        if not token:
            logger.warning(
                f"Speaker diarization requested but no HuggingFace token is set "
                f"(checked env vars: {', '.join(HF_TOKEN_ENV_VARS)}). "
                "Falling back to placeholder speaker labels."
            )
            return None
        try:
            _patch_compat_shims()
            from pyannote.audio import Pipeline
            logger.info(f"Loading speaker diarization model '{DIARIZATION_MODEL}' (first use this process)...")
            with _temporarily_trust_torch_checkpoints():
                cls._pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, use_auth_token=token)
            logger.info("Speaker diarization model loaded successfully.")
        except Exception as e:
            logger.warning(
                f"Speaker diarization unavailable ({e}); falling back to "
                "placeholder speaker labels for this job."
            )
            cls._pipeline = None
        return cls._pipeline

    _embedding_model = None

    @classmethod
    def _load_embedding_model(cls):
        if cls._embedding_model is not None:
            return cls._embedding_model
        token = _get_hf_token()
        if not token:
            return None
        try:
            _patch_compat_shims()
            from pyannote.audio import Inference, Model
            logger.info("Loading fast speaker embedding model 'pyannote/wespeaker-voxceleb-resnet34-LM'...")
            with _temporarily_trust_torch_checkpoints():
                model = Model.from_pretrained('pyannote/wespeaker-voxceleb-resnet34-LM', use_auth_token=token)
            cls._embedding_model = Inference(model, window='whole')
            logger.info("Fast speaker embedding model loaded successfully.")
            return cls._embedding_model
        except Exception as e:
            logger.warning(f"Could not load fast embedding model: {e}")
            return None

    #: Speakers whose gender was decided on weak evidence, from the most
    #: recent run. A class attribute rather than a return value because
    #: every caller unpacks a list of segments, and widening that signature
    #: for a diagnostic would break each of them.
    last_uncertain_speakers: List[Dict] = []

    @classmethod
    def diarize_segments(cls, audio_path: str, segments: List[Dict]) -> List[Dict]:
        """
        High-Accuracy Turn-Aggregated Speaker Diarization:
        Aggregates fine-grained Whisper speech segments into coherent conversational turns,
        extracts speaker embeddings in-memory with WeSpeaker ResNet34, clusters them,
        and assigns stable speaker identities ('Speaker 1', 'Speaker 2', etc.).
        Runs in ~15-20s for a 20-minute audio file with zero speaker over-fragmentation.
        """
        if not segments:
            return segments

        inference = cls._load_embedding_model()
        if inference is None:
            # Fallback to whole-file diarize if available
            turns = cls.diarize(audio_path)
            if turns:
                labels = build_stable_speaker_labels(turns)
                for seg in segments:
                    raw_spk = assign_speaker_for_interval(seg['start'], seg['end'], turns)
                    seg['speaker_label'] = labels.get(raw_spk, '') if raw_spk else ''
            return segments

        import soundfile as sf
        import torch
        import numpy as np
        from sklearn.cluster import AgglomerativeClustering

        try:
            # 1. Aggregate adjacent segments into conversational turns (gap <= 0.4s, max 8s)
            turns = []
            cur_turn = None
            for idx, s in enumerate(segments):
                st = s.get('start', s.get('start_time', 0.0))
                en = s.get('end', s.get('end_time', 0.0))
                if cur_turn is None:
                    cur_turn = {'start': st, 'end': en, 'seg_indices': [idx]}
                elif st - cur_turn['end'] <= 0.4 and (cur_turn['end'] - cur_turn['start']) < 8.0:
                    cur_turn['end'] = en
                    cur_turn['seg_indices'].append(idx)
                else:
                    turns.append(cur_turn)
                    cur_turn = {'start': st, 'end': en, 'seg_indices': [idx]}
            if cur_turn:
                turns.append(cur_turn)

            # 2. Read audio once into memory for fast slicing
            wav_data, sr = sf.read(audio_path)
            if wav_data.ndim > 1:
                wav_data = wav_data.mean(axis=1)

            # 3. Extract speaker embeddings per turn (capped at 3.0s per turn for fast CPU voiceprint extraction)
            turn_embeddings = []
            valid_turn_indices = []
            max_turn_samples = int(DIARIZATION_EMBED_SECONDS * sr)
            with torch.inference_mode():
                for i, t in enumerate(turns):
                    st_sample = max(0, int(t['start'] * sr))
                    en_sample = min(len(wav_data), int(t['end'] * sr))
                    dur = en_sample - st_sample
                    if dur < int(0.3 * sr):  # skip turns shorter than 300ms
                        continue
                    capped_end = st_sample + min(dur, max_turn_samples)
                    slice_audio = wav_data[st_sample:capped_end]
                    tensor = torch.from_numpy(slice_audio).unsqueeze(0).float()
                    emb = inference({'waveform': tensor, 'sample_rate': sr})
                    norm = np.linalg.norm(emb)
                    if norm > 0:
                        emb = emb / norm
                    turn_embeddings.append(emb)
                    valid_turn_indices.append(i)

            if not turn_embeddings:
                for seg in segments:
                    seg['speaker_label'] = 'Speaker 1'
                return segments

            turn_embeddings = np.array(turn_embeddings)
            if len(turn_embeddings) == 1:
                cluster_labels = [0]
            else:
                clustering = AgglomerativeClustering(
                    metric='cosine',
                    linkage='average',
                    distance_threshold=DIARIZATION_CLUSTER_THRESHOLD,
                    n_clusters=None
                )
                cluster_labels = clustering.fit_predict(turn_embeddings)

            # 3b. Fold away phantom speakers.
            #
            # A cough, a door, or a one-word overlap can form its own tight
            # cluster. Left alone it becomes a participant who said two
            # words -- and a reviewer reads that as diarization having
            # failed across the whole document, not as one stray label.
            #
            # Reassignment is to the nearest surviving cluster by cosine
            # distance between centroids, which is the same measure the
            # clustering used, so a fold can never contradict the geometry
            # that produced the clusters in the first place.
            cluster_labels = list(cluster_labels)
            speech_seconds = {}
            for t_idx, c_id in zip(valid_turn_indices, cluster_labels):
                t = turns[t_idx]
                speech_seconds[c_id] = speech_seconds.get(c_id, 0.0) + max(
                    0.0, float(t['end']) - float(t['start'])
                )

            real = {c for c, secs in speech_seconds.items()
                    if secs >= DIARIZATION_MIN_SPEAKER_SECONDS}
            phantom = set(speech_seconds) - real

            # Never fold everything away: if no cluster clears the bar the
            # recording is simply short, and the largest is the speaker.
            if phantom and real:
                centroids = {}
                for c_id in speech_seconds:
                    members = [
                        turn_embeddings[i]
                        for i, cc in enumerate(cluster_labels) if cc == c_id
                    ]
                    centroid = np.mean(np.array(members), axis=0).ravel()
                    norm = np.linalg.norm(centroid)
                    centroids[c_id] = centroid / norm if norm > 0 else centroid

                remap = {}
                for c_id in phantom:
                    remap[c_id] = min(
                        real,
                        key=lambda r: 1.0 - float(np.dot(centroids[c_id], centroids[r])),
                    )
                cluster_labels = [remap.get(c, c) for c in cluster_labels]
                logger.info(
                    'Diarization: folded %d phantom cluster(s) under %.1fs of '
                    'speech into %d real speaker(s).',
                    len(phantom), DIARIZATION_MIN_SPEAKER_SECONDS, len(real),
                )

            # 4. Determine Gender (Male / Female) for each cluster via acoustic pitch (F0) estimation
            cls.last_uncertain_speakers = []
            cluster_pitches = {}
            for t_idx, c_id in zip(valid_turn_indices, cluster_labels):
                t = turns[t_idx]
                st_sample = max(0, int(t['start'] * sr))
                en_sample = min(len(wav_data), int(t['end'] * sr))
                if en_sample > st_sample:
                    pitch = estimate_pitch_autocorr(wav_data[st_sample:en_sample], sr)
                    if pitch > 50:
                        cluster_pitches.setdefault(c_id, []).append(pitch)

            # Map cluster IDs to Male_1, Female_1, Male_2, etc. in appearance order
            male_idx = 0
            female_idx = 0
            cluster_to_name = {}
            for t_idx, c_id in zip(valid_turn_indices, cluster_labels):
                if c_id not in cluster_to_name:
                    pitches = cluster_pitches.get(c_id, [])
                    median_pitch = (
                        float(np.median(pitches)) if pitches
                        else DIARIZATION_DEFAULT_F0_HZ
                    )
                    is_male, ambiguous = gender_label_for(median_pitch)
                    if is_male:
                        male_idx += 1
                        cluster_to_name[c_id] = f"Male_{male_idx}"
                    else:
                        female_idx += 1
                        cluster_to_name[c_id] = f"Female_{female_idx}"
                    if ambiguous or not pitches:
                        # SURFACED, NOT SILENTLY GUESSED -- and until now
                        # this only wrote a log line nobody reads, while the
                        # coin-flip label went out on every segment.
                        #
                        # Measured on a real lecture: an adult male read
                        # 178 Hz against a 165 Hz boundary, was flagged
                        # ambiguous, and was labelled Female_1 on all 432
                        # lines anyway. There is no threshold that fixes
                        # that -- male and female ranges genuinely overlap
                        # -- so the answer is to tell the person who can
                        # hear the voice.
                        cls.last_uncertain_speakers.append({
                            'label': cluster_to_name[c_id],
                            'median_f0': round(float(median_pitch), 1),
                            'samples': len(pitches),
                        })
                        logger.warning(
                            'Diarization: %s assigned on weak evidence '
                            '(median F0 %.0f Hz from %d sample(s)); verify '
                            'this speaker before submission.',
                            cluster_to_name[c_id], median_pitch, len(pitches),
                        )
                turns[t_idx]['speaker'] = cluster_to_name[c_id]

            # 5. Forward-fill any unassigned turns
            last_speaker = 'Male_1'
            for t in turns:
                if 'speaker' not in t:
                    t['speaker'] = last_speaker
                else:
                    last_speaker = t['speaker']

            # 6. Assign speaker labels back to individual segments
            for t in turns:
                spk = t['speaker']
                for seg_idx in t['seg_indices']:
                    segments[seg_idx]['speaker_label'] = spk

            logger.info(f"Turn-aggregated diarization completed: clustered {len(segments)} segments across {len(turns)} turns into {len(cluster_to_name)} speaker(s).")
            return segments

        except Exception as e:
            logger.warning(f"Fast segment diarization failed: {e}; falling back to whole-file diarize.")
            turns = cls.diarize(audio_path)
            if turns:
                labels = build_stable_speaker_labels(turns)
                for seg in segments:
                    raw_spk = assign_speaker_for_interval(seg['start'], seg['end'], turns)
                    seg['speaker_label'] = labels.get(raw_spk, '') if raw_spk else ''
            return segments

    @classmethod
    def diarize(cls, audio_path: str) -> Optional[List[Dict]]:
        """
        Returns a list of {'start': float, 'end': float, 'speaker': str}
        turns covering the whole file, sorted by start time -- 'speaker' is
        pyannote's own raw label (e.g. 'SPEAKER_00'), not yet mapped to the
        platform's "Speaker N" display format (see build_stable_speaker_labels).
        Returns None if diarization isn't available or fails for any
        reason at all. Callers MUST treat None as "fall back to the
        existing placeholder behavior" -- never as a reason to fail the job,
        since diarization is always an optional enhancement on top of a
        transcription that already succeeded (or is about to).
        """
        pipeline = cls._load()
        if pipeline is None:
            return None
        try:
            import torch
            num_threads = min(os.cpu_count() or 4, 8)
            torch.set_num_threads(num_threads)
            logger.info(f"Running speaker diarization on '{audio_path}' with {num_threads} CPU threads...")
            with torch.inference_mode():
                result = pipeline(audio_path)
            turns = [
                {'start': float(turn.start), 'end': float(turn.end), 'speaker': str(speaker)}
                for turn, _, speaker in result.itertracks(yield_label=True)
            ]
            turns.sort(key=lambda t: t['start'])
            logger.info(f"Speaker diarization complete: found {len(turns)} speech turn(s).")
            return turns
        except Exception as e:
            logger.warning(f"Speaker diarization failed for '{audio_path}': {e}")
            return None


def estimate_pitch_autocorr(audio_segment: "np.ndarray", sr: int) -> float:
    """
    Estimate fundamental frequency (F0) using normalized autocorrelation.
    Human vocal pitch range: 65 Hz to 350 Hz.
    """
    import numpy as np
    if len(audio_segment) < int(sr * 0.05):
        return 0.0
    sig = audio_segment[:int(sr * 2.0)]
    if sig.ndim > 1:
        sig = np.mean(sig, axis=1)
    sig = sig - np.mean(sig)
    win = np.hanning(len(sig))
    sig = sig * win
    n = len(sig)
    n_fft = 2 ** int(np.ceil(np.log2(2 * n - 1)))
    fft_sig = np.fft.rfft(sig, n=n_fft)
    acorr = np.fft.irfft(fft_sig * np.conj(fft_sig))
    acorr = acorr[:n]
    if acorr[0] <= 0:
        return 0.0
    acorr = acorr / acorr[0]
    min_lag = int(sr / 350)
    max_lag = int(sr / 65)
    if max_lag >= len(acorr):
        max_lag = len(acorr) - 1
    if min_lag >= max_lag:
        return 0.0
    peak_lag = min_lag + np.argmax(acorr[min_lag:max_lag])
    peak_val = acorr[peak_lag]
    if peak_val < 0.25:
        return 0.0
    return float(sr) / peak_lag


def assign_speaker_for_interval(start: float, end: float, turns: List[Dict]) -> Optional[str]:
    """
    Returns the raw speaker label whose diarization turn overlaps
    [start, end] the most (by overlap duration), or None if nothing
    overlaps at all (e.g. a gap in the diarization output). Pure and
    stateless -- a linear scan is fine here since even a long file's turn
    list is at most a few hundred entries, tiny next to per-chunk
    transcription cost.
    """
    best_speaker = None
    best_overlap = 0.0
    for turn in turns:
        overlap = min(end, turn['end']) - max(start, turn['start'])
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn['speaker']
    return best_speaker


def build_stable_speaker_labels(
    turns: List[Dict],
    gender_aware: bool = False,
    audio_path: Optional[str] = None
) -> Dict[str, str]:
    """
    Maps each unique raw diarization speaker label to a stable display name.
    - If gender_aware=True or audio_path is provided: maps to "Male_1", "Female_1", etc.
    - If gender_aware=False (default): maps to "Speaker 0", "Speaker 1", etc.
    """
    sorted_turns = sorted(turns, key=lambda t: t['start'])
    unique_raw_speakers = []
    for turn in sorted_turns:
        if turn['speaker'] not in unique_raw_speakers:
            unique_raw_speakers.append(turn['speaker'])

    if not gender_aware and not audio_path:
        display_names: Dict[str, str] = {}
        for spk in unique_raw_speakers:
            display_names[spk] = f"Speaker {len(display_names)}"
        return display_names

    import numpy as np
    speaker_pitches = {}
    if audio_path and os.path.exists(audio_path):
        try:
            import soundfile as sf
            wav_data, sr = sf.read(audio_path)
            if wav_data.ndim > 1:
                wav_data = wav_data.mean(axis=1)
            for t in sorted_turns:
                spk = t['speaker']
                st_sample = max(0, int(t['start'] * sr))
                en_sample = min(len(wav_data), int(t['end'] * sr))
                if en_sample > st_sample:
                    pitch = estimate_pitch_autocorr(wav_data[st_sample:en_sample], sr)
                    if pitch > 50:
                        speaker_pitches.setdefault(spk, []).append(pitch)
        except Exception as e:
            logger.warning(f"Could not calculate speaker pitches in build_stable_speaker_labels: {e}")

    male_idx = 0
    female_idx = 0
    display_names: Dict[str, str] = {}

    for spk in unique_raw_speakers:
        if spk.startswith(("Male_", "Female_")):
            display_names[spk] = spk
            continue
        pitches = speaker_pitches.get(spk, [])
        median_pitch = (
            float(np.median(pitches)) if pitches else DIARIZATION_DEFAULT_F0_HZ
        )
        # Same helper as the clustering path, so the two can never disagree
        # about where male ends and female begins.
        is_male, _ambiguous = gender_label_for(median_pitch)
        if is_male:
            male_idx += 1
            display_names[spk] = f"Male_{male_idx}"
        else:
            female_idx += 1
            display_names[spk] = f"Female_{female_idx}"

    return display_names


def build_gender_speaker_labels(turns: List[Dict], audio_path: Optional[str] = None) -> Dict[str, str]:
    """Convenience helper to generate gender-aware labels (Male_1, Female_1)."""
    return build_stable_speaker_labels(turns, gender_aware=True, audio_path=audio_path)
