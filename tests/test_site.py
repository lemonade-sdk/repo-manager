import unittest

from repo_manager import site, store
from repo_manager.store import Store
from tests.helpers import TempDirCase


def seed(state):
    state.write_json("commits/" + "a" * 40 + ".json", {
        "sha": "a" * 40, "repo": "lemonade-sdk/lemonade", "bucket": "v2026.38",
        "pr_number": 3456, "author": "@someone", "summary": "Adds the Moonshine backend.",
        "verdict": "Needs Attention", "verdict_reason": "No test evidence on macOS.",
        "committed_at": "2026-09-12T10:00:00Z", "reviewed_at": "2026-09-12T11:00:00Z",
        "maintainer_todos": [{"text": "Run Moonshine on macOS."}],
        "evidence": {"tests": "Unit tests only."},
    })
    state.write_json(store.review_key("v2026.38"), {
        "bucket": "v2026.38", "branch": "release-v2026.38", "range_start": "v2026.37.1",
        "head_sha": "b" * 40,
        "checklist": [{"priority": "P1", "platforms": ["macOS"],
                       "text": "Run Moonshine on macOS (#3456, @someone)."}],
        "breaking_changes": [], "candidate_issues": [],
        "evidence": {"coverage": "All 1 commit reviewed."},
    })
    state.write_text(store.notes_key("v2026.38"), "## Headline\n\n- Moonshine.\n\n## Breaking Changes\n")
    # Record what repo-manager wrote, the way build_* does, so nothing reads as hand-edited.
    for filename in ("review.json", "notes.md"):
        store.record_generated(
            state, "v2026.38", filename, state.read_text(f"releases/v2026.38/{filename}")
        )
    state.write_json("prs/3500.json", {
        "pr_number": 3500, "title": "Add a --bar flag", "author": "@other",
        "url": "https://github.com/lemonade-sdk/lemonade/pull/3500", "state": "OPEN",
        "reviewed_at": "2026-09-14T09:00:00Z",
        "outputs": {"label": "rfc:required", "scope_display": "needs-rfc",
                    "body_matches_diff": "no", "docs_and_tests": "gaps",
                    "suggested_reviewers": ["@someone"]},
        "explanation": {"diff": "Adds a flag."},
        "facts": {"surfaces": [], "breaking_changes": []}, "cover": {}, "quality": {},
    })


