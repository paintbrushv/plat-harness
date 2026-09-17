# Bring your engine

This directory is a **path-dep stub**. It is not a copy of a private underwriting
tree.

`plat-harness underwrite --millage-rate …` **refuses** millage-less runs
(`MISSING_MILLAGE`).

Without an engine extra, the harness **does not invent** CoC / IRR / DSCR / EM /
cap. Typed errors: `NOT_IMPLEMENTED` or `UNCERTIFIED_METRIC`.

To wire calc:

```bash
export PLAT_HARNESS_ENGINE_ROOT=/path/to/your/decimal/engine
# that tree must expose engine.engine.run_underwriting (Decimal)
```

or install a separately licensed package that provides the same import path.

The synthetic 80-unit sample in `samples/deals/example_garden_style` will not
emit a cash-on-cash number until that extra is imported.
