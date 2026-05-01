# League Coverage Probe — 2026-05

Probed 2026-05-01. Each league fetched once with `h2h` market (`uk,eu` regions, 2 cr/call).

Promotion bar (prod): `avg_books >= 20 AND p95_dispersion <= 0.04`
Promotion bar (dev):  `avg_books >= 15`

| League | Odds API key | FDCO | n_fixtures | avg_books | p95_dispersion | n_3pct_hits | Prod? | Dev? |
|---|---|---|---|---|---|---|---|---|
| La Liga | `soccer_spain_la_liga` | `SP1` | 20 | 32.7 | 0.0832 | 5 | ✗ noisy | ✓ |
| La Liga 2 | `soccer_spain_segunda_division` | `SP2` | 10 | 32.4 | 0.0154 | 0 | ✓ | ✓ |
| Eredivisie | `soccer_netherlands_eredivisie` | `N1` | 18 | 23.8 | 0.0138 | 0 | ✓ | ✓ |
| Primeira Liga | `soccer_portugal_primeira_liga` | `P1` | 9 | 37.8 | 0.0202 | 1 | ✓ | ✓ |
| Ligue 2 | `soccer_france_ligue_two` | `F2` | 10 | 39.7 | 0.0168 | 1 | ✓ | ✓ |

## Notes

- **La Liga** has excellent book coverage but p95 dispersion (0.0832) is way above the 0.04 prod threshold — confirms existing exclusion. Elevated dispersion is driven by fixtures closer to the end of the season where lines haven't settled.
- **Eredivisie** split fixture pool: 9 near-term fixtures with ~38 books, 9 future fixtures with only 9 books (early odds). The avg (23.8) clears the prod bar; p95 dispersion (0.0138) well within threshold.
- **La Liga 2, Primeira Liga, Ligue 2** all comfortably pass the prod bar. Low hit counts reflect tight markets, not thin coverage.
- La Liga can go into **dev only** if we want more data; all others ready for prod (M.3).
