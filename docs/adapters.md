# Adapters

Env only. No baked-in checkout paths.

| Env | Points at |
|---|---|
| `PLAT_HARNESS_OPS_ROOT` | Directory of `{asset_id}/Standardized/*.csv` |
| `PLAT_HARNESS_DEAL_ROOT` | Deal workspaces (gitignored in your overlay) |
| `PLAT_HARNESS_BOXSCORE_DB` | Ops SQLite (never commit) |
| `PLAT_HARNESS_ENGINE_ROOT` | Decimal engine checkout (`engine.engine.run_underwriting`) |
| `PLAT_HARNESS_GLOSSARY` | Override `docs/glossary.yaml` |
| `PLAT_HARNESS_GOLDEN_UW_OUTPUT` | Optional frozen `*_model_outputs.json` |

PMS layouts we intend to wrap (CSV/export first, not 1,000 OAuth logos):

- Yardi / RealPage / AppFolio / Entrata / ResMan Excel exports
- PDF OM + `pdftotext` harvesters with **fabricated** fixtures
- Direct-site comps via Playwright (not in this first seed)

Rent-roll standardizer and scrape engines copy later, only without live keys or PII.
