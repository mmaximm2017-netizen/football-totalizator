# Home CSS map

`static/css/home.css` remains a single production stylesheet on purpose.

The production nginx serves `/opt/football-totalizator/static` directly, so structural CSS changes must preserve both cascade order and static delivery. The stylesheet is therefore documented in-place before any future cleanup.

## Main sections

1. Base page and shared home styles.
2. Bets sheet — base layer.
3. World Cup 2026 — primary home layer.
4. RPL — base layer.
5. Match Card V2 — shared layout system.
6. Skeleton loader.
7. Bets sheet — World Cup 2026 theme.
8. Final shared RPL / Russian Cup metrics.
9. RPL finished / closed authoritative layer.
10. Bets sheet — RPL final theme.
11. RPL scroll performance hardening.

## Safety rules

- Do not reorder sections without a dedicated visual check.
- Do not split `home.css` again unless production static delivery and browser-visible asset checks are explicitly verified.
- Keep the RPL performance hardening layer at the end.
- Treat late overrides as intentional until proven redundant by tests and visual verification.
- Make one small CSS cleanup per pull request.
