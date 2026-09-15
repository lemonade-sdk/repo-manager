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
        "bucket": "v2026.38", "branch": "release-v2026.38", "verdict": "Needs Attention",
        "verdict_reason": "Moonshine is unverified on macOS.", "range_start": "v2026.37.1",
        "head_sha": "b" * 40,
        "prioritized_todos": [{"priority": "P1", "text": "Run Moonshine on macOS (#3456, @someone)."}],
        "breaking_changes": [], "candidate_issues": [],
        "tester_plan": [{"platform": "macOS", "changed": "Moonshine", "exercise": "Transcribe a clip."}],
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
        self.assertEqual(self.data["counts"], {
            "commits": 1, "prs": 1, "releases": 1,
            "verdicts": {"Needs Attention": 1}, "labels": {"rfc:required": 1}, "blockers": 0,
        })

    def test_the_repo_is_read_back_from_a_stored_review(self):
        self.assertEqual(self.data["repo"], "lemonade-sdk/lemonade")

    def test_a_bucket_carries_its_verdict_plan_and_artifacts(self):
        bucket = self.data["releases"][0]
        self.assertEqual(bucket["bucket"], "v2026.38")
        self.assertEqual(bucket["verdict"], "Needs Attention")
        self.assertEqual(bucket["tester_plan"][0]["platform"], "macOS")
        self.assertIn("## Headline", bucket["notes"])
        self.assertEqual(bucket["commits"], 1)

    def test_a_hand_edited_artifact_shows_as_frozen(self):
        self.state.write_text(store.notes_key("v2026.38"), "## Headline\n\n- edited by hand\n")
        self.assertEqual(site.load(self.state)["releases"][0]["frozen"], ["notes.md"])

    def test_p0_todos_are_counted_as_open_blockers(self):
        review = self.state.read_json(store.review_key("v2026.38"))
        review["prioritized_todos"].append({"priority": "P0", "text": "Fix the installer."})
        self.state.write_json(store.review_key("v2026.38"), review)
        self.assertEqual(site.load(self.state)["counts"]["blockers"], 1)

    def test_an_unreadable_file_is_skipped_rather_than_crashing_the_page(self):
        self.state.write_text("commits/broken.json", "{ this is not json")
        self.assertEqual(site.load(self.state)["counts"]["commits"], 1)


class Rendering(TempDirCase):
    def setUp(self):
        super().setUp()
        self.state = Store(self.tmp / "state")
        seed(self.state)

    def test_render_writes_a_self_contained_page(self):
        index = site.render(self.state, self.tmp / "out")
        html = index.read_text(encoding="utf-8")
        self.assertIn("window.REPO_MANAGER_STATIC = true", html)
        self.assertIn("Moonshine", html)
        self.assertNotIn("http://", html.split("<script>")[0])

    def test_embedded_review_text_cannot_close_the_script_element(self):
        data = self.state.read_json("prs/3500.json")
        data["title"] = "</script><script>alert(1)</script>"
        self.state.write_json("prs/3500.json", data)
        html = site.render(self.state, self.tmp / "out").read_text(encoding="utf-8")
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("\\u003c/script\\u003e", html)

    def test_rendering_an_empty_directory_still_produces_a_page(self):
        index = site.render(Store(self.tmp / "empty"), self.tmp / "empty-out")
        self.assertIn("REPO_MANAGER_DATA", index.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
