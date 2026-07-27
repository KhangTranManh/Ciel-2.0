# Design reference (adapted for Ciel workbench)

Source intent: `D:\Ciel-2.0\ui\styles.md` (Mindloop monochrome landing).
**We do NOT port the Mindloop page, Tailwind, shadcn, hls.js, or Framer Motion.**
We map the **visual system** onto the existing React + plain CSS chat-first UI.

## Tokens (HSL channels → `hsl(var(--*))`)

| Token | HSL | Use |
|-------|-----|-----|
| `--background` | `0 0% 0%` | page |
| `--foreground` | `0 0% 100%` | primary text |
| `--card` | `0 0% 5%` | panels |
| `--card-foreground` | `0 0% 100%` | panel text |
| `--primary` | `0 0% 100%` | primary actions |
| `--primary-foreground` | `0 0% 0%` | on primary |
| `--secondary` | `0 0% 12%` | secondary surfaces |
| `--secondary-foreground` | `0 0% 85%` | secondary text |
| `--muted` | `0 0% 15%` | muted surfaces |
| `--muted-foreground` | `0 0% 65%` | hints |
| `--accent` | `170 15% 45%` | subtle teal accent (sparingly) |
| `--border` | `0 0% 20%` | borders |
| `--input` | `0 0% 18%` | inputs |
| `--ring` | `0 0% 40%` | focus |

Fonts: **Inter** (sans) + optional **Instrument Serif** for brand italic only.
Effects: `.liquid-glass` (blur + gradient edge) for header chips / input dock.

## Ciel layout (unchanged architecture)

Chat-first · skills drawer · cognition drawer · compact orb · bus/WS protocol.
No landing sections, no video hero, no newsletter form.
