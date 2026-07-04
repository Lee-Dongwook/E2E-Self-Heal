# Releasing

This project ships on two channels from one tag:

- **PyPI** — the `e2e-healer` CLI (`ai-driven-e2e` package), published by
  [`.github/workflows/publish.yml`](../.github/workflows/publish.yml) on GitHub release.
- **GitHub Marketplace** — the composite Action ([`action.yml`](../action.yml)), listed from
  the same release.

## One-time setup (console tasks)

These cannot be scripted — do them once in the respective web consoles.

### 1. PyPI trusted publishing (OIDC — no API token)

`publish.yml` uses [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no
PyPI token/secret is stored. Configure it on PyPI:

1. Log in to [pypi.org](https://pypi.org/) and go to **Your projects → Publishing** (or, for a
   brand-new name, **Account → Publishing → Add a pending publisher**).
2. Add a **GitHub** trusted publisher with:
   - **Owner**: `Lee-Dongwook`
   - **Repository**: `E2E-Self-Heal`
   - **Workflow name**: `publish.yml`
   - **Environment**: `pypi`
3. Confirm the project name `ai-driven-e2e` is available (or already owned by you).

### 2. GitHub `pypi` environment

`publish.yml` runs in `environment: pypi`. Create it under
**Settings → Environments → New environment → `pypi`** (add reviewers/protection if desired).

### 2b. TestPyPI rehearsal (recommended)

[`publish-testpypi.yml`](../.github/workflows/publish-testpypi.yml) publishes to
[TestPyPI](https://test.pypi.org/) on manual dispatch, so the real run is never the first.
Set it up once:

- Add a **trusted publisher** on [test.pypi.org](https://test.pypi.org/) for repo
  `Lee-Dongwook/E2E-Self-Heal`, workflow `publish-testpypi.yml`, environment `testpypi`.
- Create a GitHub environment named `testpypi`.

Then run it from **Actions → publish-testpypi → Run workflow** and verify:

```bash
pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ ai-driven-e2e
```

### 3. Marketplace (Action)

- Repo must be **public**, `action.yml` at root with `name`/`description`/`branding` (all set).
- The action `name` (`E2E Self-Heal`) must be unique across the Marketplace.
- Accept the **GitHub Marketplace Developer Agreement** once.

## Per-release checklist

1. `make check && make test` green locally, and CI green on `main`.
   Optionally rehearse first via **Actions → publish-testpypi** (see 2b above).
2. Bump `version` in `pyproject.toml`; move the `## [Unreleased]` section in
   [`CHANGELOG.md`](../CHANGELOG.md) to the new version with today's date.
3. Commit, then tag: `git tag -a vX.Y.Z -m "..."` and `git push origin main --follow-tags`.
4. Update the moving major tag so Action users can pin `@vX`:
   `git tag -f -a v0 -m "..." vX.Y.Z && git push origin v0 --force`.
5. Draft a **GitHub Release** on tag `vX.Y.Z`; tick **Publish to Marketplace**; publish.
   - Publishing the release triggers `publish.yml` → PyPI.
6. Verify: `pip install ai-driven-e2e==X.Y.Z` works and the Marketplace listing is live.

## Version policy

Semantic versioning. In `0.x` (alpha), minor bumps may include breaking changes; pin exact
versions in production. The `v0` moving tag tracks the latest `0.x` for convenience.
