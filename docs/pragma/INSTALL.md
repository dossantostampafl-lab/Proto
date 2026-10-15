# PRAGMA-CORE 2.0 in Proto

Installed manually from `PRAGMA-CORE_2.0_Codex_Claude.zip` (package SHA256SUMS verified). The
package installer refuses to run here because `AGENTS.md` and `CLAUDE.md` already exist, so:

- The package `AGENTS.md` became `docs/pragma/PRAGMA-CORE.md`. The root `AGENTS.md` references it
  and keeps precedence; `CLAUDE.md` imports it with `@docs/pragma/PRAGMA-CORE.md`.
- Skills copied verbatim to `.claude/skills/pragma-*` and `.agents/skills/pragma-*`. The only change
  is the `pragma-benchmark` pointer to `docs/pragma/BENCHMARK_MODELO.csv`.
- Templates kept here: `PROMPT_TAREFA.md`, `PROMPT_UNICO.md`, `CHECKPOINT_MODELO.md`,
  `BENCHMARK_MODELO.csv`, and the package `README.md`.
- Not installed: the package `CLAUDE.md` bridge (replaced by the import above), `instalar.py`, and
  `REFERENCIA_THE_CREATION_OS.md` (it describes another project).
