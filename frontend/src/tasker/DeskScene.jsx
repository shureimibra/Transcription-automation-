import React from 'react';

/**
 * The moving picture on the sign-in and sign-up pages: what the product does,
 * shown rather than described. Someone listening, someone typing, a
 * transcript being proofread, the audio playing, and the little moments of
 * the job -- a speaker label, a timestamp, a language pair, "copied".
 *
 * ALL DRAWN HERE. No stock photos and nothing fetched from another site:
 * the page loads as fast as before and depends on nobody. Decorative only
 * (aria-hidden); the animations live in index.css and stand still for
 * anyone who has asked their device to reduce motion.
 */
export default function DeskScene({ compact = false }) {
  if (compact) {
    return (
      <div aria-hidden="true" className="flex h-10 items-end gap-1">
        <Wave bars={18} height={40} />
      </div>
    );
  }
  return (
    <div aria-hidden="true" className="relative h-[330px] w-full max-w-[420px] select-none">
      {/* The listener, top left, with sound rising from the headphones. */}
      <div className="desk-float absolute left-0 top-2" style={{ animationDelay: '-1s' }}>
        <Listener />
      </div>

      {/* The transcript card, centre. */}
      <div className="desk-float absolute left-[92px] top-[64px] w-[250px] rounded-xl bg-[#F7F3EC] p-3.5 text-[#17151A] shadow-2xl shadow-black/40">
        <div className="flex items-center justify-between">
          <span className="rounded bg-[#C2542D] px-1.5 py-0.5 font-mono text-[10px] font-medium text-white">Female_1</span>
          <span className="font-mono text-[10px] text-[#6B6560]">00:12:04.310</span>
        </div>
        <p className="mt-2 text-[12px] leading-relaxed">
          <span className="desk-sweep rounded-sm">So the second component is the</span>{' '}
          location of that event in the S frame.
        </p>
        <div className="mt-2.5 flex items-end gap-[3px]">
          <Wave bars={22} height={22} />
        </div>
      </div>

      {/* The typist, bottom right. */}
      <div className="desk-float absolute bottom-0 right-0" style={{ animationDelay: '-3s' }}>
        <Typist />
      </div>

      {/* Moments of the job, drifting. */}
      <Chip className="left-[18px] top-[292px]" delay="-2s">EN → FR</Chip>
      <Chip className="right-[6px] top-[18px]" delay="-5s">Male_1 → Female_1</Chip>
      <Chip className="left-[150px] top-[18px]" delay="-6.5s">✓ Speakers fixed</Chip>

      {/* A toast that comes and goes. */}
      <div className="desk-pop absolute bottom-[84px] left-[56px] flex items-center gap-2 rounded-lg bg-[#2A2730] px-3 py-2 text-[11px] text-[#F7F3EC] shadow-lg shadow-black/40 ring-1 ring-white/10">
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-[#4CAF7A] text-[10px] text-white">✓</span>
        Final draft copied
      </div>
    </div>
  );
}

function Chip({ className, delay, children }) {
  return (
    <span className={`desk-drift absolute rounded-full bg-white/[0.07] px-2.5 py-1 font-mono text-[10px] text-[#F7F3EC]/80 ring-1 ring-white/15 backdrop-blur-sm ${className}`}
          style={{ animationDelay: delay }}>
      {children}
    </span>
  );
}

/** Audio bars, each on its own beat so the wave never looks mechanical. */
function Wave({ bars, height }) {
  return Array.from({ length: bars }, (_, i) => (
    <span key={i} className="desk-wave inline-block w-[3px] rounded-full bg-[#C2542D]"
          style={{
            height: `${height * (0.45 + 0.55 * Math.abs(Math.sin(i * 1.7)))}px`,
            animationDelay: `${-((i * 0.137) % 1.1)}s`,
            opacity: 0.55 + 0.45 * Math.abs(Math.cos(i)),
          }} />
  ));
}

/** A person listening on headphones, sound rings rising. */
function Listener() {
  return (
    <svg width="104" height="112" viewBox="0 0 104 112" fill="none">
      <circle cx="52" cy="56" r="50" fill="#2A2730" />
      <circle className="desk-ring" cx="82" cy="30" r="10" stroke="#E2805C" strokeWidth="2" fill="none" />
      <circle className="desk-ring" cx="82" cy="30" r="10" stroke="#E2805C" strokeWidth="2" fill="none"
              style={{ animationDelay: '-1.2s' }} />
      {/* shoulders */}
      <path d="M22 104c2-20 15-31 30-31s28 11 30 31z" fill="#C2542D" />
      {/* neck + head */}
      <rect x="46" y="62" width="12" height="12" rx="4" fill="#8D5B3E" />
      <circle cx="52" cy="48" r="17" fill="#8D5B3E" />
      {/* hair */}
      <path d="M35 46c0-12 8-19 17-19s17 7 17 18c-4-6-10-9-17-9s-13 3-17 10z" fill="#17151A" />
      {/* headphones */}
      <path d="M33 48c0-12 9-22 19-22s19 10 19 22" stroke="#F7F3EC" strokeWidth="3.5" fill="none" strokeLinecap="round" />
      <rect x="29" y="44" width="8" height="13" rx="3" fill="#F7F3EC" />
      <rect x="67" y="44" width="8" height="13" rx="3" fill="#F7F3EC" />
      {/* face */}
      <circle cx="46" cy="50" r="1.6" fill="#17151A" />
      <circle cx="58" cy="50" r="1.6" fill="#17151A" />
      <path d="M47 57c3 2.5 7 2.5 10 0" stroke="#17151A" strokeWidth="1.6" strokeLinecap="round" fill="none" />
    </svg>
  );
}

/** A person typing at a laptop. */
function Typist() {
  return (
    <svg width="132" height="112" viewBox="0 0 132 112" fill="none">
      <circle cx="70" cy="58" r="52" fill="#2A2730" />
      {/* body */}
      <path d="M38 108c2-22 15-34 32-34s30 12 32 34z" fill="#E8E2D8" />
      <rect x="64" y="62" width="12" height="13" rx="4" fill="#C68B59" />
      <circle cx="70" cy="48" r="17" fill="#C68B59" />
      {/* hair: braids tied up */}
      <path d="M53 47c0-11 7-18 17-18s17 7 17 17c-5-5-11-8-17-8s-12 3-17 9z" fill="#17151A" />
      <circle cx="70" cy="27" r="7" fill="#17151A" />
      <circle cx="64" cy="51" r="1.6" fill="#17151A" />
      <circle cx="76" cy="51" r="1.6" fill="#17151A" />
      <path d="M66 57c2.5 1.8 5.5 1.8 8 0" stroke="#17151A" strokeWidth="1.6" strokeLinecap="round" fill="none" />
      {/* laptop */}
      <path d="M30 86h62l-6 18H36z" fill="#17151A" />
      <rect x="40" y="70" width="44" height="18" rx="2" fill="#3A3640" />
      <rect x="44" y="74" width="20" height="2.5" rx="1" fill="#E2805C" />
      <rect x="44" y="79" width="30" height="2.5" rx="1" fill="#F7F3EC" opacity="0.6" />
      {/* hands, tapping */}
      <g className="desk-type"><ellipse cx="52" cy="92" rx="6" ry="4" fill="#C68B59" /></g>
      <g className="desk-type" style={{ animationDelay: '-0.25s' }}><ellipse cx="74" cy="92" rx="6" ry="4" fill="#C68B59" /></g>
    </svg>
  );
}
