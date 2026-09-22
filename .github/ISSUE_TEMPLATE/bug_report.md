---
name: Bug report
about: Report a crash, wrong number, or a gate that let bad data through
labels: bug
---

**What happened?** A clear description of the incorrect behavior.

**What did you expect?**

**Reproduction** (synthetic inputs only — never paste live deal, tenant, or
resident data):

```
plat-harness ...
```

**Exit code and output** (stdout JSON / stderr):

**Environment:** OS, Python version, install method (`pip install plat-harness`,
extras enabled).

**Governance note:** if the report involves an underwriting number, name the
deterministic tool that produced it. Numbers that no tool vouches for are bugs,
not findings.
