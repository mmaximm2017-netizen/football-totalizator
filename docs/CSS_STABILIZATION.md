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

## First extraction step

The home-screen CSS was extracted from the inline `<style>` block in `templates/index.html` into `static/css/home.css`.

What moved:

- Home/month/day accordion styles.
- Match card V2 styles.
- Deadline/timer styles.
- Finished and active match state styles.
- WC-2026 and tournament override blocks.
- Bottom sheet and compact bets styles.
- Mobile `@media (max-width: 430px)` rules.
- Existing comments, selector names, selector order, media query order, and WC override order.

What changed in `templates/index.html`:

- The transferred `<style>` block was replaced with `<link rel="stylesheet" href="{{ url_for('static', filename='css/home.css') }}">`.

## What intentionally remains inline

- Accordion JavaScript.
- Prediction save JavaScript.
- Deadline/timer JavaScript.
- Bets bottom sheet JavaScript.
- Jinja match/month/day rendering logic.
- Dynamic Jinja-generated `style` attributes used for accordion initial state and arrow rotation.

## Next recommended cleanup step

Do a rendered UI verification pass before moving any more code:

- desktop and mobile home screen;
- active, closed, and finished cards;
- WC-2026 active card background;
- stepper controls;
- save button states;
- bottom sheet open/close and compact bets rendering.

After visual verification, the next cleanup should be either adding a cache-busting/static asset convention for `home.css` if needed by deployment, or extracting the remaining inline JavaScript into a separate file in a similarly mechanical pass.
