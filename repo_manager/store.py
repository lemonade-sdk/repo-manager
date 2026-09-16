"""The state directory: one file per artifact, and git as the concurrency control.

There is one store, everywhere. In production it is a clone of `lemonade-testing`; on a
laptop it is the same clone, or an empty directory for a scratch run. Nothing branches on
environment: pushing is a flag, not a mode.

Keys are relative POSIX paths inside the directory (`commits/<sha>.json`,
`releases/v2026.38/notes.md`). Writers commit after each artifact rather than at the end,
so a cancelled run loses at most the review it was in the middle of.
"""

import hashlib
import json
import subprocess
from pathlib import Path


PUSH_ATTEMPTS = 5


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def dumps(data):
    """The one JSON shape the store writes: pretty, sorted, newline-terminated.

    Sorted keys are not cosmetic — every artifact lives in a git history a human reads as a
    diff, and a key order that follows whatever the model happened to emit turns a one-field
    change into a whole-file rewrite.
    """
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class Store:
    def __init__(self, root, push=True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.is_git = (self.root / ".git").exists()
        self.branch = self._branch() if self.is_git else ""
        self.has_origin = self.is_git and self._has_origin()
        self.pushes = bool(push and self.has_origin)

    # --- git ---------------------------------------------------------------------------

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], text=True, capture_output=True, check=False
        )

    def _checked(self, args):
        result = self._git(*args)
        if result.returncode != 0:
            raise StoreError(
                f"git {' '.join(args)} failed in {self.root}:\n{(result.stderr or result.stdout).strip()}"
            )
        return result

    def _branch(self):
        branch = (self._git("rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip()
        return "" if branch in ("", "HEAD") else branch

    def _has_origin(self):
        result = self._git("remote", "get-url", "origin")
        return result.returncode == 0 and bool((result.stdout or "").strip())

    # --- paths -------------------------------------------------------------------------

    def path(self, key):
        path = (self.root / key).resolve()
        if self.root not in path.parents and path != self.root:
            raise StoreError(f"Key escapes the state directory: {key}")
        return path

    def exists(self, key):
        return self.path(key).exists()

    def read_text(self, key):
        path = self.path(key)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def read_json(self, key):
        text = self.read_text(key)
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def keys(self, prefix, suffix=""):
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(
            str(path.relative_to(self.root).as_posix())
            for path in base.rglob(f"*{suffix}")
            if path.is_file()
        )

    # --- writing -----------------------------------------------------------------------

    def write_text(self, key, text):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def write_json(self, key, data):
        return self.write_text(key, dumps(data))

    def save(self, keys, message):
        """Commit (and push) files already written. A no-op outside a git checkout."""
        keys = [keys] if isinstance(keys, str) else list(keys)
        if not self.is_git or not keys:
            return False
        self._checked(["add", "--", *keys])
        staged = self._git("diff", "--cached", "--quiet", "--", *keys)
        if staged.returncode == 0:
            return False
        self._checked(["commit", "-m", message])
        if self.pushes:
            self._push()
        return True

    def put_json(self, key, data, message=""):
        self.write_json(key, data)
        self.save([key], message or describe([key]))
        return self.path(key)

    def put_text(self, key, text, message=""):
        self.write_text(key, text)
        self.save([key], message or describe([key]))
        return self.path(key)

    def _push(self):
        """Push, and on rejection rebase onto the remote and try again.

        A push is atomic, which is the whole reason state lives in git: jobs writing
        disjoint paths never conflict, and two jobs racing for the same bucket serialize
        here instead of corrupting each other. `origin/<branch>` rather than a hardcoded
        `origin/main` so a scratch branch or a fork of the state repo rebases onto itself.
        """
        branch = self.branch or "main"
        last = ""
        for attempt in range(1, PUSH_ATTEMPTS + 1):
            result = self._git("push", "origin", f"HEAD:{branch}")
            if result.returncode == 0:
                return
            last = (result.stderr or result.stdout or "").strip()
            if attempt == PUSH_ATTEMPTS:
                break
            print(f"Push rejected (attempt {attempt}); rebasing onto origin/{branch}.", flush=True)
            self._checked(["fetch", "origin", branch])
            rebase = self._git("rebase", f"origin/{branch}")
            if rebase.returncode != 0:
                self._git("rebase", "--abort")
                raise StoreError(
                    f"Could not rebase the state directory onto origin/{branch}:\n"
                    + (rebase.stderr or rebase.stdout or "").strip()
                )
        raise StoreError(f"Could not push the state directory after {PUSH_ATTEMPTS} attempts:\n{last}")


class StoreError(RuntimeError):
    pass


def describe(keys):
    """A commit message that names what changed: `commits: abc1234`, `releases/v2026.38: review, notes`."""
    keys = [keys] if isinstance(keys, str) else list(keys)
    if not keys:
        return "repo-manager: no change"
    groups = {}
    for key in keys:
        parent = str(Path(key).parent.as_posix())
        name = Path(key).stem
        if parent == "commits":
            name = name[:7]
        groups.setdefault(parent, []).append(name)
    return "; ".join(f"{parent}: {', '.join(names)}" for parent, names in groups.items())


# --- keys ------------------------------------------------------------------------------


def commit_key(sha):
    return f"commits/{sha}.json"


def pr_key(number):
    return f"prs/{number}.json"


def bucket_dir(bucket):
    return f"releases/{bucket}"


def review_key(bucket):
    return f"releases/{bucket}/review.json"


def notes_key(bucket):
    return f"releases/{bucket}/notes.md"


def announcement_key(bucket):
    return f"releases/{bucket}/announcement.md"


def candidate_key(bucket, number):
    return f"releases/{bucket}/candidates/{number}.md"


def generated_key(bucket):
    return f"releases/{bucket}/generated.json"


# --- the human-edit rule ---------------------------------------------------------------
#
# repo-manager records the SHA-256 of everything it writes into a bucket. A file whose
# content no longer matches that hash was edited by the release admin, and a human edit is
# authoritative: `release build` says so and leaves the file alone. `--force` overrules it
# and re-records the hash. A file that exists with no recorded hash is frozen too — nothing
# repo-manager wrote is missing from the ledger, so it came from somewhere else.


VALIDATION_NOTES = "validation_notes"


def generated_hashes(store, bucket):
    return store.read_json(generated_key(bucket)) or {}


def validation_notes(store, bucket):
    """{filename: [problems the last attempt still had]}, for the bucket's Markdown files."""
    return generated_hashes(store, bucket).get(VALIDATION_NOTES) or {}


def is_frozen(store, bucket, filename):
    key = f"releases/{bucket}/{filename}"
    if not store.exists(key):
        return False
    recorded = generated_hashes(store, bucket).get(filename)
    return sha256_text(store.read_text(key)) != recorded


def record_generated(store, bucket, filename, content, notes=None):
    hashes = generated_hashes(store, bucket)
    hashes[filename] = sha256_text(content)
    recorded = hashes.get(VALIDATION_NOTES) or {}
    recorded.pop(filename, None)
    if notes:
        recorded[filename] = list(notes)
    if recorded:
        hashes[VALIDATION_NOTES] = recorded
    else:
        hashes.pop(VALIDATION_NOTES, None)
    store.write_json(generated_key(bucket), hashes)
    return generated_key(bucket)
