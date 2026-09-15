import re
from pathlib import Path

from setuptools import find_packages, setup


def metadata(name):
    """Read a constant from the package without importing it."""
    text = Path("repo_manager/__init__.py").read_text(encoding="utf-8")
    match = re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"{name} is not defined in repo_manager/__init__.py")
    return match.group(1)


def data_files():
    """Skills and scripts ship alongside the package; `pi setup` copies them from here."""
    files = []
    for root in ("skills", "scripts"):
        for path in Path(root).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            files.append((str(Path("share") / "repo-manager" / path.parent), [str(path)]))
    return files


setup(
    name="repo-manager",
    version=metadata("__version__"),
    description="Commit reviews, PR triage, and release artifacts for GitHub projects, stored as files.",
    long_description=Path("README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    url="https://github.com/lemonade-sdk/repo-manager",
    license="MIT",
    packages=find_packages(include=["repo_manager", "repo_manager.*"]),
    package_data={"repo_manager": ["dashboard.html"]},
    include_package_data=True,
    data_files=data_files(),
    python_requires=">=3.9",
    entry_points={"console_scripts": ["repo-manager=repo_manager.cli:main"]},
)

# The `pi` release the skills are tuned against is pinned beside the version, in
# repo_manager/__init__.py, and read back by `repo-manager pi version`. Consuming workflows
# install that exact version, so the agent that writes an artifact is the one it was tuned on.
PI_VERSION = metadata("PI_VERSION")
