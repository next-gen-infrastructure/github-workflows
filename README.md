# GitHub Workflows

Reusable GitHub Actions workflows and actions for Python, Node and Go repositories, with Docker builds to
GHCR. Workflows whose name starts with `self-` run this repository's own CI and release automation.

Each section has one example. Every input is described in the `inputs:` block of the linked file. Pin
`@v1`: it moves to every 1.x release, and a breaking change gets `v2`. The caller always keeps its own
triggers, `concurrency` and test gate (`needs:`).

## Setup

The private-dependency and release workflows authenticate through a GitHub App you own, installed on the
organisation. Pass the secrets with `secrets: inherit`:

| Secrets | Used by | App permissions |
|---|---|---|
| `DEPS_READER_APP_ID`, `DEPS_READER_PRIVATE_KEY` | `python-test`, `docker-build` | Contents: read |
| `RELEASE_APP_ID`, `RELEASE_APP_PRIVATE_KEY` | `python-dev-tag`, `python-release` | Contents: write |

This repository's own release needs the repository variable `RELEASE_PLEASE_APP_ID` and the secret `RELEASE_PLEASE_PRIVATE_KEY`.

## Python tests

[`python-test.yml`](.github/workflows/python-test.yml) clones the `../<name>` siblings (derived from
`[tool.uv.sources]`; set `sibling-prefix` to clone only names starting with it), runs `uv sync` and pytest. On a pull request, `compare-with-base` fails only on
tests that fail here and pass on the target branch.

```yaml
jobs:
  test:
    uses: next-gen-infrastructure/github-workflows/.github/workflows/python-test.yml@v1
    secrets: inherit
```

## Python lint (changed files)

[`python-lint-changed.yml`](.github/workflows/python-lint-changed.yml) runs on pull requests only: per
changed `.py` file, ruff findings must not rise versus the target branch. Ruff runs through `uvx` at the
version in `uv.lock`, so no sync and no sibling clone.

```yaml
jobs:
  lint:
    uses: next-gen-infrastructure/github-workflows/.github/workflows/python-lint-changed.yml@v1
```

## Node tests

[`node-test.yml`](.github/workflows/node-test.yml) runs type check (`npx tsc --noEmit`), lint and format
(`npm run lint`, `npm run format:check`) in one job, and the unit tests (`npm run test:run`) in a second,
parallel job. Needs a `package-lock.json`.

```yaml
jobs:
  test:
    uses: next-gen-infrastructure/github-workflows/.github/workflows/node-test.yml@v1
    with:
      codecov: true
    secrets: inherit   # CODECOV_TOKEN
```

## Go tests

[`go-test.yml`](.github/workflows/go-test.yml) runs `go build`, `go vet` and `go test`, with the Go version
from `go.mod`. On a pull request, the `image_inputs_changed` output tells the caller whether to build its
Docker image.

```yaml
jobs:
  test:
    uses: next-gen-infrastructure/github-workflows/.github/workflows/go-test.yml@v1
```

## Python dev tag and release

[`python-dev-tag.yml`](.github/workflows/python-dev-tag.yml) tags every change on dev as a release
candidate (`vX.Y.ZrcN`). [`python-release.yml`](.github/workflows/python-release.yml) cuts a final
`vX.Y.Z` from main, for members of the release team, and publishes a GitHub Release with the wheel.

```yaml
jobs:
  test:
    uses: ./.github/workflows/test.yml
    secrets: inherit
  release:
    needs: test
    uses: next-gen-infrastructure/github-workflows/.github/workflows/python-release.yml@v1
    with:
      version: ${{ inputs.version }}
    secrets: inherit
```

**Test gate (required).** These workflows do not run the repository's tests, because a local `./` path
would resolve in the caller's checkout. The calling repository runs them in its own job and the release
job `needs` it, as above. Without that job, a release is published untested.

**Release convention (required).** The package version comes from git tags, never from a hand-written
number. Use `hatch-vcs` in `pyproject.toml`, as the example below does:

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
dynamic = ["version"]

[tool.hatch.version]
source = "vcs"
fallback-version = "0.0.0"
tag-pattern = '^v(?P<version>\d+\.\d+\.\d+(?:rc\d+)?)$'
```

Both workflows build the package and fail unless its filename carries the tag's version, so a repository
that does not follow this convention cannot use them.

## Trigger downstream build

[`trigger-downstream.yml`](.github/workflows/trigger-downstream.yml) sends a `repository_dispatch` to each
listed repository.

```yaml
jobs:
  trigger-downstream:
    uses: next-gen-infrastructure/github-workflows/.github/workflows/trigger-downstream.yml@v1
    with:
      repositories: '["my-other-app"]'
    secrets: inherit
```

The dispatch payload:

```json
{ "source_repo": "<current repo>", "branch": "<current branch>" }
```

## Docker build

[`docker-build.yml`](.github/workflows/docker-build.yml) builds and pushes `ghcr.io/<owner>/<image-name>`.
Tags are `latest` and `sha-<sha>` on main, `dev-<sha>` on dev, and `pr-<n>` on a pull request (built, not
pushed). After a dispatch from a dependency, `-<dependency sha>` is appended to the sha tags. Images are
built for `dev` and `main` only: a push or dispatch on any other branch fails with "refusing build
environment".

```yaml
jobs:
  build:
    needs: test
    uses: next-gen-infrastructure/github-workflows/.github/workflows/docker-build.yml@v1
    with:
      image-name: my-app
    secrets: inherit
```

**Build secret file (`BUILD_ENV_FILE`).** For an image that needs build-time settings from a `.env` file
(for example a front-end image), store the whole `.env` as one secret named `BUILD_ENV_FILE` in a GitHub
environment, and pass that environment's name as `environment`. The build receives it as the secret file
`env`, written to the runner's temp directory, never into the build context. A repository without such a
file leaves `environment` unset. If the named environment has no `BUILD_ENV_FILE`, the build runs without
it.

## GHCR cleanup

[`ghcr-cleanup`](.github/actions/ghcr-cleanup/action.yml) is an action, not a workflow. It keeps the
newest main (`latest`, `sha-*`) and dev (`dev-*`) versions of a package and deletes the rest. Repositories
that build with `docker-build` get it by passing `cleanup: true`. To run it by hand or on a schedule, call
it from a job that grants `packages: write`:

```yaml
on:
  schedule:
    - cron: '0 7 * * 0'
  workflow_dispatch:
    inputs:
      dry_run: { type: boolean, default: true }
jobs:
  cleanup:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: next-gen-infrastructure/github-workflows/.github/actions/ghcr-cleanup@v1
        with:
          package: my-app
          dry-run: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run }}
```