class LoadFromFiles(TempDirCase):
    def setUp(self):
        super().setUp()
        self.state = Store(self.tmp / "state")
        seed(self.state)
        self.data = site.load(self.state)

    def test_everything_comes_from_the_directory(self):
        counts = self.data["counts"]
        self.assertEqual(counts["commits"], 1)
        self.assertEqual(counts["pr_reviews"], 1)
        self.assertEqual(counts["release_reviews"], 1)
        self.assertEqual(counts["announcements"], 1)
        self.assertEqual(counts["verdicts"], {"Needs Attention": 1})
        self.assertEqual(counts["blockers"], 0)

    def test_the_repo_is_read_back_from_a_stored_review(self):
        self.assertEqual(self.data["config"]["repo"], "lemonade-sdk/lemonade")

    def test_a_bucket_carries_its_plan_and_artifacts(self):
        """One release, one row: the checklist and both artifacts together."""
        bucket = self.data["releases"][0]
        self.assertEqual(bucket["bucket"], "v2026.38")
        self.assertNotIn("verdict", bucket)
        self.assertEqual(bucket["todo_items"][0]["platforms"], ["macOS"])
        self.assertEqual(bucket["commits"], 1)
        self.assertIn("## Headline", bucket["notes_markdown"])
        self.assertEqual(self.data["buckets"], ["v2026.38"])

    def test_a_bucket_with_only_commits_is_still_a_release(self):
        """The bucket on `main` before anyone has built it. Leaving it out of the list is
        how the old page managed to have a release filter that was empty after a sweep."""
        self.state.write_json("commits/" + "c" * 40 + ".json", {
            "sha": "c" * 40, "repo": "lemonade-sdk/lemonade", "bucket": "v2026.39",
            "author": "@someone", "summary": "Later work.", "verdict": "Clean",
            "committed_at": "2026-09-20T10:00:00Z",
        })
        data = site.load(self.state)
        upcoming = data["releases"][0]
        self.assertEqual(upcoming["bucket"], "v2026.39")
        self.assertFalse(upcoming["reviewed"])
        self.assertEqual(upcoming["todo_items"], [])
        self.assertEqual(upcoming["commits"], 1)
        self.assertEqual(data["counts"]["releases"], 2)
        self.assertEqual(data["counts"]["release_reviews"], 1)

    def test_validation_notes_reach_every_kind_of_row(self):
        commit = self.state.read_json("commits/" + "a" * 40 + ".json")
        commit["validation_notes"] = ["Names a source file."]
        self.state.write_json("commits/" + "a" * 40 + ".json", commit)
        pr = self.state.read_json("prs/3500.json")
        pr["validation_notes"] = ["The cover names no group."]
        self.state.write_json("prs/3500.json", pr)
        review = self.state.read_json(store.review_key("v2026.38"))
        review["validation_notes"] = ["An id was left unrated."]
        self.state.write_json(store.review_key("v2026.38"), review)
        store.record_generated(self.state, "v2026.38", "review.json",
                               self.state.read_text(store.review_key("v2026.38")))
        store.record_generated(self.state, "v2026.38", "notes.md",
                               self.state.read_text(store.notes_key("v2026.38")), ["Too long."])
        data = site.load(self.state)
        self.assertEqual(data["commit_reviews"][0]["validation_notes"], ["Names a source file."])
        self.assertEqual(data["pr_reviews"][0]["validation_notes"], ["The cover names no group."])
        self.assertEqual(data["releases"][0]["validation_notes"],
                         {"review.json": ["An id was left unrated."], "notes.md": ["Too long."]})

    def test_a_row_with_nothing_to_note_says_so_rather_than_omitting_the_field(self):
        self.assertEqual(self.data["commit_reviews"][0]["validation_notes"], [])
        self.assertEqual(self.data["pr_reviews"][0]["validation_notes"], [])
        self.assertEqual(self.data["releases"][0]["validation_notes"], {"review.json": []})

    def test_a_hand_edited_artifact_shows_as_frozen(self):
        self.state.write_text(store.notes_key("v2026.38"), "## Headline\n\n- edited by hand\n")
        self.assertEqual(site.load(self.state)["releases"][0]["frozen"], ["notes.md"])

    def test_blockers_lead_the_to_do_list_and_are_counted(self):
        review = self.state.read_json(store.review_key("v2026.38"))
        review["checklist"].append({"priority": "P2", "text": "Tidy the imports."})
        review["checklist"].append({"priority": "P0", "text": "Fix the installer."})
        self.state.write_json(store.review_key("v2026.38"), review)
        data = site.load(self.state)
        self.assertEqual(data["counts"]["blockers"], 1)
        self.assertEqual(
            [t["priority"] for t in data["releases"][0]["todo_items"]], ["P0", "P1", "P2"]
        )

    def test_a_checklist_item_carries_the_commit_it_came_from(self):
        """Which is what makes it the same checkbox as the commit review's own to-do: the
        page keys the box on the commit, not on the list it is being read in."""
        review = self.state.read_json(store.review_key("v2026.38"))
        review["checklist"][0].update(
            {"commit": "a" * 40, "pr_number": 3456, "author": "@someone"})
        self.state.write_json(store.review_key("v2026.38"), review)
        item = site.load(self.state)["releases"][0]["todo_items"][0]
        self.assertEqual(item["commit"], "a" * 40)
        self.assertEqual(item["pr_number"], 3456)
        self.assertEqual(item["author"], "@someone")

    def test_a_commit_to_do_names_no_commit_of_its_own(self):
        # It is already filed under one; the field exists so a release item can point back.
        self.assertEqual(self.data["commit_reviews"][0]["todo_items"][0]["commit"], "")

    def test_an_unreadable_file_is_skipped_rather_than_crashing_the_page(self):
        self.state.write_text("commits/broken.json", "{ this is not json")
        self.assertEqual(site.load(self.state)["counts"]["commits"], 1)

    def test_without_a_mirror_no_pr_claims_a_live_status(self):
        # `site render` has no network, so the Status column must not be drawn from stale
        # data as though it were fresh. The page shows "not live" instead.
        row = self.data["pr_reviews"][0]
        self.assertFalse(row["state_known"])
        self.assertEqual(row["review_status"], "")
        self.assertEqual(row["attention"], "elevated")

    def test_a_commit_is_filed_under_its_bucket(self):
        self.assertEqual(self.data["commit_reviews"][0]["bucket"], "v2026.38")

    def test_the_commit_evidence_is_ordered_for_reading_not_alphabetically(self):
        self.assertEqual(list(self.data["commit_reviews"][0]["evidence"]), ["tests"])


class Rendering(TempDirCase):
    def setUp(self):
        super().setUp()
        self.state = Store(self.tmp / "state")
        seed(self.state)

    def test_render_writes_a_self_contained_page(self):
        index = site.render(self.state, self.tmp / "out")
        html = index.read_text(encoding="utf-8")
        self.assertIn("window.REPO_MANAGER_STATIC = true", html)
        self.assertIn("REPO_MANAGER_STATIC_DATA", html)
        self.assertIn("Moonshine", html)

    def test_embedded_review_text_cannot_close_the_script_element(self):
        data = self.state.read_json("prs/3500.json")
        data["title"] = "</script><script>alert(1)</script>"
        self.state.write_json("prs/3500.json", data)
        html = site.render(self.state, self.tmp / "out").read_text(encoding="utf-8")
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("\\u003c/script\\u003e", html)

    def test_rendering_an_empty_directory_still_produces_a_page(self):
        index = site.render(Store(self.tmp / "empty"), self.tmp / "empty-out")
        self.assertIn("REPO_MANAGER_STATIC_DATA", index.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
