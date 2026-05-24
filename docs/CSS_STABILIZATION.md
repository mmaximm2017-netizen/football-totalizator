# CSS_STABILIZATION

## First safe pass

This pass keeps the existing home-screen CSS inline in `templates/index.html` and only introduces aliases for repeated literal values. No selectors, class names, media queries, animations, WC-2026 overrides, match-state classes, stepper styles, JavaScript hooks, or layout rules were renamed or moved.

## Duplicate groups found

- Pill-like radii repeated as `border-radius: 999px`.
- Glass blur repeated as `blur(14px)` across the main accordion/card surfaces.
- Fast transition timing repeated as `0.18s ease` inside the `.match-card-v2` transition list.
- Soft blue shadow repeated as `0 6px 14px rgba(0,78,140,0.08)`.

## Variables introduced

- `--radius-pill: 999px`
- `--transition-fast: 0.18s ease`
- `--glass-blur: blur(14px)`
- `--shadow-soft-blue: 0 6px 14px rgba(0,78,140,0.08)`

## Intentionally untouched

- Responsive breakpoints and mobile-specific rules.
- WC-2026 and tournament override blocks.
- Match-card state selectors such as `.active`, `.closed`, `.finished`, `.predicted`, `.urgency-mid`, `.urgency-hot`, and `.save-success-pulse`.
- Score stepper styles and form-control contracts.
- Animation keyframes and animation declarations.
- Larger spacing/radius families such as `10px`, `12px`, `14px`, `18px`, and `24px`, because those values are shared across different semantic roles and need a visual baseline before broader tokenization.
- CSS extraction from `templates/index.html`; this pass is stabilization only.

## Risk notes

The variables are direct aliases for existing values, so computed styles should remain equivalent. The remaining risk is limited to browser support for CSS custom properties, which is acceptable for modern browsers already needed by the app UI.
