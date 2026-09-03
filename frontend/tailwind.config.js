/** @type {import('tailwindcss').Config} */

// Design tokens for the operational UI.
//
// EVERY COLOUR IS A CSS VARIABLE, NOT A HEX VALUE. The names below
// (`surface`, `ink`, `edge`) were already used in ~290 places across the
// worker and admin screens. Pointing them at variables instead of literals
// means the whole application re-themes by editing `index.css` alone -- no
// screen has to be visited twice, and a screen written next month is
// theme-aware without its author thinking about it.
//
// THE TRIPLETS ARE SPACE-SEPARATED RGB, and every entry is wrapped in
// `rgb(... / <alpha-value>)`, because Tailwind's opacity modifiers are load
// bearing here: `bg-status-ready/15` and `ring-accent/30` appear throughout
// the existing code and would silently stop working against a plain
// `var(--x)`.
//
// A NAMED SCALE RATHER THAN RAW TAILWIND COLOURS, because status colour is
// load-bearing: a worker scanning a queue reads colour before text, and
// "in progress" must look the same on the worker dashboard, the admin table
// and the audit timeline. Centralising it means a status cannot drift
// between two screens.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Surfaces, ground upward.
        canvas:    token('canvas'),
        surface:   token('surface'),
        raised:    token('raised'),
        overlay:   token('overlay'),
        edge:      token('edge'),
        edging:    token('edging'),

        ink:        token('ink'),
        'ink-dim':  token('ink-dim'),
        'ink-mute': token('ink-mute'),

        // THE ACCENT IS EAGLE'S INDIGO. `accent` is the interactive hue --
        // links, focus rings, the selected row. `brand` is the deeper navy
        // reserved for the one primary action on a screen, which is how
        // Eagle uses it: exactly one filled navy button per card.
        accent: {
          DEFAULT: token('accent'),
          soft:    token('accent-soft'),
          strong:  token('accent-strong'),
          ink:     token('accent-ink'),
        },
        brand: {
          DEFAULT: token('brand'),
          soft:    token('brand-soft'),
          strong:  token('brand-strong'),
        },

        // Task status. One hue per state, used everywhere that state appears.
        status: {
          new:       token('status-new'),
          assigned:  token('status-assigned'),
          progress:  token('status-progress'),
          review:    token('status-review'),
          ready:     token('status-ready'),
          submitted: token('status-submitted'),
          approved:  token('status-approved'),
          rejected:  token('status-rejected'),
          failed:    token('status-failed'),
          cancelled: token('status-cancelled'),
        },

        // Money is its own channel. Eagle prints earnings in a filled green
        // pill and nothing else on the card is that colour, so the number a
        // tasker is deciding on is the first thing the eye lands on.
        money: {
          DEFAULT: token('money'),
          soft:    token('money-soft'),
          ink:     token('money-ink'),
        },
      },
      fontFamily: {
        // Rubik: geometric with softened corners and real tabular figures,
        // which is what the platform our taskers already use is setting.
        sans: ['Rubik', 'Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      borderRadius: { xl2: '0.875rem' },
      boxShadow: {
        // Variables again: a shadow tuned for a dark ground reads as grime
        // on a light one.
        panel: 'var(--shadow-panel)',
        lift:  'var(--shadow-lift)',
      },
      backgroundImage: {
        'hero-brand': 'linear-gradient(105deg, rgb(var(--brand-strong)) 0%, rgb(var(--brand)) 55%, rgb(var(--accent)) 100%)',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: { fadeIn: 'fadeIn .3s ease-out forwards' },
    },
  },
  plugins: [],
}
