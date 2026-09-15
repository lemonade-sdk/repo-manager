"""What every command is handed: the state directory, the repo, and a clone to read git from."""

import os
from datetime import datetime, timezone

from repo_manager import gitops
from repo_manager.store import Store


class Context:
    def __init__(self, args):
        self.args = args
        self.repo = args.repo or os.environ.get("REPO_MANAGER_REPO", "")
        self.store = Store(args.state, push=not args.no_push)
        self.checkout_path = args.checkout
        self.force = bool(getattr(args, "force", False))
        self._checkout = None
        self._tags = None

    def require_repo(self):
        if not self.repo:
            raise SystemExit(
                "No repository given. Pass --repo OWNER/REPO or set REPO_MANAGER_REPO."
            )
        return self.repo

    def checkout(self):
        if self._checkout is None:
            self._checkout = gitops.open_checkout(self.require_repo(), self.checkout_path)
        return self._checkout

    def tags(self):
        if self._tags is None:
            self._tags = self.checkout().tags()
        return self._tags

    def now_iso(self):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def now(self):
        return datetime.now(timezone.utc)

    def describe_store(self):
        where = "push" if self.store.pushes else ("commit only" if self.store.is_git else "files only")
        return f"{self.store.root} ({where})"
