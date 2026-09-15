import json
import unittest

from repo_manager import store
from repo_manager.store import Store
from tests.helpers import TempDirCase, commit_file, git, init_repo


class PlainDirectory(TempDirCase):
    def test_a_directory_with_no_git_just_holds_files(self):
        state = Store(self.tmp / "state")
        state.put_json("commits/abc.json", {"sha": "abc"})
        self.assertFalse(state.is_git)
        self.assertFalse(state.pushes)
        self.assertEqual(state.read_json("commits/abc.json"), {"sha": "abc"})

    def test_json_is_pretty_sorted_and_newline_terminated(self):
        state = Store(self.tmp / "state")
        state.write_json("commits/abc.json", {"b": 2, "a": 1})
        text = (state.root / "commits" / "abc.json").read_text()
        self.assertEqual(text, '{\n  "a": 1,\n  "b": 2\n}\n')

    def test_missing_keys_read_as_empty_rather_than_raising(self):
        state = Store(self.tmp / "state")
        self.assertIsNone(state.read_json("commits/nothing.json"))
        self.assertEqual(state.read_text("commits/nothing.json"), "")

    def test_a_key_cannot_escape_the_state_directory(self):
        state = Store(self.tmp / "state")
        with self.assertRaises(store.StoreError):
            state.path("../outside.json")

    def test_keys_lists_what_is_there(self):
        state = Store(self.tmp / "state")
        state.write_json("commits/a.json", {})
        state.write_json("commits/b.json", {})
        state.write_text("releases/v2026.38/notes.md", "x")
        self.assertEqual(state.keys("commits", ".json"), ["commits/a.json", "commits/b.json"])
        self.assertEqual(state.keys("releases"), ["releases/v2026.38/notes.md"])


class Committing(TempDirCase):
    def setUp(self):
        super().setUp()
        init_repo(self.tmp / "state")
        self.state = Store(self.tmp / "state")

    def last_message(self):
        return git(self.state.root, "log", "-1", "--format=%s").stdout.strip()

    def test_a_write_becomes_a_commit(self):
        self.assertTrue(self.state.is_git)
        self.state.put_json("commits/abcdef1234.json", {"sha": "abcdef1234"})
        self.assertEqual(self.last_message(), "commits: abcdef1")
        self.assertEqual(git(self.state.root, "status", "--porcelain").stdout.strip(), "")

    def test_writing_the_same_content_twice_makes_one_commit(self):
        self.state.put_json("commits/abc.json", {"sha": "abc"})
        before = git(self.state.root, "rev-parse", "HEAD").stdout
        self.state.put_json("commits/abc.json", {"sha": "abc"})
        self.assertEqual(git(self.state.root, "rev-parse", "HEAD").stdout, before)

    def test_a_release_commit_names_the_bucket_and_its_files(self):
        self.state.write_text("releases/v2026.38/notes.md", "notes")
        self.state.write_json("releases/v2026.38/generated.json", {})
        self.state.save(
            ["releases/v2026.38/notes.md", "releases/v2026.38/generated.json"],
            store.describe(["releases/v2026.38/notes.md", "releases/v2026.38/generated.json"]),
        )
        self.assertEqual(self.last_message(), "releases/v2026.38: notes, generated")

    def test_no_origin_means_no_push_attempt(self):
        self.assertFalse(self.state.has_origin)
        self.assertFalse(self.state.pushes)


