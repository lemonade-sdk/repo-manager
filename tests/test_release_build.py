"""The release pipeline's plumbing, with the model stubbed out.

What is being tested is everything around the model: which files get written, what the
commits say, that a hash is recorded with every write, and that a human edit survives a
rebuild. The model's own output is stubbed, because its quality is what the validators in
test_validators.py are for.
"""

import unittest
from unittest import mock

from repo_manager import release, store
from repo_manager.context import Context
from repo_manager.store import Store
from tests.helpers import TempDirCase, commit_file, git, init_repo


NOTES = "## Headline\n\n- One.\n- Two.\n- Three.\n\n## Breaking Changes\n"
POST = "## Lemonade v2026.39\n\n@everyone three good things landed.\n"
REVIEW = {
    "checklist": [],
    "breaking_changes": [],
    "evidence": {key: "none observed" for key in release.EVIDENCE_KEYS},
}


class Args:
    def __init__(self, state, checkout, repo="lemonade-sdk/lemonade"):
        self.state, self.checkout, self.repo = str(state), str(checkout), repo
        self.no_push = True
        self.force = False


class BuildingABucket(TempDirCase):
    def setUp(self):
        super().setUp()
        asked = mock.patch.object(release.github, "requests_answered", return_value={})
        asked.start()
        self.addCleanup(asked.stop)
        self.source = init_repo(self.tmp / "source")
        self.first = commit_file(self.source, "a.txt", "one\n", "first")
        git(self.source, "tag", "v11.9.0")
        self.second = commit_file(self.source, "b.txt", "two\n", "second (#3456)")
        self.state = Store(self.tmp / "state")
        self.ctx = Context(Args(self.tmp / "state", self.source))
        self.ctx._checkout = release.gitops.Checkout(self.source, "lemonade-sdk/lemonade")
        self.state.write_json(store.commit_key(self.second), {
            "sha": self.second, "repo": "lemonade-sdk/lemonade", "bucket": "v2026.39",
            "pr_number": 3456, "author": "@someone", "summary": "Adds a thing.",
            "verdict": "Clean", "verdict_reason": "Fine.", "maintainer_todos": [],
            "shout_outs": [], "evidence": {"tests": "Covered."},
        })
        self.bucket = self.make_bucket()

    def make_bucket(self):
        with mock.patch.object(release.Bucket, "__init__", release.Bucket.__init__):
            bucket = release.Bucket.__new__(release.Bucket)
            bucket.ctx, bucket.branch = self.ctx, "main"
            bucket.checkout = self.ctx._checkout
            bucket.name = "v2026.39"
            bucket.tags = ["v11.9.0"]
            bucket.range_start = "v11.9.0"
            bucket.head = self.second
            bucket.stable_tags, bucket.is_hotfix, bucket.last_stable = [], False, ""
            bucket.cut, bucket.previous_head = "", ""
        return bucket

    def build_all(self, review=None, notes=NOTES, post=POST, force=False):
        answers = [release.normalize_review(dict(review or REVIEW)), notes, post]
        with mock.patch.object(release, "generate", side_effect=lambda *a, **k: answers.pop(0)), \
             mock.patch.object(release, "candidate_issues", return_value=[]):
            release.build_review(self.ctx, self.bucket, force=force)
            release.build_notes(self.ctx, self.bucket, force=force)
            release.build_announcement(self.ctx, self.bucket, force=force)

    def test_the_three_artifacts_are_written(self):
        self.build_all()
        for key in (store.review_key("v2026.39"), store.notes_key("v2026.39"),
                    store.announcement_key("v2026.39")):
            self.assertTrue(self.state.exists(key), key)

    def test_the_review_carries_the_facts_the_dashboard_renders_from(self):
        self.build_all()
        review = self.state.read_json(store.review_key("v2026.39"))
        self.assertNotIn("verdict", review)
        self.assertEqual(review["branch"], "main")
        self.assertEqual(review["range_start"], "v11.9.0")
        self.assertEqual(review["head_sha"], self.second)
        self.assertEqual(review["commits_reviewed"], 1)

    def test_every_write_records_its_hash(self):
        self.build_all()
        recorded = self.state.read_json(store.generated_key("v2026.39"))
        self.assertEqual(set(recorded), {"review.json", "notes.md", "announcement.md"})
        self.assertNotIn(store.VALIDATION_NOTES, recorded)
        self.assertEqual(release.frozen_files(self.ctx, "v2026.39"), [])

    def test_an_artifact_the_validator_gave_up_on_is_written_with_its_notes(self):
        def advise(skill, outputs, build_prompt, validate, notes=None, **kwargs):
            if skill == "release-announcement":
                notes.append("The post must open with an `@everyone` ping.")
                return POST.replace("@everyone ", "")
            if skill == "release-notes":
                return NOTES
            notes.append("An id was left unrated.")
            return release.normalize_review(dict(REVIEW))

        with mock.patch.object(release, "generate", side_effect=advise), \
             mock.patch.object(release, "candidate_issues", return_value=[]):
            release.build_review(self.ctx, self.bucket)
            release.build_notes(self.ctx, self.bucket)
            release.build_announcement(self.ctx, self.bucket)
        review = self.state.read_json(store.review_key("v2026.39"))
        self.assertEqual(review["validation_notes"], ["An id was left unrated."])
        self.assertIn("three good things", self.state.read_text(store.announcement_key("v2026.39")))
        self.assertEqual(store.validation_notes(self.state, "v2026.39"),
                         {"announcement.md": ["The post must open with an `@everyone` ping."]})
        # The ledger still knows every file, so none of them reads as hand-edited.
        self.assertEqual(release.frozen_files(self.ctx, "v2026.39"), [])

    def test_a_hand_edited_file_is_skipped_and_kept(self):
        self.build_all()
        mine = "## Headline\n\n- The admin's own words.\n- Two.\n- Three.\n\n## Breaking Changes\n"
        self.state.write_text(store.notes_key("v2026.39"), mine)
        self.assertEqual(release.frozen_files(self.ctx, "v2026.39"), ["notes.md"])
        with mock.patch.object(release, "generate", side_effect=AssertionError("must not run")):
            release.build_notes(self.ctx, self.bucket)
        self.assertEqual(self.state.read_text(store.notes_key("v2026.39")), mine)

    def test_force_overwrites_a_hand_edit_and_re_records_the_hash(self):
        self.build_all()
        self.state.write_text(store.notes_key("v2026.39"), "edited")
        with mock.patch.object(release, "generate", return_value=NOTES):
            release.build_notes(self.ctx, self.bucket, force=True)
        self.assertEqual(self.state.read_text(store.notes_key("v2026.39")), release.annotate_notes(NOTES))
        self.assertEqual(release.frozen_files(self.ctx, "v2026.39"), [])

    def test_the_announcement_is_shaped_by_the_notes_already_written(self):
        seen = {}

        def capture(skill, outputs, build_prompt, validate, **kwargs):
            seen[skill] = build_prompt({name: self.tmp / f"x{suffix}" for name, suffix in outputs.items()}, "")
            return {"notes": NOTES, "announcement": POST}[next(iter(outputs))]

        notes = release.annotate_notes(NOTES)
        self.state.write_text(store.notes_key("v2026.39"), notes)
        store.record_generated(self.state, "v2026.39", "notes.md", notes)
        with mock.patch.object(release, "generate", side_effect=capture):
            release.build_announcement(self.ctx, self.bucket)
        self.assertIn("already written", seen["release-announcement"])
        self.assertIn("## Headline", seen["release-announcement"])
        self.assertNotIn(release.AI_WARNING, seen["release-announcement"])
        post = self.state.read_text(store.announcement_key("v2026.39"))
        self.assertEqual(post.count(release.AI_WARNING), 1)

    def test_the_previous_candidates_headline_is_the_notes_baseline(self):
        seen = {}

        def capture(skill, outputs, build_prompt, validate, **kwargs):
            seen[skill] = build_prompt({name: self.tmp / f"x{suffix}" for name, suffix in outputs.items()}, "")
            return NOTES

        previous = release.annotate_notes("## Headline\n\n- pi gains MCP mode.\n- B.\n- C.\n\n## Breaking Changes\n")
        self.state.write_text(store.notes_key("v2026.39"), previous)
        store.record_generated(self.state, "v2026.39", "notes.md", previous)
        with mock.patch.object(release, "generate", side_effect=capture):
            release.build_notes(self.ctx, self.bucket)
        self.assertIn("previous candidate", seen["release-notes"])
        self.assertIn("- pi gains MCP mode.", seen["release-notes"])
        self.assertNotIn(release.AI_WARNING, seen["release-notes"])

    def test_last_runs_breaking_changes_are_not_carried_forward(self):
        self.state.write_json(store.review_key("v2026.39"), {
            "checklist": [], "breaking_changes": ["resolved_ctx_size can now be null."],
        })
        self.assertNotIn("resolved_ctx_size", release.prior_review_block(self.ctx, self.bucket))

    def test_a_missing_commit_review_is_reported_as_a_coverage_gap(self):
        commit_file(self.source, "c.txt", "three\n", "third")
        self.bucket.head = self.ctx._checkout.resolve("HEAD")
        seen = {}

        def capture(skill, outputs, build_prompt, validate, **kwargs):
            seen["prompt"] = build_prompt({"review": self.tmp / "x.json"}, "")
            return release.normalize_review(dict(REVIEW))

        with mock.patch.object(release, "generate", side_effect=capture), \
             mock.patch.object(release, "candidate_issues", return_value=[]):
            release.build_review(self.ctx, self.bucket)
        self.assertIn("have no commit review", seen["prompt"])
        self.assertEqual(
            self.state.read_json(store.review_key("v2026.39"))["commits_unreviewed"], 1
        )

    def test_building_with_no_commit_reviews_at_all_fails_loudly(self):
        empty = Context(Args(self.tmp / "empty", self.source))
        empty._checkout = self.ctx._checkout
        with self.assertRaises(SystemExit) as caught:
            release.build_review(empty, self.bucket)
        self.assertIn("commit sweep", str(caught.exception))


