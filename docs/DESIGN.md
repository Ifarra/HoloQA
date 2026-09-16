---
version: alpha
name: HoloQA Control Surface
description: Light-mode operational Web3-inspired design system for HoloQA's MCP-first SIT/UAT platform.
colors:
  primary: "#161616"
  canvas: "#E7E7E4"
  surface: "#F3F3F0"
  paper: "#FBFBF8"
  ink: "#161616"
  muted: "#777773"
  line: "#C9C9C4"
  accent: "#FF6327"
  success: "#2D8B62"
  warning: "#B67920"
  danger: "#B83B2E"
  white: "#FFFFFF"
typography:
  display:
    fontFamily: "Space Grotesk, Segoe UI, sans-serif"
    fontSize: "8.625rem"
    fontWeight: 900
    lineHeight: 0.82
    letterSpacing: "-0.085em"
  heading-lg:
    fontFamily: "Space Grotesk, Segoe UI, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.06em"
  heading-md:
    fontFamily: "Space Grotesk, Segoe UI, sans-serif"
    fontSize: "1.4375rem"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.05em"
  body:
    fontFamily: "IBM Plex Sans, Segoe UI, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "IBM Plex Sans, Segoe UI, sans-serif"
    fontSize: "0.625rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.14em"
  mono:
    fontFamily: "IBM Plex Mono, Consolas, monospace"
    fontSize: "0.6875rem"
    fontWeight: 400
    lineHeight: 1.4
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "20px"
  6: "24px"
  7: "32px"
  8: "48px"
  9: "64px"
rounded:
  none: "0px"
  sm: "2px"
  md: "4px"
  shell: "18px"
  pill: "999px"
components:
  shell:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.shell}"
    padding: "{spacing.6}"
  canvas-grid:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
  divider:
    backgroundColor: "{colors.line}"
    textColor: "{colors.ink}"
  card:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "{spacing.4}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "12px 15px"
  button-primary-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.white}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "12px 15px"
  button-secondary-hover:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.ink}"
  tab-active:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.white}"
    rounded: "{rounded.none}"
    padding: "9px 12px"
  tab-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "9px 12px"
  status-success:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "4px 8px"
  status-warning:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "4px 8px"
  status-danger:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.danger}"
    rounded: "{rounded.pill}"
    padding: "4px 8px"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "9px 12px"
---

## Overview

HoloQA uses a **light-mode operational control surface**: editorial structure, technical labels, visible alignment, and evidence-focused data density. The visual language is inspired by mature Web3 portfolio interfaces without copying a specific brand.

The primary surface archetype is **Monitor / Operate**. Users need to understand state quickly, inspect records, and move into an approved action. Do not use marketing-hero composition inside operational screens.

The system should feel:

- Precise rather than decorative.
- Technical rather than cyberpunk.
- Institutional but not corporate-generic.
- Calm at rest, decisive at the point of action.

## Colors

- **Canvas (`#E7E7E4`)** is the outer workspace and carries the low-contrast blueprint grid.
- **Surface (`#F3F3F0`)** is the application frame.
- **Paper (`#FBFBF8`)** is reserved for readable content surfaces such as tables and cards.
- **Ink (`#161616`)** is the primary text, border, and active-control color.
- **Muted (`#777773`)** is secondary metadata. Do not use it for essential content below accessible contrast requirements.
- **Line (`#C9C9C4`)** separates modules without adding shadows.
- **Accent (`#FF6327`)** is the sole high-energy accent. Use it for primary actions, active emphasis, indexes, and directional affordances.
- **Success, warning, and danger** communicate run state. Never rely on color alone; pair each state with text and, where useful, a small status dot.

Use one accent color per surface. Do not introduce gradients, neon glows, rainbow status colors, or arbitrary product-specific accents without extending this system deliberately.

## Typography

Use Space Grotesk for display and headings when available, with Segoe UI as the Windows fallback. Use IBM Plex Sans for interface copy and IBM Plex Mono for IDs, timestamps, run states, and technical values.

Rules:

- Reserve `display` for one primary page identity or control-surface title.
- Use sentence case for explanatory copy and uppercase only for labels, navigation metadata, and statuses.
- Use tight negative tracking only for large headings; body and labels must remain legible.
- Never use the display style for paragraphs, form labels, or critical instructions.
- Numeric values and identifiers should be aligned and rendered with the mono style where comparison matters.

## Layout

Use a centered application shell with a maximum width of `1400px`, `18px` outer padding, and a `3px` ink frame on desktop. The shell becomes edge-to-edge on small screens.

