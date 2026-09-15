"""The gates that stand between a model's output and a file a maintainer will trust."""

import unittest

from repo_manager import commits, release


def commit_review(**overrides):
    data = {
        "summary": "Adds a --foo flag.",
        "verdict": "Clean",
        "verdict_reason": "Well tested and reviewed by the CLI owner.",
        "maintainer_todos": [],
        "shout_outs": [],
        "evidence": {key: "none observed" for key in commits.EVIDENCE_KEYS},
    }
    data.update(overrides)
    return data


class CommitReviewValidation(unittest.TestCase):
    def test_a_complete_review_passes(self):
        self.assertEqual(commits.validation_errors(commit_review()), [])

    def test_a_missing_summary_is_caught(self):
        errors = commits.validation_errors(commit_review(summary=""))
        self.assertTrue(any("summary" in e for e in errors))

    def test_an_unknown_verdict_is_caught(self):
        errors = commits.validation_errors(commit_review(verdict="Looks fine"))
        self.assertTrue(any("verdict must be" in e for e in errors))

    def test_verdict_spelling_is_normalized_rather_than_rejected(self):
        self.assertEqual(commits.normalize_verdict("needs attention"), "Needs Attention")
        self.assertEqual(commits.normalize_verdict("BLOCKER"), "Blocker")
        self.assertEqual(commits.normalize_verdict("blocked"), "Blocker")

    def test_missing_evidence_is_named_key_by_key(self):
        evidence = {key: "x" for key in commits.EVIDENCE_KEYS}
        evidence["security"] = ""
        errors = commits.validation_errors(commit_review(evidence=evidence))
        self.assertEqual(errors, ["evidence.security is required: a sentence of what you found "
                                  "(or 'none observed' when that is the honest answer)."])

    def test_a_clean_verdict_that_asks_for_verification_is_a_false_green(self):
        errors = commits.validation_errors(commit_review(
            verdict_reason="A maintainer should verify the installer on Fedora before release."
        ))
        self.assertTrue(any("verdict is Clean but" in e for e in errors), errors)

    def test_a_negated_claim_does_not_trip_the_guard(self):
        # "No maintainer action needed" is what a clean review says; a guard that fires on it
        # would send a correct answer back for a rewrite.
        self.assertEqual(commits.validation_errors(commit_review(
            verdict_reason="No maintainer action needed; nothing needs manual verification."
        )), [])

    def test_a_clean_verdict_with_a_to_do_is_allowed_to_mention_verification(self):
        # The contradiction is an empty list, not the word: with a to-do the reader is told.
        errors = commits.validation_errors(commit_review(
            verdict_reason="A maintainer should verify the installer on Fedora.",
            maintainer_todos=[{"text": "Install the Fedora package and start the server."}],
        ))
        self.assertEqual(errors, [])


def review(**overrides):
    data = {
        "verdict_reason": "Nothing blocks the release.",
        "prioritized_todos": [],
        "breaking_changes": [],
        "tester_plan": [
            {"platform": name, "changed": "nothing in this bucket", "exercise": "Smoke test."}
            for name in release.PLATFORMS
        ],
        "evidence": {key: "none observed" for key in release.EVIDENCE_KEYS},
    }
    data.update(overrides)
    return data


