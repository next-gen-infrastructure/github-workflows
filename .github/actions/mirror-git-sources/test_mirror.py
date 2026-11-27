"""Tests for mirror.py. Run: uv run --no-project --with pytest pytest .github/actions/mirror-git-sources"""
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import mirror as mi  # noqa: E402

UV_LOCK = """
version = 1

[[package]]
name = "osint-private"
version = "0.1.0"
source = { git = "https://github.com/acme/osint-private?rev=main#0123456789abcdef" }

[[package]]
name = "tool"
version = "1.0"
source = { git = "https://github.com/other/tool.git#abcdef" }

[[package]]
name = "gitlab-thing"
version = "1.0"
source = { git = "https://gitlab.com/acme/thing?rev=v1#abcdef" }

[[package]]
name = "requests"
version = "2.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "local"
version = "0.1"
source = { editable = "." }
"""


def test_from_uv_lock_takes_github_git_sources_only(capsys):
    assert mi.from_uv_lock(UV_LOCK) == {"acme/osint-private", "other/tool"}
    assert "gitlab.com" in capsys.readouterr().out  # a source that cannot be mirrored is reported, not silent


def test_from_uv_lock_without_git_sources():
    assert mi.from_uv_lock('version = 1\n[[package]]\nname = "a"\nsource = { registry = "https://pypi.org/simple" }\n') == set()


def test_git_sources_reads_the_lock_file_it_finds(tmp_path):
    (tmp_path / "uv.lock").write_text(UV_LOCK)
    assert mi.git_sources(tmp_path) == ["acme/osint-private", "other/tool"]


def test_git_sources_fails_without_a_supported_lock_file(tmp_path):
    (tmp_path / "package-lock.json").write_text("{}")
    with pytest.raises(SystemExit, match="no supported lock file found .*uv.lock"):
        mi.git_sources(tmp_path)


def test_git_sources_adds_up_every_lock_file_found(tmp_path, monkeypatch):
    (tmp_path / "uv.lock").write_text(UV_LOCK)
    (tmp_path / "other.lock").write_text("x")
    monkeypatch.setitem(mi.LOCK_READERS, "other.lock", lambda text: {"acme/from-other"})
    assert mi.git_sources(tmp_path) == ["acme/from-other", "acme/osint-private", "other/tool"]


def test_mirror_clones_each_repo_bare_into_owner_dirs_named_dot_git(tmp_path):
    calls = []

    def fake_git(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    mi.mirror(["acme/a", "other/b"], tmp_path, fake_git)
    assert sorted(calls) == [
        ["git", "clone", "-q", "--bare", "https://github.com/acme/a", str(tmp_path / "acme/a.git")],
        ["git", "clone", "-q", "--bare", "https://github.com/other/b", str(tmp_path / "other/b.git")],
    ]


def test_mirror_fails_when_a_clone_fails(tmp_path):
    with pytest.raises(SystemExit, match="cannot mirror acme/a"):
        mi.mirror(["acme/a"], tmp_path, lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 128))