Use an 8px rhythm based on the spacing tokens:

- `4px` micro separation.
- `8–12px` icon, label, and control internals.
- `16–24px` component padding and table rhythm.
- `32px` section separation.
- `48–64px` hero or major composition separation.

Operational screens should use explicit grids, aligned columns, and thin dividers. Prefer CSS Grid for page composition and tables for comparable records. Avoid equal-weight feature-card grids when the user is monitoring or operating a system.

Responsive rules:

- Desktop: two-column featured modules and full navigation.
- Tablet: reduce gutters and allow controls to wrap.
- Mobile: stack featured modules, use one-column records, preserve 44px minimum hit targets, and allow horizontal overflow for genuinely tabular data.

## Elevation & Depth

Depth comes from borders, surface changes, and the outer frame—not floating shadows.

- Use `1px` line borders for internal divisions.
- Use a `2px` ink rule above major data tables.
- Use the shell's `3px` ink frame to establish the application boundary.
- Avoid card shadows except for a restrained shell offset or an explicitly elevated modal.
- Do not use glassmorphism, blur, or translucent panels as default treatments.

The blueprint grid is a background texture only. It must remain lower contrast than content borders and must never reduce text readability.

## Shapes

The outer application frame is rounded (`18px`) to feel like a contained product environment. Internal cards, tables, inputs, and tabs are square or lightly rounded (`0–4px`). This contrast is intentional.

Use pill shapes only for compact statuses or tags. Do not turn every control into a pill.

Decorative marks—crosses, plus signs, arrows, and index numbers—are allowed as sparse navigation cues. They must not replace labels or accessible names.

## Components

### Navigation

Use a shallow top bar with a brand anchor, parent/back navigation, section links, and one emphasized action. Navigation links are small and uppercase with generous spacing. The primary action uses the accent color but should not dominate the entire header.

### Hero / control-surface identity

An operational title may be large and editorial, but it must be followed by a concise explanation of what the operator can do. Pair the title with live counts or system state—not invented marketing claims.

### Tables and ledgers

Use tables for projects, runs, evidence, and comparable records. Columns should have clear uppercase labels, stable alignment, and a final directional link or inspect action. Empty states must be explicit and useful, for example: `No runs executed yet.`

### Search and filters

Use compact rectangular segmented tabs with a dark active state. Search inputs use paper surfaces, thin line borders, and visible focus treatment in accent orange. Search should filter visible records without changing the page composition.

### Status

Supported semantic states are `PASS`, `FAIL`, `BLOCKED`, and `INCONCLUSIVE`. Render the state as text plus a dot or icon. Use green for pass, orange/red for failure, amber for blocked, and muted/neutral treatment for inconclusive. Never silently map blocked or inconclusive to pass.

### Buttons and links

Use the accent-filled primary button once per local composition whenever possible. Secondary actions are outlined or paper-surface controls. Directional row links may use `↗`, but every icon-only control requires an accessible label.

### Metrics

Metrics should answer an operational question: count, freshness, pass rate, evidence quantity, or execution state. Keep values compact and labels small. Do not fill empty space with arbitrary numbers.

### Empty, loading, and error states

Empty states preserve the same table or panel geometry and explain the next action. Loading states should preserve layout to avoid shifts. Errors must identify the affected operation and offer a recovery path; do not use decorative red banners without actionable context.

## Do's and Don'ts

### Do

- Use one monochrome base plus orange action emphasis.
- Keep important information aligned to a visible grid.
- Use thin borders and surface contrast instead of heavy shadows.
- Make project, run, and evidence identifiers easy to scan.
- Preserve responsive behavior and keyboard focus states.
- Escape dynamic data before injecting it into HTML.
- Define new components by purpose and state, not by visual novelty.

### Don't

- Do not use blue/purple SaaS gradients or crypto-neon effects.
- Do not use a centered marketing hero as the default dashboard layout.
- Do not make every item a large rounded card.
- Do not use tiny gray text for required instructions or critical statuses.
- Do not rely on color alone for pass/fail/blocked state.
- Do not invent metrics, customer claims, or activity to make a screen look populated.
- Do not add a new color or radius without documenting its semantic role here.

## Implementation notes

The current dashboard implements these tokens as CSS custom properties in `src/holoqa/dashboard.py`. Future screens should reuse the same names and values rather than introducing local replacements. If the UI grows beyond inline HTML, extract the variables into a shared stylesheet while preserving the token names above.

When extending the system, update this file first, then update the shared implementation and visual regression coverage in the same change.
