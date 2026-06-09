from pathlib import Path

from setuptools import find_packages, setup


def data_files():
    files = []
    for root in ("skills", "scripts", "db"):
        for path in Path(root).rglob("*"):
            if path.is_file():
                files.append((str(Path("share") / "repo-manager" / path.parent), [str(path)]))
    return files


setup(
    name="repo-manager",
    version="0.1.0",
    description="CLI and Pi skills for managing GitHub projects at scale.",
    packages=find_packages(),
    include_package_data=True,
    data_files=data_files(),
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "repo-manager=repo_manager.cli:main",
        ],
    },
)
