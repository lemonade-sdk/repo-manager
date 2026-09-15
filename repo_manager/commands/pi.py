"""`repo-manager pi ...` — making a fresh machine able to run the skills."""

import json
import os
import shutil
from pathlib import Path

from repo_manager import PI_VERSION
from repo_manager.pi import package_root


def add_parser(sub, shared):
    parser = sub.add_parser("pi", help="Set up pi, and report the version repo-manager wants.")
    nouns = parser.add_subparsers(dest="verb", required=True)

    setup = nouns.add_parser(
        "setup", parents=[shared],
        help="Install the bundled skills and point pi at a Lemonade server. Idempotent.",
    )
    setup.set_defaults(func=cmd_setup)

    version = nouns.add_parser("version", parents=[shared], help="Print the pinned pi version.")
    version.set_defaults(func=cmd_version)


def config_dir():
    return Path(os.environ.get("HOME", Path.home())) / ".pi" / "agent"


def cmd_setup(args):
    """Write pi's provider config and install this package's skills under $HOME.

    The workflows run this with `HOME` pointed at the job workspace, so a shared runner's real
    home directory is never touched. Everything it writes is derived from two environment
    variables, so switching model or server is a one-line change in the caller.
    """
    url = os.environ.get("REPO_MANAGER_LEMONADE_URL", "")
    model = os.environ.get("REPO_MANAGER_PI_MODEL", "")
    if not url:
        raise SystemExit(
            "REPO_MANAGER_LEMONADE_URL is not set. Point it at a Lemonade server's "
            "OpenAI-compatible endpoint, for example http://127.0.0.1:13305/v1."
        )
    model_id = model.split("/", 1)[1] if "/" in model else model
    if not model_id:
        raise SystemExit(
            "REPO_MANAGER_PI_MODEL is not set. Use Lemonade/<model>, for example "
            "Lemonade/Qwen3.6-35B-A3B-MTP-GGUF."
        )

    target = config_dir()
    target.mkdir(parents=True, exist_ok=True)

    models = read_json(target / "models.json")
    providers = models.setdefault("providers", {})
    lemonade = providers.setdefault("Lemonade", {})
    lemonade.update({"api": "openai-completions", "apiKey": "lemonade", "baseUrl": url})
    listed = lemonade.setdefault("models", [])
    if not any(entry.get("id") == model_id for entry in listed if isinstance(entry, dict)):
        listed.append({"id": model_id})
    write_json(target / "models.json", models)

    package = target / "packages" / "repo-manager"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    for name in ("skills", "scripts"):
        source = package_root() / name
        if source.exists():
            shutil.copytree(source, package / name)

    settings = read_json(target / "settings.json")
    settings["defaultProvider"] = "Lemonade"
    settings["defaultModel"] = model_id
    packages = [p for p in settings.get("packages", []) if "repo-manager" not in str(p)]
    packages.append(str(package))
    settings["packages"] = packages
    write_json(target / "settings.json", settings)

    print(f"Wrote {target / 'models.json'} and {target / 'settings.json'}")
    print(f"Provider Lemonade at {url}, default model {model_id}")
    print(f"Installed skills and scripts into {package}")
    return 0


def read_json(path):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def cmd_version(args):
    print(PI_VERSION)
    return 0
