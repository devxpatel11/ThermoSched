# Contribution and integration rules

## Before feature work

Each member must complete their E0 row in `docs/environment_matrix.md` from Ubuntu WSL 2. Record actual results; use `N/A`, `failed`, or `skipped: <reason>` instead of assuming a capability exists.

Start every task from current `main`:

```bash
git switch main
git pull --ff-only origin main
git switch -c <branch-from-docs/work_plan.md>
```

One branch and one pull request must contain exactly one task ID. Direct feature pushes to `main` are prohibited after the foundation commit.

## File ownership

| Owner | Primary paths | Shared handoff |
| --- | --- | --- |
| A — Scheduler/Integration | `thermosched/models.py`, `thermosched/config.py`, `thermosched/scheduler/`, root dependency/configuration files, release integration | Publishes A1 contracts first; owns controller integration and release freeze |
| B — Telemetry/Systems | `thermosched/sensors/`, `thermosched/telemetry/`, simulation/metrics code, `docs/experiment_results.md` | Supplies capability, provenance, telemetry, model, and experiment results through A1/A4 contracts |
| C — Testing/CLI/Docs | `workloads/`, `tests/`, `thermosched/cli.py`, `thermosched/__main__.py`, `thermosched/logging/`, `thermosched/dashboard/`, `scripts/`, final `README.md` | Uses stable contracts; reconciles CLI, tests, demo, and documentation on frozen `main` |
| Integration lead | `.github/`, `AGENTS.md`, `CONTRIBUTING.md`, `.gitattributes`, `.gitignore`, cross-cutting contract changes | Changes these only in a dedicated `chore/` or `docs/` PR |

Do not create placeholders or opportunistic refactors in another owner's path. If a task needs another owner's file, state it in the PR and have that owner review the change.

## Shared-interface protocol

The A1 contracts merge before dependent work. After that, changing a shared dataclass field, configuration key, event field, CLI contract, or public interface requires a small dedicated contract PR:

1. Describe the old and proposed contract and name every affected task.
2. Obtain reviews from the owners of all affected paths.
3. Merge the contract PR first.
4. Rebase dependent branches onto the new `main`, then update implementations.

Never bundle a contract rename into a feature PR. This rule keeps parallel branches from resolving the same interface differently.

## Pull request contract

A PR must:

- use the exact task ID and branch from `docs/work_plan.md`;
- remain small enough to review as one task;
- list changed files and any shared-interface impact;
- include focused tests, or explain why tests do not apply;
- show the exact validation commands and observed outcomes;
- update only the README sections affected by implemented behavior;
- report hardware-dependent checks as passed, failed, or skipped with a reason;
- contain no generated logs, environment folders, secrets, or absolute user paths.

Before requesting review, update your branch without merging `main` into it:

```bash
git fetch origin
git rebase origin/main
python -m pytest -q
```

Use task-oriented Conventional Commit messages, for example `feat(policy): add thermal risk scoring` or `test(controller): cover process cleanup`. The repository uses squash merges so each task lands as one reviewable commit.

## Merge queue

Merge only green, reviewed PRs in dependency order. A1 lands first. Independent C1 may develop in parallel; B1 may prototype in parallel but must rebase onto A1 before merging. Later tasks merge only after every dependency in `docs/work_plan.md` is on `main`.

After each merge, wait for `main` CI before merging the next PR. The next branch rebases onto that green commit. If two PRs touch the same file, the documented owner merges first and the second owner rebases and resolves the conflict with them before review.

`main` must remain runnable. Day 6 accepts release fixes only after code freeze; no new features begin after Day 5.
