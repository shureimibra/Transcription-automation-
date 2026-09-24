import React, {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from 'react';
import { FOCUS } from '../components/ui/index.jsx';

/**
 * The review player.
 *
 * SPEED IS THE FEATURE. Reviewing 25 minutes of audio inside a one-hour
 * deadline only works above 1×, so the rates are one click away rather than
 * buried, and the current one is always visible.
 *
 * FINDING A PLACE IS THE OTHER FEATURE, and the first version was bad at it.
 * A bare 6-pixel progress bar over ten minutes gives each second about one
 * fifth of a pixel: "start again from the beginning" was a click you could
 * miss, and matching a line of text to a moment in the audio meant guessing.
 * Three things fix that, and they work together:
 *
 *   1. A RULER. Labelled ticks at a spacing chosen from the duration, so a
 *      five-minute file and a twenty-five-minute one are both readable.
 *      You can see that you are at 4:20, not merely somewhere past halfway.
 *   2. TURN MARKS. Every speaker change is a notch on the timeline, so the
 *      shape of the conversation is visible and the boundary you want is a
 *      target you can aim at rather than a place you hunt for.
 *   3. STEP BY TURN. Buttons and keys that jump to the previous or next
 *      segment boundary exactly. This is the one that actually answers
 *      "trace the exact point": you stop scrubbing and start stepping.
 *
 * DRAGGING, NOT JUST CLICKING. Pointer capture means the scrub follows the
 * cursor off the edge of the bar and keeps working, which is how every other
 * player behaves and therefore what hands expect.
 *
 * SEEKING IS EXPOSED ON A REF so clicking a transcript line jumps the audio
 * to that moment — the single affordance that makes checking a suspicious
 * turn cost seconds instead of minutes.
 *
 * KEYBOARD FIRST. A reviewer's hands are on the keyboard editing text; a
 * player that can only be driven with a mouse costs a context switch on every
 * check. These keys work from anywhere on the page that is not a text field.
 */

// 0.5 and 0.75 ADDED FOR VERIFY-AGAINST-AUDIO WORK, specifically: a name or
// term flagged as unclear was often unclear BECAUSE it's spoken quickly or
// run together with the next word, and 1x sometimes still isn't slow enough
// to make it out -- a tasker was manually fighting the player to get
// anything slower.
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

function formatTime(seconds) {
  if (!Number.isFinite(seconds)) return '0:00';
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * How far apart the ruler's labelled ticks should sit.
 *
 * Picked from the duration rather than fixed, because a fixed interval is
 * wrong at both ends: 60-second ticks on a five-minute file give you four
 * labels, and on a ninety-minute file they give you ninety.
 */
function tickInterval(duration) {
  if (duration <= 0) return 60;
  const target = duration / 8;                       // aim for ~8 labels
  const choices = [10, 15, 30, 60, 120, 300, 600, 900];
  return choices.find((c) => c >= target) || 1800;
}

const AudioPlayer = forwardRef(function AudioPlayer(
  { src, onTimeUpdate, onError, segments = [] }, ref,
) {
  const audioRef = useRef(null);
  const barRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1.5);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [failed, setFailed] = useState(false);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [hover, setHover] = useState(null);   // seconds under the cursor
  const [dragging, setDragging] = useState(false);

  // Where each turn begins. Sorted and de-duplicated so a notch is drawn
  // once even when several segments share a boundary.
  const boundaries = useMemo(() => {
    const seen = new Set();
    for (const s of segments || []) {
      const t = Number(s?.start_time);
      if (Number.isFinite(t) && t > 0) seen.add(Math.round(t * 10) / 10);
    }
    return [...seen].sort((a, b) => a - b);
  }, [segments]);

  const seek = useCallback((seconds, { play = false } = {}) => {
    const audio = audioRef.current;
    if (!audio || !Number.isFinite(seconds)) return;
    audio.currentTime = Math.max(0, Math.min(seconds, audio.duration || seconds));
    setCurrent(audio.currentTime);
    if (play) audio.play().catch(() => {});
  }, []);

  /** The boundary before / after where we are, for exact stepping. */
  const stepTurn = useCallback((direction) => {
    const audio = audioRef.current;
    if (!audio) return;
    const now = audio.currentTime;
    if (direction < 0) {
      // A small grace window so "previous" from just after a boundary goes
      // to the one before it, not back to the one you are standing on.
      const previous = [...boundaries].reverse().find((t) => t < now - 0.75);
      seek(previous ?? 0);
    } else {
      const next = boundaries.find((t) => t > now + 0.05);
      if (next !== undefined) seek(next);
    }
  }, [boundaries, seek]);

  useImperativeHandle(ref, () => ({
    seekTo(seconds) {
      // Seeking is nearly always "let me hear this bit", so play on arrival
      // rather than making it two actions.
      seek(seconds, { play: true });
    },
    togglePlay() {
      const audio = audioRef.current;
      if (!audio) return;
      if (audio.paused) audio.play().catch(() => {}); else audio.pause();
    },
    nudge(delta) {
      const audio = audioRef.current;
      if (audio) seek(audio.currentTime + delta);
    },
    restart() { seek(0, { play: false }); },
  }), [seek]);

  useEffect(() => {
    const audio = audioRef.current;
    if (audio) audio.playbackRate = speed;
  }, [speed, src]);

  useEffect(() => {
    const audio = audioRef.current;
    if (audio) { audio.volume = volume; audio.muted = muted; }
  }, [volume, muted]);

  // Global keys, but never while someone is typing.
  useEffect(() => {
    const onKeyDown = (event) => {
      const tag = (event.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || event.target.isContentEditable) return;
      const audio = audioRef.current;
      if (!audio) return;

      if (event.code === 'Space') {
        event.preventDefault();
        if (audio.paused) audio.play().catch(() => {}); else audio.pause();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        seek(audio.currentTime - 5);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        seek(audio.currentTime + 5);
      } else if (event.key === ',') {
        event.preventDefault();
        stepTurn(-1);
      } else if (event.key === '.') {
        event.preventDefault();
        stepTurn(1);
      } else if (event.key === 'Home') {
        event.preventDefault();
        seek(0);
      } else if (event.key === '[') {
        setSpeed((s) => SPEEDS[Math.max(0, SPEEDS.indexOf(s) - 1)] ?? s);
      } else if (event.key === ']') {
        setSpeed((s) => SPEEDS[Math.min(SPEEDS.length - 1, SPEEDS.indexOf(s) + 1)] ?? s);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [seek, stepTurn]);

  const progress = duration > 0 ? (current / duration) * 100 : 0;

  const timeAt = (clientX) => {
    const box = barRef.current?.getBoundingClientRect();
    if (!box || !duration) return 0;
    const ratio = Math.min(1, Math.max(0, (clientX - box.left) / box.width));
    return ratio * duration;
  };

  const onPointerDown = (event) => {
    if (!duration) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragging(true);
    seek(timeAt(event.clientX));
  };
  const onPointerMove = (event) => {
    if (!duration) return;
    const at = timeAt(event.clientX);
    setHover(at);
    if (dragging) seek(at);
  };
  const endDrag = (event) => {
    if (dragging) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      setDragging(false);
    }
  };

  const ticks = useMemo(() => {
    if (!duration) return [];
    const step = tickInterval(duration);
    const out = [];
    for (let t = 0; t <= duration; t += step) out.push(t);
    return out;
  }, [duration]);

  if (failed) {
    return (
      <div role="alert" className="rounded-xl2 border border-status-failed/40 bg-status-failed/10 px-4 py-3">
        <p className="text-xs text-ink">
          The audio for this task could not be loaded. Tell an administrator —
          do not submit without listening.
        </p>
      </div>
    );
  }

  const iconButton =
    `flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-ink-dim ` +
    `transition-colors hover:bg-overlay hover:text-ink ${FOCUS}`;

  return (
    <div className="rounded-xl2 border border-edge bg-surface p-4 shadow-panel">
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
        onTimeUpdate={(e) => {
          setCurrent(e.currentTarget.currentTime);
          onTimeUpdate?.(e.currentTarget.currentTime);
        }}
        onError={() => { setFailed(true); onError?.(); }}
      />

      {/* ---- the ruler ------------------------------------------------- */}
      <div className="relative mb-1 h-4 select-none" aria-hidden="true">
        {ticks.map((t) => (
          <span
            key={t}
            className="absolute top-0 flex h-full flex-col items-start"
            style={{ left: `${(t / duration) * 100}%` }}
          >
            <span className="h-1.5 w-px bg-edging" />
            <span className="pl-1 text-[9px] leading-none tabular-nums text-ink-mute">
              {formatTime(t)}
            </span>
          </span>
        ))}
      </div>

      {/* ---- the timeline ---------------------------------------------- */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        ref={barRef}
        role="slider"
        tabIndex={0}
        aria-label="Seek"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(current)}
        aria-valuetext={`${formatTime(current)} of ${formatTime(duration)}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          const audio = audioRef.current;
          if (!audio) return;
          if (e.key === 'ArrowLeft') { e.preventDefault(); seek(audio.currentTime - 5); }
          if (e.key === 'ArrowRight') { e.preventDefault(); seek(audio.currentTime + 5); }
          if (e.key === 'Home') { e.preventDefault(); seek(0); }
          if (e.key === 'End') { e.preventDefault(); seek(duration); }
        }}
        className={`group relative h-8 cursor-pointer touch-none rounded ${FOCUS}`}
      >
        <div className="absolute inset-x-0 top-3 h-2 overflow-hidden rounded-full bg-overlay">
          <div className="h-full rounded-full bg-accent" style={{ width: `${progress}%` }} />
        </div>

        {/* Turn marks: where each speaker change begins. */}
        {duration > 0 && boundaries.map((t) => (
          <span
            key={t}
            aria-hidden="true"
            className="absolute top-[9px] h-3.5 w-px bg-ink-mute/45"
            style={{ left: `${(t / duration) * 100}%` }}
          />
        ))}

        {/* The playhead, big enough to grab. */}
        {duration > 0 && (
          <span
            aria-hidden="true"
            className="absolute top-1.5 h-5 w-1 -translate-x-1/2 rounded-full bg-brand ring-2 ring-surface"
            style={{ left: `${progress}%` }}
          />
        )}

        {/* Where a click would land, before you commit to it. */}
        {hover !== null && duration > 0 && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute -top-0.5 -translate-x-1/2 rounded bg-ink px-1.5 py-0.5
                       text-[10px] font-medium tabular-nums text-canvas"
            style={{ left: `${(hover / duration) * 100}%` }}
          >
            {formatTime(hover)}
          </span>
        )}
      </div>

      {/* ---- transport -------------------------------------------------- */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button onClick={() => seek(0)} aria-label="Back to the start" title="Back to the start (Home)"
                className={iconButton}>
          <Icon d="M6 5v14M19 5l-9 7 9 7V5Z" />
        </button>
        <button onClick={() => stepTurn(-1)} aria-label="Previous turn" title="Previous turn (,)"
                className={iconButton}>
          <Icon d="M11 5 4 12l7 7M20 5l-7 7 7 7" />
        </button>
        <button onClick={() => { const a = audioRef.current; if (a) seek(a.currentTime - 10); }}
                aria-label="Back 10 seconds" title="Back 10 seconds (← is 5s)" className={iconButton}>
          <Icon d="M11 5 4 12l7 7M20 12H4.5" />
        </button>

        <button
          onClick={() => {
            const audio = audioRef.current;
            if (!audio) return;
            if (audio.paused) audio.play().catch(() => {}); else audio.pause();
          }}
          aria-label={playing ? 'Pause' : 'Play'}
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-brand
                      text-white shadow-sm transition-colors hover:bg-brand-strong ${FOCUS}`}
        >
          {playing ? (
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="6" y="5" width="4" height="14" rx="1" />
              <rect x="14" y="5" width="4" height="14" rx="1" />
            </svg>
          ) : (
            <svg className="h-5 w-5 translate-x-px" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M8 5v14l11-7z" />
            </svg>
          )}
        </button>

        <button onClick={() => { const a = audioRef.current; if (a) seek(a.currentTime + 10); }}
                aria-label="Forward 10 seconds" title="Forward 10 seconds (→ is 5s)" className={iconButton}>
          <Icon d="m13 5 7 7-7 7M4 12h15.5" />
        </button>
        <button onClick={() => stepTurn(1)} aria-label="Next turn" title="Next turn (.)"
                className={iconButton}>
          <Icon d="m13 5 7 7-7 7M4 5l7 7-7 7" />
        </button>

        <span className="ml-1 shrink-0 font-mono text-xs tabular-nums text-ink">
          {formatTime(current)}
          <span className="text-ink-mute"> / {formatTime(duration)}</span>
        </span>

        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => setMuted((m) => !m)}
                  aria-label={muted ? 'Unmute' : 'Mute'} aria-pressed={muted}
                  className={iconButton}>
            <Icon d={muted
              ? 'M11 5 6 9H3v6h3l5 4V5ZM16 9l5 6M21 9l-5 6'
              : 'M11 5 6 9H3v6h3l5 4V5ZM15.5 8.5a5 5 0 0 1 0 7M18.5 6a9 9 0 0 1 0 12'} />
          </button>
          <input
            type="range" min="0" max="1" step="0.05" value={muted ? 0 : volume}
            onChange={(e) => { setVolume(Number(e.target.value)); setMuted(false); }}
            aria-label="Volume"
            className={`h-1 w-20 cursor-pointer accent-accent ${FOCUS}`}
          />

          <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-edge bg-canvas p-0.5">
            {SPEEDS.map((rate) => (
              <button
                key={rate}
                onClick={() => setSpeed(rate)}
                aria-pressed={speed === rate}
                className={`rounded px-1.5 py-0.5 text-2xs font-medium tabular-nums transition-colors ${
                  speed === rate ? 'bg-accent text-white' : 'text-ink-mute hover:text-ink'
                }`}
              >
                {rate}×
              </button>
            ))}
          </div>
        </div>
      </div>

      <p className="mt-2 text-2xs text-ink-mute">
        Space play/pause · ← → 5s · <kbd>,</kbd> <kbd>.</kbd> previous/next turn ·
        Home back to start · [ ] speed · click any line to jump there
      </p>
    </div>
  );
});

function Icon({ d }) {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export default AudioPlayer;