class PushRetry(TempDirCase):
    """A push that loses the race rebases onto the remote and goes again.

    This is the whole concurrency story: several runners write disjoint files at once, and
    git — not a lock — is what makes that safe.
    """

    def setUp(self):
        super().setUp()
        self.remote = self.tmp / "remote.git"
        self.remote.mkdir()
        git(self.remote, "init", "-q", "--bare", "-b", "main")
        # A first commit, so both clones share a history to diverge from.
        seed = init_repo(self.tmp / "seed")
        commit_file(seed, "README.md", "state\n")
        git(seed, "remote", "add", "origin", str(self.remote))
        git(seed, "push", "-q", "origin", "main")

    def clone(self, name):
        git(self.tmp, "clone", "-q", str(self.remote), str(self.tmp / name))
        path = self.tmp / name
        git(path, "config", "user.name", "Test")
        git(path, "config", "user.email", "test@example.com")
        return Store(path)

    def test_a_push_reaches_the_remote(self):
        state = self.clone("a")
        self.assertTrue(state.pushes)
        state.put_json("commits/a.json", {"sha": "a"})
        self.assertIn("commits/a.json", git(self.remote, "ls-tree", "-r", "--name-only", "main").stdout)

    def test_a_rejected_push_rebases_and_succeeds(self):
        mine = self.clone("mine")
        theirs = self.clone("theirs")
        # Another runner gets there first, so my push is rejected as non-fast-forward.
        theirs.put_json("commits/theirs.json", {"sha": "theirs"})
        mine.put_json("commits/mine.json", {"sha": "mine"})
        listing = git(self.remote, "ls-tree", "-r", "--name-only", "main").stdout
        self.assertIn("commits/mine.json", listing)
        self.assertIn("commits/theirs.json", listing)

    def test_push_gives_up_with_a_clear_error_when_the_remote_is_gone(self):
        state = self.clone("gone")
        git(state.root, "remote", "set-url", "origin", str(self.tmp / "nowhere.git"))
        with self.assertRaises(store.StoreError) as caught:
            state.put_json("commits/a.json", {"sha": "a"})
        self.assertIn("origin", str(caught.exception))


class HumanEdits(TempDirCase):
    """A file whose content no longer matches the hash repo-manager recorded was edited by
    the release admin, and a human edit wins."""

    def setUp(self):
        super().setUp()
        self.state = Store(self.tmp / "state")

    def write(self, filename, content):
        self.state.write_text(f"releases/v2026.38/{filename}", content)
        store.record_generated(self.state, "v2026.38", filename, content)

    def test_what_repo_manager_just_wrote_is_not_frozen(self):
        self.write("notes.md", "## Headline\n\n- a\n")
        self.assertFalse(store.is_frozen(self.state, "v2026.38", "notes.md"))

    def test_an_edited_file_is_frozen(self):
        self.write("notes.md", "## Headline\n\n- a\n")
        self.state.write_text("releases/v2026.38/notes.md", "## Headline\n\n- the admin's words\n")
        self.assertTrue(store.is_frozen(self.state, "v2026.38", "notes.md"))

    def test_regenerating_over_a_freeze_clears_it(self):
        self.write("notes.md", "a")
        self.state.write_text("releases/v2026.38/notes.md", "edited")
        self.assertTrue(store.is_frozen(self.state, "v2026.38", "notes.md"))
        self.write("notes.md", "regenerated with --force")
        self.assertFalse(store.is_frozen(self.state, "v2026.38", "notes.md"))

    def test_a_file_with_no_recorded_hash_is_frozen(self):
        # Nothing repo-manager wrote is missing from the ledger, so it came from a human.
        self.state.write_text("releases/v2026.38/announcement.md", "hand-written")
        self.assertTrue(store.is_frozen(self.state, "v2026.38", "announcement.md"))

    def test_a_file_that_does_not_exist_is_not_frozen(self):
        self.assertFalse(store.is_frozen(self.state, "v2026.38", "notes.md"))

    def test_freezing_one_file_leaves_the_others_writable(self):
        self.write("notes.md", "a")
        self.write("announcement.md", "b")
        self.state.write_text("releases/v2026.38/notes.md", "edited")
        self.assertTrue(store.is_frozen(self.state, "v2026.38", "notes.md"))
        self.assertFalse(store.is_frozen(self.state, "v2026.38", "announcement.md"))

    def test_the_ledger_is_json_a_human_can_read(self):
        self.write("notes.md", "a")
        recorded = json.loads((self.state.root / "releases/v2026.38/generated.json").read_text())
        self.assertEqual(set(recorded), {"notes.md"})
        self.assertEqual(recorded["notes.md"], store.sha256_text("a"))


if __name__ == "__main__":
    unittest.main()