class ReleaseReviewValidation(unittest.TestCase):
    def test_a_complete_review_passes(self):
        self.assertEqual(release.review_errors(review(), []), [])

    def test_the_verdict_is_computed_from_the_list_not_read_from_the_model(self):
        data = release.normalize_review({"verdict": "Ready", "prioritized_todos": [
            {"priority": "P0", "text": "Fix the installer."}]})
        self.assertEqual(data["verdict"], "Blocked")
        data = release.normalize_review({"verdict": "Blocked", "prioritized_todos": [
            {"priority": "P1", "text": "Check the installer."}]})
        self.assertEqual(data["verdict"], "Needs Attention")
        self.assertEqual(release.normalize_review({"prioritized_todos": []})["verdict"], "Ready")

    def test_a_misnamed_to_do_list_is_still_found(self):
        # Pi has filed the same list under `open_todos` and under `todos`; a misnamed list must
        # never collapse into a false "Ready".
        data = release.normalize_review({"open_todos": [{"priority": "P0", "text": "Fix it."}]})
        self.assertEqual(data["verdict"], "Blocked")
        self.assertEqual(data["prioritized_todos"], [{"priority": "P0", "text": "Fix it."}])

    def test_an_empty_list_under_blocking_prose_is_caught(self):
        errors = release.review_errors(
            review(verdict_reason="One P0 remains: the Fedora package is untested."), []
        )
        self.assertTrue(any("prioritized_todos is empty" in e for e in errors))

    def test_an_empty_breaking_list_under_breaking_prose_is_caught(self):
        errors = release.review_errors(review(
            evidence={**{k: "x" for k in release.EVIDENCE_KEYS},
                      "breaking_changes": "The --foo flag was removed, a breaking change."}
        ), [])
        self.assertTrue(any("breaking_changes is empty" in e for e in errors))

    def test_a_ready_verdict_may_say_nothing_blocks_the_release(self):
        self.assertEqual(release.review_errors(
            review(verdict_reason="Nothing blocks the release; no P0s remain."), []), [])

    def test_a_negation_only_covers_its_own_clause(self):
        errors = release.review_errors(review(
            verdict_reason="Nothing else blocks the release, though a maintainer should "
                           "confirm the Fedora package before shipping."
        ), [])
        self.assertTrue(any("prioritized_todos is empty" in e for e in errors))

    def test_saying_there_are_no_breaking_changes_is_not_a_contradiction(self):
        errors = release.review_errors(review(
            evidence={**{k: "x" for k in release.EVIDENCE_KEYS},
                      "breaking_changes": "No user-facing breaking change ships here."}
        ), [])
        self.assertEqual(errors, [])

    def test_every_platform_needs_a_tester_plan_entry(self):
        plan = [{"platform": "Windows", "changed": "x", "exercise": "y"}]
        errors = release.review_errors(review(tester_plan=plan), [])
        self.assertEqual(len(errors), len(release.PLATFORMS) - 1)

    def test_a_tester_plan_entry_needs_both_halves(self):
        plan = [{"platform": name, "changed": "x", "exercise": "y"} for name in release.PLATFORMS]
        plan[0]["exercise"] = ""
        errors = release.review_errors(review(tester_plan=plan), [])
        self.assertTrue(any("exercise is required" in e for e in errors))

    def test_the_tester_plan_is_reordered_into_the_canonical_order(self):
        plan = release.normalize_tester_plan([
            {"platform": "docker", "changed": "a", "exercise": "b"},
            {"platform": "Windows", "changed": "c", "exercise": "d"},
        ])
        self.assertEqual([entry["platform"] for entry in plan], ["Windows", "Docker"])

    def test_every_tester_report_must_reach_the_to_do_list(self):
        issues = [{"number": 3600, "title": "Snap fails to start"}]
        errors = release.review_errors(review(), issues)
        self.assertTrue(any("#3600" in e for e in errors))
        passed = release.review_errors(review(prioritized_todos=[
            {"priority": "P0", "text": "Decide whether to hotfix or revert the snap change (#3600)."}
        ]), issues)
        self.assertEqual(passed, [])


