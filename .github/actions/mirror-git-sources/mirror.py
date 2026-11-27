"""Mirror the private git dependencies a project's lock file pins, as bare repos a docker build can fetch from.

A build has no credentials, so `git config url.<mirror>.insteadOf https://github.com/` points it at these
mirrors instead. The lock files understood are the LOCK_READERS below. Stdlib only (Python 3.11 for tomllib);
configured by environment variables (see action.yml).
"""
import os
import pathlib
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

GITHUB_URL = re.compile(r"https://github\.com/([^/?#]+/[^/?#]+?)(?:\.git)?(?:[?#].*)?$")


def from_uv_lock(text):
    """The `owner/repo` of every GitHub git source in a uv.lock."""
    import tomllib

    repos = set()
    for package in tomllib.loads(text).get("package", []):
        url = package.get("source", {}).get("git")
        if not url:
            continue
        match = GITHUB_URL.match(url)
        if match:
            repos.add(match.group(1))
        else:
            print(f"::notice::{package.get('name')}: only github.com git sources are mirrored, not {url}")
    return repos


LOCK_READERS = {"uv.lock": from_uv_lock}  # lock file name -> reader; add one per ecosystem


def git_sources(project_dir):
    """The repos pinned by the lock files found in `project_dir`; fails when it has none of the known ones."""
    found = {name: read for name, read in LOCK_READERS.items() if (project_dir / name).exists()}
    if not found:
        sys.exit(f"::error::no supported lock file found in the project (looked for {', '.join(LOCK_READERS)})")
    return sorted({repo for name, read in found.items() for repo in read((project_dir / name).read_text())})


def mirror(repos, dest, git=subprocess.run):
    """Bare-clone each `owner/repo` to dest/owner/repo.git, 8 at a time.

    The `.git` name serves both `https://github.com/owner/repo` and `.../repo.git` once git rewrites the URL to a path.
    """
    def clone(repo):
        cmd = ["git", "clone", "-q", "--bare", f"https://github.com/{repo}", str(dest / f"{repo}.git")]
        if git(cmd, capture_output=True).returncode != 0:
            sys.exit(f"::error::cannot mirror {repo}")

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(clone, repos))


def main():
    if sys.version_info < (3, 11):
        sys.exit("::error::mirror-git-sources needs Python 3.11 or newer (tomllib)")
    repos = git_sources(pathlib.Path(os.environ["WORKSPACE"]))
    mirror(repos, pathlib.Path(os.environ["MIRROR_DIR"]))
    print(f"Mirrored {len(repos)} git sources: {' '.join(repos)}")


if __name__ == "__main__":
    main()
