"""Clone the sibling repos a project depends on, and optionally stage them for a docker build.

The siblings come from the readers below (today: the `../<name>` path dependencies of [tool.uv.sources]) and from
an explicit list. Stdlib only (Python 3.11 for tomllib); configured by environment variables (see action.yml).
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
PARALLEL = 8


def from_pyproject(repo_dir, prefix):
    """The `../<name>` path dependencies in the pyproject.toml of `repo_dir`, whose name starts with `prefix`."""
    import tomllib

    pyproject = repo_dir / "pyproject.toml"
    if not pyproject.exists():
        return []
    sources = tomllib.loads(pyproject.read_text()).get("tool", {}).get("uv", {}).get("sources", {})
    names = set()
    for source in sources.values():
        match = re.match(rf"\.\./({NAME})", source.get("path", "")) if isinstance(source, dict) else None
        if match and match.group(1).startswith(prefix):
            names.add(match.group(1))
    return sorted(names)


READERS = [from_pyproject]  # a reader per kind of project file; each returns sibling names for a repo directory


def sibling_names(repo_dir, prefix):
    """What every reader finds in `repo_dir`."""
    return sorted({name for read in READERS for name in read(repo_dir, prefix)})


def listed_repos(text):
    """Repo names from an explicit list: one per line, `#` starts a comment, blank lines ignored."""
    names = [line.split("#")[0].strip() for line in text.splitlines()]
    names = [name for name in names if name]
    for name in names:
        if not re.fullmatch(NAME, name):
            sys.exit(f"::error::refusing sibling repo {name!r}")
    return sorted(set(names))


def clone(repo, parent, owner, ref, fallback_ref, git=subprocess.run):
    """Shallow-clone `repo` at `ref`; at `fallback_ref` when it lacks that branch (no fallback when empty)."""
    url = f"https://github.com/{owner}/{repo}.git"

    def attempt(branch):
        return git(["git", "clone", "--depth", "1", "-b", branch, url, str(parent / repo)], capture_output=True)

    if attempt(ref).returncode == 0:
        return
    if not fallback_ref:
        sys.exit(f"::error::branch {ref} not in {repo}")
    print(f"branch {ref} not in {repo}; falling back to {fallback_ref}")
    if attempt(fallback_ref).returncode != 0:
        sys.exit(f"::error::cannot clone {repo} at {ref} or {fallback_ref}")


def clone_closure(workspace, prefix, clone_one, listed=()):
    """Clone the siblings of `workspace` and the `listed` repos, then theirs, until nothing new appears."""
    parent = workspace.parent
    cloned = []
    frontier = sorted(set(sibling_names(workspace, prefix)) | set(listed))
    with ThreadPoolExecutor(PARALLEL) as pool:
        while True:
            todo = [name for name in frontier if not (parent / name).exists()]
            if not todo:
                return cloned
            for name in todo:
                print(f"Cloning sibling: {name}")
            list(pool.map(clone_one, todo))  # one failed clone raises and fails the step
            cloned += todo
            frontier = sorted({n for repo in todo for n in sibling_names(parent / repo, prefix)})


def stage(repos, parent, dest, files):
    """Copy each repo's `files` (all but .git when empty) to dest/<repo>, the layout `COPY --from=<ctx> / /` wants."""
    shutil.rmtree(dest, ignore_errors=True)
    for repo in repos:
        if not files:
            shutil.copytree(parent / repo, dest / repo, ignore=shutil.ignore_patterns(".git"))
            continue
        for name in files:
            path = pathlib.PurePath(name)
            if path.is_absolute() or ".." in path.parts:
                sys.exit(f"::error::refusing sibling-files entry {name}")
            if not (parent / repo / name).exists():
                sys.exit(f"::error::{repo} has no {name}")
            target = dest / repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if (parent / repo / name).is_dir():
                shutil.copytree(parent / repo / name, target, ignore=shutil.ignore_patterns(".git"))
            else:
                shutil.copy2(parent / repo / name, target)


def main():
    if sys.version_info < (3, 11):
        sys.exit("::error::clone-siblings needs Python 3.11 or newer (tomllib)")
    env = os.environ
    prefix, fallback_ref = env["PREFIX"], env["FALLBACK_REF"]
    if not re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)?", prefix):
        sys.exit(f"::error::refusing sibling-prefix {prefix!r}")
    source = env.get("SOURCE_REPO", "")
    if source and not re.fullmatch(NAME, source):
        sys.exit(f"::error::refusing source-repo {source!r}")

    workspace = pathlib.Path(env["WORKSPACE"])
    parent = workspace.parent

    def clone_one(repo):
        clone(repo, parent, env["OWNER"], env["BASE_REF"], fallback_ref)

    listed = env.get("SIBLING_REPOS", "")
    if env.get("SIBLING_REPOS_FILE"):
        repos_file = workspace / env["SIBLING_REPOS_FILE"]
        if not repos_file.is_file():
            sys.exit(f"::error::sibling-repos-file {env['SIBLING_REPOS_FILE']} not found")
        listed += "\n" + repos_file.read_text()
    cloned = clone_closure(workspace, prefix, clone_one, listed_repos(listed))
    with open(env["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"repos={' '.join(cloned)}\n")

    if env.get("STAGE_DIR"):
        if not cloned:
            sys.exit("::error::no sibling repositories found to stage")
        stage(cloned, parent, pathlib.Path(env["STAGE_DIR"]), env["STAGE_FILES"].split())
        if source in cloned:  # a dispatch from a dependency: its commit goes into the image tag
            sha = subprocess.check_output(["git", "-C", str(parent / source), "rev-parse", "--short", "HEAD"], text=True)
            with open(env["GITHUB_ENV"], "a") as fh:
                fh.write(f"SUBMODULE_SHA=-{sha.strip()}\n")


if __name__ == "__main__":
    main()
