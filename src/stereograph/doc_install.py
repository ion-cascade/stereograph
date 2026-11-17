"""Install the pinned Blender documentation corpus without bundling upstream code."""

from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

CORPUS_TAG = "v1.0.3"
CORPUS_COMMIT = "2cea8d566dde07fbac28a61d698909d69724e853"
CORPUS_LABEL = "Blender Lab MCP 1.0.3 reference snapshot"
CORPUS_REPO = "https://projects.blender.org/lab/blender_mcp.git"


def default_directory():
    return Path.home() / ".stereograph" / "docs" / CORPUS_TAG


def is_installed(directory):
    pin = directory / ".source-commit"
    return (pin.is_file() and pin.read_text(encoding="ascii").strip() == CORPUS_COMMIT
            and all((directory / name / "index.rst").is_file() for name in ("api", "manual")))


def install(directory):
    directory = directory.expanduser().resolve()
    if is_installed(directory):
        return directory
    if directory.exists():
        raise FileExistsError(f"Documentation destination already exists: {directory}")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="stereograph-docs-", dir=directory.parent) as scratch:
        work = Path(scratch)
        source = work / "source"
        subprocess.run(["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1", "--branch", CORPUS_TAG,
                        CORPUS_REPO, str(source)], check=True, timeout=120)
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                         text=True, timeout=10).strip()
        if commit != CORPUS_COMMIT:
            raise ValueError(f"Expected Blender documentation commit {CORPUS_COMMIT}, got {commit}")
        staged = work / "corpus"
        staged.mkdir()
        docs = source / "mcp" / "blmcp" / "data"
        for name in ("api", "manual"):
            shutil.copytree(docs / name, staged / name)
        (staged / ".source-commit").write_text(f"{commit}\n", encoding="ascii")
        if not is_installed(staged):
            raise ValueError("The source did not contain both Blender documentation collections.")
        try:
            staged.rename(directory)
        except OSError:
            if not is_installed(directory):
                raise
    return directory
