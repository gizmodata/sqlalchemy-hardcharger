# Publishing sqlalchemy-hardcharger

## One-time PyPI setup

At https://pypi.org/manage/account/publishing/ add a **pending GitHub publisher**:

| Field | Exact value |
| --- | --- |
| PyPI project name | `sqlalchemy-hardcharger` |
| GitHub owner | `gizmodata` |
| Repository name | `sqlalchemy-hardcharger` |
| Workflow filename | `ci.yml` |
| Environment name | `release` |

Enter only the workflow filename, not `.github/workflows/ci.yml`. No API token secret is needed.
A pending publisher creates the PyPI project on the first successful upload; it does not reserve
the name beforehand. In the GitHub repository, create the `release` environment and limit it to
`v*` tags. After the first publication, manage the publisher at
https://pypi.org/manage/project/sqlalchemy-hardcharger/settings/publishing/.

References: https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/ and
https://docs.pypi.org/trusted-publishers/adding-a-publisher/.

## Release

1. Set the version in `sqlalchemy_hardcharger/_version.py` (the only place it lives) and move
   `CHANGELOG.md`'s `[Unreleased]` entries into a `## [X.Y.Z] - YYYY-MM-DD` section.
2. Run `make all` locally, commit, push `main`, and wait for CI to pass on that commit.
3. Tag the commit CI passed on and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. The tag's CI run tests the tagged commit again (lint, unit, integration, SQLAlchemy's
   compliance suite and the README examples on every supported Python and SQLAlchemy series, and
   the lowest dependency versions), builds and checks the sdist and wheel, checks that the tag
   matches the package version and that `CHANGELOG.md` has its section, and only then publishes to
   PyPI with an OIDC identity and attestations and creates the GitHub Release with the section as
   its notes.
5. Check PyPI shows the new version and install it into a fresh environment
   (`pip install sqlalchemy-hardcharger==X.Y.Z`). Update the Hardcharger integrations page on
   gizmodata.com only after that.

If the tag's run fails before anything was published, fix the code and move the same tag to the
fixed commit (this package is on PyPI only: there is no Go module proxy to have recorded the
tag). A failed upload can be retried by rerunning the failed job. Never move a tag whose version
is on PyPI: changed artifacts need a new version.
