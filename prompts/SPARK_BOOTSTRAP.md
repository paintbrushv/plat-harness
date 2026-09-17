# Spark bootstrap (generic)

Paste into a new session on the GPU box that also trains local models.
Fill FACTS. Clone **your** git remotes. Rsync proprietary data from the
machine that already holds OM / T12 / ops exports into a **non-git** data
root. Do not put deal rooms inside the training dataset directory.

Copy from `# ROLE`.

---

```text
# ROLE
You are on a GPU workstation (local LLM + plat-harness). A data host on the
same network holds live deal rooms and ops exports. Inventory disk first so
you do not overwrite model/FT directories. Clone code. Rsync overlay data
into DATA_ROOT (chmod 700, never git init). Point PLAT_HARNESS_* there.

Do not invent CoC/IRR. Do not git-add OM/T12/rent rolls/*.db/.env.

# FACTS
DATA_HOST=user@data-host
CODE_ROOT=$HOME/plat
DATA_ROOT=$HOME/data/repe
MODELS_ROOT=$HOME/models
TRAIN_ROOT=$HOME/train/plat-harness
GIT_ORG=
REPOS="plat-harness plat-agent <engine-repo> <market-study-repo>"

# PHASES
0. df -h; ls $HOME; locate existing model/FT dirs; propose CODE_ROOT/DATA_ROOT
   if collision.
1. mkdir code vs data vs models vs redacted train.
2. git clone each repo into CODE_ROOT.
3. ssh DATA_HOST; rsync --dry-run ops Standardized + deals; then rsync.
   Exclude .git .venv Data/*.xlsx if disk is tight.
4. Write DATA_ROOT/overlay.env with PLAT_HARNESS_OPS_ROOT/DEAL_ROOT/ENGINE_ROOT.
5. pip install -e CODE_ROOT/plat-harness; source overlay.env; smoke ask/scoreboard.
6. Optional: SSH Graph fetch on DATA_HOST, rsync new raw_inputs, then local ingest.
7. Redacted tool-sequence JSONL into TRAIN_ROOT only.

See also SPARK_OPERATOR.md and DEAL_ROOM_CAMPAIGN.md in this prompts/ folder.
```
