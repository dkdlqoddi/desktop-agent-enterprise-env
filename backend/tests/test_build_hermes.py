"""scripts/build-hermes.py keeps build/hermes-source at the commit runtime/hermes/manifest.json pins: cloned once, and
moved to a new pin by fetching its tag into the same checkout. A repository on this PC stands in for Hermes', so no
network is needed."""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture
def builder(monkeypatch, tmp_path):
    # Git as the tests set it, whatever this PC's own settings say (signing, hooks, the default branch).
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "Builder")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "builder@example.com")
    spec = importlib.util.spec_from_file_location("build_hermes", ROOT / "scripts" / "build-hermes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """Hermes' repository as a release tags it: v1.0.0, then v1.1.0 (an annotated tag) on a later commit."""
    repository = tmp_path / "upstream"
    repository.mkdir()
    git("init", "--quiet", "--initial-branch=main", cwd=repository)
    commits = {}
    for version, text in (("1.0.0", "first"), ("1.1.0", "second")):
        (repository / "agent.py").write_text(f"VERSION = {text!r}\n")
        git("add", "agent.py", cwd=repository)
        git("commit", "--quiet", "-m", f"release {version}", cwd=repository)
        if version == "1.1.0":
            git("tag", "-a", f"v{version}", "-m", f"Hermes {version}", cwd=repository)
        else:
            git("tag", f"v{version}", cwd=repository)
        commits[version] = git("rev-parse", "HEAD", cwd=repository)
    return repository, commits


def pin(builder, monkeypatch, repository, version, commit):
    monkeypatch.setattr(builder, "PIN", {"version": version, "repository": repository.as_uri(), "commit": commit})


def test_a_new_pin_is_fetched_into_the_checkout_of_the_last_one(builder, upstream, monkeypatch, tmp_path):
    repository, commits = upstream
    source = tmp_path / "build" / "hermes-source"
    pin(builder, monkeypatch, repository, "1.0.0", commits["1.0.0"])
    builder.checkout(source)
    assert builder.head(source) == commits["1.0.0"]
    # The manifest moves to the next release: no folder to delete by hand.
    pin(builder, monkeypatch, repository, "1.1.0", commits["1.1.0"])
    builder.checkout(source)
    assert builder.head(source) == commits["1.1.0"]
    assert (source / "agent.py").read_text() == "VERSION = 'second'\n"
    assert not builder.modified(source)
    # And back, as when a branch with the older pin is built again.
    pin(builder, monkeypatch, repository, "1.0.0", commits["1.0.0"])
    builder.checkout(source)
    assert builder.head(source) == commits["1.0.0"]


def test_a_tag_that_names_another_commit_than_the_manifest_stops_the_build(builder, upstream, monkeypatch, tmp_path):
    repository, commits = upstream
    source = tmp_path / "hermes-source"
    pin(builder, monkeypatch, repository, "1.0.0", commits["1.0.0"])
    builder.checkout(source)
    # The tag was moved, or the manifest names the wrong commit: the commit is checked as before, never trusted.
    pin(builder, monkeypatch, repository, "1.1.0", "0" * 40)
    with pytest.raises(RuntimeError, match="differs from"):
        builder.checkout(source)
    # A first clone is held to the same commit.
    with pytest.raises(RuntimeError, match="differs from"):
        builder.checkout(tmp_path / "fresh")


def test_a_pin_that_cannot_be_fetched_says_how_to_go_on(builder, upstream, monkeypatch, tmp_path):
    repository, commits = upstream
    source = tmp_path / "hermes-source"
    pin(builder, monkeypatch, repository, "1.0.0", commits["1.0.0"])
    builder.checkout(source)
    # A release the repository does not have (or a repository out of reach).
    pin(builder, monkeypatch, repository, "2.0.0", "f" * 40)
    with pytest.raises(RuntimeError, match="Could not fetch Hermes v2.0.0") as failure:
        builder.checkout(source)
    assert "Run npm run build:hermes again once the repository can be reached" in str(failure.value)
    assert "git checkout of that commit" in str(failure.value)
    # The checkout is left as it was.
    assert builder.head(source) == commits["1.0.0"]


def test_a_modified_checkout_is_never_moved(builder, upstream, monkeypatch, tmp_path):
    repository, commits = upstream
    source = tmp_path / "hermes-source"
    pin(builder, monkeypatch, repository, "1.0.0", commits["1.0.0"])
    builder.checkout(source)
    (source / "agent.py").write_text("VERSION = 'patched here'\n")
    with pytest.raises(RuntimeError, match="Hermes source checkout is modified"):
        builder.checkout(source)
    pin(builder, monkeypatch, repository, "1.1.0", commits["1.1.0"])
    with pytest.raises(RuntimeError, match="keep the changes elsewhere"):
        builder.checkout(source)
    assert builder.head(source) == commits["1.0.0"]
    assert (source / "agent.py").read_text() == "VERSION = 'patched here'\n"
