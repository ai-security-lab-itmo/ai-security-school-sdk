# Publishing the SDK

The public source repository is
[`ai-security-lab-itmo/ai-security-school-sdk`](https://github.com/ai-security-lab-itmo/ai-security-school-sdk).
GitHub tags and releases provide a pip-installable source distribution independently
of PyPI. Creating a tag or GitHub release does **not** trigger PyPI publishing.

## GitHub checks and release artifacts

`check.yml` runs for pushes to `main`, version tags, pull requests and manual runs.
It tests Python 3.12 and 3.13, runs Ruff and strict mypy, builds wheel/sdist, checks
metadata with Twine, and installs the wheel in a separate virtual environment.
The Python 3.12 distributions are retained as a workflow artifact for seven days.

Before tagging, update the version in `pyproject.toml`,
`src/ai_security_school_sdk/__init__.py`, and the SDK User-Agent in `_http.py`.
Run `uv lock` to synchronize the root package version in `uv.lock`, and update
the pinned installation example and example links in `README.md`.
Keep stable tags in exact `vX.Y.Z` form and never move an existing release tag.
For a GitHub release, attach the checked wheel/sdist and document the release
changes. Release 0.2.0 replaces the separate learner-labs API with the shared
`/api/agent-env` API. Deploy the matching platform endpoints before publishing it;
0.1.x scripts require migration to environments and tasks.

PyPI stores the README with each release; editing it in GitHub does not update
the package page. Publish a new patch version for README-only corrections.

Users can install that GitHub version without a PyPI account:

```sh
python -m pip install "ai-security-school-sdk @ https://github.com/ai-security-lab-itmo/ai-security-school-sdk/archive/refs/tags/v0.2.0.tar.gz"
```

## One-time PyPI setup

The existing project uses GitHub Trusted Publishing. If transferring or
recreating it, the PyPI project owner must configure the publisher before
running `publish.yml`. No PyPI API token or password is needed.

For the first upload, add a **pending publisher** under the PyPI account's
[Publishing settings](https://pypi.org/manage/account/publishing/), selecting
GitHub Actions and these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `ai-security-school-sdk` |
| Repository owner | `ai-security-lab-itmo` |
| Repository name | `ai-security-school-sdk` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

Enter only the filename `publish.yml`, not `.github/workflows/publish.yml`.
A pending publisher creates the project on its first successful upload; it does
not reserve the package name. If the project already exists and you own it, add
the same GitHub publisher in that project's Publishing settings.
[PyPI pending publisher documentation](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/),
[existing-project configuration](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

Create the GitHub environment `pypi` in the repository settings. Restrict its
deployment refs to release tags matching `v*`; maintainers can additionally
configure required reviewers. Its name must match the PyPI publisher exactly.

## Manual PyPI release

The tagged commit must contain both workflow files. Dispatch **from the release
tag**, with the same tag as the input; dispatching from `main` is rejected:

```sh
gh workflow run publish.yml \
  --repo ai-security-lab-itmo/ai-security-school-sdk \
  --ref v0.2.0 \
  -f tag=v0.2.0
```

The workflow resolves `refs/tags/v0.2.0` to its commit SHA, checks it against the
dispatch commit, and passes that SHA to the isolated CI/build job. The package
name and version must match the release tag. The publish job receives only the
checked artifact, has `id-token: write` without repository write permissions, and
does not check out or build project code. Third-party actions are pinned by full
commit SHA. This follows the separation recommended by the
[PyPA publishing action](https://github.com/pypa/gh-action-pypi-publish#trusted-publishing).

Watch the workflow through completion and verify the published package in a fresh
virtual environment:

```sh
python -m pip install --index-url https://pypi.org/simple ai-security-school-sdk==0.2.0
python -m pip check
python -c "from ai_security_school_sdk import Client, AsyncClient"
```

Only after this upload succeeds does `pip install ai-security-school-sdk` resolve
the new version from PyPI. PyPI publication is permanent for a given artifact name;
release changes under a new version. The workflow intentionally fails on existing
files instead of skipping them. An interrupted upload should be investigated
before retrying.