class CommittedToGit(TempDirCase):
    def setUp(self):
        super().setUp()
        self.source = init_repo(self.tmp / "source")
        self.sha = commit_file(self.source, "a.txt", "one\n", "only")
        init_repo(self.tmp / "state")
        self.ctx = Context(Args(self.tmp / "state", self.source))
        self.ctx._checkout = release.gitops.Checkout(self.source, "lemonade-sdk/lemonade")
        self.ctx.store.write_json(store.commit_key(self.sha), {
            "sha": self.sha, "summary": "x", "verdict": "Clean", "evidence": {},
            "maintainer_todos": [], "shout_outs": [],
        })
        self.ctx.store.save([store.commit_key(self.sha)], "seed")
        self.bucket = release.Bucket.__new__(release.Bucket)
        self.bucket.ctx, self.bucket.branch = self.ctx, "main"
        self.bucket.checkout = self.ctx._checkout
        self.bucket.name, self.bucket.tags = "v2026.39", []
        self.bucket.range_start, self.bucket.head = "", self.sha
        self.bucket.stable_tags, self.bucket.is_hotfix, self.bucket.last_stable = [], False, ""
        self.bucket.cut, self.bucket.previous_head = "", ""

    def test_the_file_and_its_hash_land_in_one_commit(self):
        with mock.patch.object(release, "generate", return_value=NOTES):
            release.build_notes(self.ctx, self.bucket)
        message = git(self.ctx.store.root, "log", "-1", "--format=%s").stdout.strip()
        self.assertEqual(message, "releases/v2026.39: notes")
        touched = git(self.ctx.store.root, "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertEqual(sorted(touched),
                         ["releases/v2026.39/generated.json", "releases/v2026.39/notes.md"])
        self.assertEqual(git(self.ctx.store.root, "status", "--porcelain").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()


def test_review_digest_keeps_fix_rows_and_marks_the_amended_pr():
    from repo_manager import release

    rows = [
        {"sha": "a", "pr_number": 3862, "summary": "sizing", "fixes_prs": []},
        {"sha": "b", "pr_number": 3986, "summary": "never refuse", "fixes_prs": [3862],
         "evidence": {"api_compatibility": "No breaking change: always a number again."}},
    ]
    kept = release.fold_fixes(rows, keep_fixes=True)
    assert [r["sha"] for r in kept] == ["a", "b"]
    assert kept[0]["amended_by"] == [{
        "pr_number": 3986, "summary": "never refuse",
        "api_compatibility": "No breaking change: always a number again.",
    }]
    assert [r["sha"] for r in release.fold_fixes(rows)] == ["a"]


def test_the_digest_carries_the_requests_a_change_answers():
    from repo_manager import release

    rows = [
        {"sha": "a", "pr_number": 3709, "author": "@a", "summary": "Adds containers.",
         "shout_outs": [], "evidence": {}, "fixes_prs": [],
         "answers": [{"ref": "discussion #3585", "title": "RFC: OCI backends", "opened": "2026-09-14",
                      "votes": 24, "comments": 20}]},
        {"sha": "b", "pr_number": 3800, "author": "@b", "summary": "Adds a flag.",
         "shout_outs": [], "evidence": {}, "fixes_prs": [], "answers": []},
    ]
    entries = release.announcement_digest(rows)
    assert entries[0]["answers"][0]["votes"] == 24
    assert "answers" not in entries[1]


def test_requests_answered_reads_closing_issues_and_linked_discussions():
    from unittest import mock
    from repo_manager import github

    def fake(query):
        if "pullRequest" in query:
            return {
                "p10": {"body": "Implements the RFC in https://github.com/o/r/discussions/5 (see #11).",
                        "closingIssuesReferences": {"nodes": [{"number": 7}]}},
                "p11": {"body": "Follow-up to #10.", "closingIssuesReferences": {"nodes": []}},
            }
        return {
            "d5": {"title": "RFC", "createdAt": "2026-09-14T00:00:00Z", "upvoteCount": 24,
                   "comments": {"totalCount": 20}},
            "i5": None, "d7": None,
            "i7": {"title": "Bug", "createdAt": "2026-10-01T00:00:00Z", "state": "CLOSED",
                   "reactions": {"totalCount": 3}, "comments": {"totalCount": 2}},
        }

    with mock.patch.object(github, "graphql_data", side_effect=fake):
        asked = github.requests_answered("o/r", [10, 11])
    assert [a["ref"] for a in asked[10]] == ["issue #7", "discussion #5"]
    assert asked[10][1]["votes"] == 24
    assert asked[11] == []


def test_referenced_numbers_reads_issue_discussion_and_hash_links():
    from repo_manager import github

    text = ("Fixes #3862. RFC: https://github.com/o/r/discussions/3585, see also "
            "https://github.com/o/r/issues/12. Not a&#39; entity, and not other/repo#7, which "
            "may name another repository's number.")
    assert github.referenced_numbers(text) == [3862, 3585, 12]
