# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Reusable GitHub Actions workflows (`workflow_call`) and composite actions for Python, Node and Go repos, plus Docker builds to GHCR. There is no application code: it is YAML under `.github/`, plus the scripts behind four composite actions (stdlib-only Python in `run-pytest/run_pytest.py`, `clone-siblings/siblings.py` and `mirror-git-sources/mirror.py`, JS in `ghcr-cleanup/cleanup-ghcr.js`), each with unit tests next to it. `README.md` documents each workflow's usage; every input is described in the workflow's own `inputs:` block.

- `.github/workflows/self-*.yml` run this repo's own CI (`self-lint`) and release (`self-release`). All other workflows are for callers.
- `.github/actions/` holds the composite actions (`clone-siblings`, `run-pytest`, `mirror-git-sources`, `ghcr-cleanup`).

## Commands

- Lint and test everything (what CI runs): `pre-commit run --all-files` (hooks: actionlint with shellcheck, zizmor with `--fix=safe`, yaml/whitespace checks, the JS and Python unit tests).
- Python action tests only: `uv run --no-project --with pytest pytest -q .github/actions` (one file: pass its path).
- JS tests only: `node --test .github/actions/ghcr-cleanup/cleanup-ghcr.test.js` (pass the file, not the directory; Node 24 doesn't search directories).
- Run a single hook: `pre-commit run actionlint --all-files` or `pre-commit run actions-python-test --all-files`.

Workflows can't be executed locally; they are validated by the linters and the unit tests above. The Python scripts read their configuration from environment variables (listed in each `action.yml`), so they can also be run by hand against a throwaway project. actionlint does not lint the `run:` blocks of composite actions.

## Conventions that span files

- **Logic lives in actions, not workflows.** A reusable workflow cannot run scripts from this repo (a local `./` path resolves in the caller's checkout), so it calls a composite action as `...@v1 # x-release-please-major`. release-please bumps those markers, so a new action reference needs the comment. `clone-siblings` runs plain `python3` (3.11+, for `tomllib`) because it runs before `setup-uv` and `docker-build` has no `uv`; `run-pytest` runs through `uv run`.
- **Versioning and releases:** release-please (`release-type: simple`) drives releases from Conventional Commits. Callers pin `@v1`; `self-release` force-moves the major tag (`v1`) on every release, so a breaking change needs a new major. `release-please-config.json` lists `extra-files` (`docker-build.yml`, `python-test.yml`) where release-please bumps version markers: keep that in mind when editing those files. Don't hand-edit `CHANGELOG.md` or `.release-please-manifest.json`.
- **Callers keep their own triggers, `concurrency` and test gate.** Workflows here deliberately don't run the caller's tests; callers add `needs: test`.
- **Auth is via GitHub Apps, not PATs:** `DEPS_READER_*` (python-test, docker-build), `RELEASE_APP_*` (python-dev-tag, python-release), `TRIGGER_DOWNSTREAM_*` (trigger-downstream), and `RELEASE_PLEASE_*` for this repo's own release. Callers in the same organisation pass them with `secrets: inherit`; across organisations `inherit` passes nothing, so the secrets are mapped explicitly (see the README Setup section).
- **python-test defaults:** pytest-xdist workers from the cgroup CPU quota (at most 8), failures re-run serially so order-dependent tests only warn, coverage measured when `pytest-cov` is present and never failing unless `coverage-fail-under` is set. The project must declare xdist and pytest-cov; without them the run is serial with no coverage.
- **Siblings:** `clone-siblings` takes the sibling repos from readers of project files (today the `../<name>` path dependencies in `[tool.uv.sources]`) plus an explicit list (`sibling-repos`, `sibling-repos-file`), all added together, and follows each clone's own dependencies. `docker-build` with `siblings: true` clones them with no fallback branch and passes them as the named build context `siblings` (`COPY --from=siblings / /`), so no repo list is kept in a workflow or Dockerfile. A new kind of project file is a new reader in `siblings.py` (add it to `READERS` with a test).
- **docker-build stays language-neutral:** ecosystem-specific parsing lives behind reader lists in the actions (`READERS` in `siblings.py`, `LOCK_READERS` in `mirror.py`), not in the workflow's inputs. `mirror-git-sources` mirrors the git dependencies a lock file pins (today only `uv.lock`) and fails when it finds no supported lock file; a new ecosystem is a new reader and a test.
- **Python release convention:** package version comes from git tags via `hatch-vcs`; `python-dev-tag` / `python-release` fail if the built filename doesn't carry the tag version.
- **Security linting is strict:** zizmor and actionlint run on every PR. Pass untrusted values through `env:` instead of inline `${{ }}` in `run:`; keep top-level `permissions` minimal; pin third-party actions by SHA (first-party `actions/*` use major tags); suppress findings only with a targeted `# zizmor: ignore[rule]` comment and a reason (see `self-release.yml`). `.github/zizmor.yml` and `.github/actionlint.yaml` hold the repo config.
- **Docker builds:** `docker-build` only builds `dev` and `main` (other refs fail with "refusing build environment"); `BUILD_ENV_FILE` is mounted as a build secret file, never into the build context.