class ArtifactReconciliation(unittest.TestCase):
    NOTES = "## Headline\n\n- One.\n- Two.\n- Three.\n\n## Breaking Changes\n\n- Removed --foo.\n"

    def test_a_well_formed_notes_file_passes(self):
        self.assertEqual(release.notes_errors(self.NOTES, ["Removed --foo; pass --bar."]), [])

    def test_the_two_sections_are_required_in_order(self):
        errors = release.notes_errors("## Breaking Changes\n\n## Headline\n\n- a\n- b\n- c\n", [])
        self.assertTrue(any("exactly `## Headline` then" in e for e in errors))

    def test_a_stray_horizontal_rule_is_forgiven(self):
        self.assertEqual(release.notes_errors("---\n" + self.NOTES, ["Removed --foo."]), [])

    def test_too_few_headline_bullets_is_caught(self):
        notes = "## Headline\n\n- One.\n\n## Breaking Changes\n"
        errors = release.notes_errors(notes, [])
        self.assertTrue(any("3-5 bullets" in e for e in errors))

    def test_a_dropped_breaking_change_is_caught(self):
        notes = "## Headline\n\n- One.\n- Two.\n- Three.\n\n## Breaking Changes\n\n- Removed --foo.\n"
        errors = release.notes_errors(notes, ["Removed --foo.", "Renamed the baz model id."])
        self.assertTrue(any("2" in e and "1 Breaking Changes bullet" in e for e in errors))

    def test_an_invented_breaking_change_is_caught(self):
        errors = release.notes_errors(self.NOTES, [])
        self.assertTrue(any("release review found none" in e for e in errors))

    def test_the_announcement_must_ping_everyone(self):
        errors = release.announcement_errors("## Lemonade v2026.38\n\nHi.\n", [], hotfix=False)
        self.assertTrue(any("@everyone" in e for e in errors))

    def test_a_hotfix_pings_release_instead(self):
        post = "## Lemonade v2026.38\n\n@everyone a fix.\n"
        errors = release.announcement_errors(post, [], hotfix=True)
        self.assertTrue(any("never `@everyone`" in e for e in errors))
        good = "## Lemonade v2026.38\n\n@release a fix for the snap package.\n"
        self.assertEqual(release.announcement_errors(good, [], hotfix=True), [])

    def test_a_missing_breaking_section_in_the_post_is_caught(self):
        post = "## Lemonade v2026.38\n\n@everyone here we go.\n"
        errors = release.announcement_errors(post, ["Removed --foo."], hotfix=False)
        self.assertTrue(any("no Breaking Changes section" in e for e in errors))

    def test_a_post_that_runs_long_is_caught(self):
        post = "## Lemonade v2026.38\n\n@everyone\n" + "\n".join(f"- line {n}" for n in range(60))
        errors = release.announcement_errors(post, [], hotfix=False)
        self.assertTrue(any("non-blank lines" in e for e in errors))

    def test_breaking_bullets_are_counted_at_any_heading_depth(self):
        post = "### Breaking Changes\n\n- a\n- b\n\n### Next\n\n- c\n"
        self.assertEqual(release.count_breaking_bullets(post), 2)
        self.assertEqual(release.count_breaking_bullets("## Headline\n\n- a\n"), -1)

    def test_a_link_built_from_the_bucket_name_is_rejected(self):
        # `v2026.39` is the bucket; the tag will be `v2026.39.<n>`, uncut when the post is
        # written. So this URL is wrong the moment it is typed, not merely stale.
        post = ("## Lemonade v2026.39\n\n@everyone here we go.\n\n"
                "Full notes: https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.39\n")
        errors = release.announcement_errors(post, [], hotfix=False, bucket="v2026.39")
        self.assertTrue(any("will never exist" in e for e in errors), errors)

    def test_a_link_to_a_real_tag_in_the_bucket_is_fine(self):
        post = ("## Lemonade v2026.39\n\n@everyone a fix.\n\n"
                "https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.39.1\n")
        self.assertEqual(release.announcement_errors(post, [], hotfix=False, bucket="v2026.39"), [])

    def test_a_link_to_the_releases_page_is_fine(self):
        post = ("## Lemonade v2026.39\n\n@everyone here we go.\n\n"
                "https://github.com/lemonade-sdk/lemonade/releases\n")
        self.assertEqual(release.announcement_errors(post, [], hotfix=False, bucket="v2026.39"), [])

    def test_the_notes_file_is_held_to_the_same_link_rule(self):
        notes = (self.NOTES + "\nSee https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.39\n")
        errors = release.notes_errors(notes, ["Removed --foo."], bucket="v2026.39")
        self.assertTrue(any("will never exist" in e for e in errors), errors)

    def test_breaking_change_objects_are_folded_into_sentences(self):
        self.assertEqual(
            release.normalize_breaking_changes(
                [{"change": "Removed --foo", "migration": "pass --bar instead"}]
            ),
            ["Removed --foo — pass --bar instead"],
        )


if __name__ == "__main__":
    unittest.main()


class StoredShape(unittest.TestCase):
    """The files are the store, so what lands in them has one shape per field."""

    def test_a_bare_string_to_do_becomes_an_object(self):
        self.assertEqual(
            commits.normalize_todos(["Run the installer on Fedora."]),
            [{"text": "Run the installer on Fedora."}],
        )

    def test_an_object_to_do_keeps_its_other_fields(self):
        self.assertEqual(
            commits.normalize_todos([{"text": "Check X.", "priority": "P1"}]),
            [{"text": "Check X.", "priority": "P1"}],
        )

    def test_a_to_do_filed_under_another_key_is_found(self):
        self.assertEqual(commits.normalize_todos([{"action": "Check X."}]),
                         [{"action": "Check X.", "text": "Check X."}])

    def test_empty_and_unusable_entries_are_dropped(self):
        self.assertEqual(commits.normalize_todos(["", {"text": "   "}, {}, None]), [])

    def test_no_to_dos_is_an_empty_list_not_a_missing_key(self):
        self.assertEqual(commits.normalize_todos(None), [])
