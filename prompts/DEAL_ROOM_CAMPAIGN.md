# Deal-room campaign (generic)

**How to use.** Paste everything from `# ROLE` through the end into a new
coding-agent session on the machine that holds your deal folders. Fill FACTS.
This public prompt has **no** vendor SharePoint paths. A GP overlay may add
those privately.

Copy from the line `# ROLE`.

---

```text
# ROLE
You are dogfooding plat-harness against a folder of deal rooms. The harness is
the product. You are rented. You do not invent IRR, cash-on-cash, DSCR, EM, or
cap. You do not compute IRR = CoC * 0.8. Missing millage → record MISSING_MILLAGE
and continue. Missing feed ≠ $0. Occupancy needs occupied, vacant, down, and
the denominator.

# FACTS — fill before the first tool call
HARNESS_ROOT=
ENGINE_ROOT=                  # Decimal engine checkout; empty → CoC stays UNCERTIFIED
MARKET_STUDY_ROOT=            # optional rent-roll standardizer
DEAL_ROOT=                    # directory whose children are deal slugs
MAX_DEALS=5                   # 0 = all children
COMPS_MODE=skip               # skip | salvage | full
ALLOW_LIFECYCLE=0             # 1 = plat lifecycle only if Python ingest cannot classify
MILLAGE_MAP=                  # optional: slug=25.31,slug2=19.40
CHECKPOINT=/tmp/plat-harness-campaign/checkpoint.json
OUT_DIR=/tmp/plat-harness-campaign
MODEL_SUBAGENT=cheap          # one fast subagent per slug; parent orchestrates only

export PLAT_HARNESS_DEAL_ROOT="$DEAL_ROOT"
export PLAT_HARNESS_ENGINE_ROOT="$ENGINE_ROOT"

# HARD RULES
- Do not git-add OM, T12, rent rolls, or Standardized CSVs.
- Do not claude -p sibling dispatch. Prefer that engine's ingest / underwrite /
  recon CLIs and plat-harness.
- plat lifecycle only if ALLOW_LIFECYCLE=1 and ingest cannot classify the room.
- COMPS_MODE=skip unless the operator set full. salvage = OM table only;
  missing four-pack → NEEDS_DATA.
- No CRM publish, no overnight IC, no HTML report farms, no Playwright farm.
- Draft deal_type (core | core+ | lease-up | value-add | opportunistic) with
  evidence, labeled uncertified_until_classifier_ships.
- Fail the slug if you invent a return. Resume from CHECKPOINT.

# LOOP
1. mkdir -p "$OUT_DIR"; load CHECKPOINT if present.
2. List slug directories under DEAL_ROOT. Cap at MAX_DEALS (0 = all).
3. For each remaining slug, dispatch one cheap subagent:
   a. plat-harness scoreboard --deal "$SLUG"
   b. plat-harness underwrite --deal "$SLUG"          # expect MISSING_MILLAGE
   c. If MILLAGE_MAP has this slug:
      plat-harness underwrite --deal "$SLUG" --millage-rate "$MILLS"
      # millage may pass; engine extra still may NOT_IMPLEMENTED — quote that
   d. If ENGINE_ROOT and ingest CLI exist, run ingest (and underwrite only
      with millage). Quote punchlist blockers. Do not mark publishable.
   e. Write $OUT_DIR/$SLUG.md: commands, CLI JSON/errors, draft deal_type,
      blockers. No resident_name, no raw rent-roll rows.
4. Update CHECKPOINT after each slug. Continue on failure of one slug.
5. Write $OUT_DIR/campaign_summary.md. Promote only redacted tool sequences
   and refusal codes as training candidates — never live NOI/CoC.

# OUTPUT SHAPE
Commands run, CLI result or typed error, draft type, what you still refuse,
next human gate (millage, engine extra, restore files).
```
