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

    def test_a_verdict_that_names_verification_carries_the_to_do_and_the_grade(self):
        # A reason that says somebody must verify something, with the to-do that says how, is
        # a complete review — at Needs Attention. Saying it over `Clean` is the false green
        # this guard exists for, whether or not a list sits under it.
        content = dict(
            verdict_reason="A maintainer should verify the installer on Fedora.",
            maintainer_todos=[{"text": "Install the Fedora package and start the server."}],
        )
        self.assertEqual(commits.validation_errors(
            commit_review(verdict="Needs Attention", **content)), [])
        self.assertTrue(commits.validation_errors(commit_review(verdict="Clean", **content)))


class AGradeAndAListMustAgree(unittest.TestCase):
    def test_clean_cannot_carry_a_to_do(self):
        # Seen in the wild: two perfectly good tester instructions under a grade that says
        # nothing is needed. The reader believes the grade.
        errors = commits.validation_errors(commit_review(
            verdict="Clean",
            maintainer_todos=[{"text": "Run lemonade-server --version and confirm 2026.39.1."}],
        ))
        self.assertTrue(any("Clean but there is 1 to-do" in e for e in errors), errors)

    def test_needs_attention_may_carry_none(self):
        # The two move independently: a test-only commit with a late unreviewed change needs
        # a maintainer and gives a tester nothing to do.
        self.assertEqual(commits.validation_errors(commit_review(
            verdict="Needs Attention", maintainer_todos=[],
            verdict_reason="A post-approval commit changed test behavior without re-review.",
        )), [])

    def test_clean_with_an_empty_list_is_the_ordinary_case(self):
        self.assertEqual(commits.validation_errors(commit_review(
            verdict="Clean", maintainer_todos=[],
            verdict_reason="Focused refactor with tests and no user-visible change.",
        )), [])


class TodosAreWrittenForATester(unittest.TestCase):
    """A to-do is copied verbatim onto the checklist of somebody who has never seen the code.
    These are the two failures readable off the sentence itself; the rest is the skill's job."""

    def flagged(self, text):
        return commits.todo_errors([{"text": text}])

    def test_a_tester_sized_instruction_passes(self):
        for text in (
            "Run lemonade-server --version and confirm it prints 2026.39.1.",
            "Install the Snap build on Ubuntu 24.04 and confirm the server starts.",
            "Call GET /api/v1/health and confirm it answers 200 with the running version.",
            "Open the Models page and confirm Llama-3.1-8B is listed.",
            "Install the .deb on Debian 13 and confirm the service starts.",
        ):
            self.assertEqual(self.flagged(text), [], text)

    def test_naming_something_only_the_source_explains_is_caught(self):
        for text, why in (
            ("Verify whether the matches() method is a hot path.", "a function"),
            ("Confirm origin_utils.h still compiles.", "a header"),
            ("Verify version.py still derives the tag.", "a module"),
            ("Check that src/backends/engine.cpp builds.", "a path"),
            ("Confirm CMakeLists picks up the new target.", "a build file"),
            ("Confirm the refactor bcfc5c9 was re-reviewed.", "a SHA"),
        ):
            self.assertTrue(self.flagged(text), f"{why} was not caught: {text}")

    def test_a_product_that_happens_to_look_like_a_filename_is_not(self):
        # llama.cpp is a backend a user picks, not a translation unit; node.js is a runtime.
        self.assertEqual(self.flagged("Confirm the llama.cpp backend answers after a swap."), [])
        self.assertEqual(self.flagged("Confirm node.js clients can still connect."), [])

    def test_an_english_word_spelled_in_hex_is_not_a_sha(self):
        # "defaced" is seven characters of [a-f]; a real SHA has digits in it.
        self.assertEqual(self.flagged("Check the defaced banner is gone on startup."), [])

    def test_an_item_with_no_pass_or_fail_is_caught(self):
        for text in (
            "Consider caching the compiled pattern.",
            "Investigate the latency regression.",
            "Decide whether the race condition warrants a follow-up.",
            "Verify whether the endpoint is on the release surface.",
            "Evaluate the new allowlist policy.",
        ):
            self.assertTrue(self.flagged(text), text)

    def test_the_same_verb_with_a_definite_outcome_is_fine(self):
        # "Verify that X" is a task; "verify whether X" is a question.
        self.assertEqual(
            self.flagged("Verify that the installer is signed and Windows does not warn."), [])


def todo_index(*texts):
    """The digest's to-do index, as `digest` hands it to `normalize_review`."""
    return {
        f"c0mmit{i}-1": {"text": text, "commit": f"{i}" * 40,
                         "pr_number": 3500 + i, "author": "@dev"}
        for i, text in enumerate(texts)
    }


def rated(*priorities, **overrides):
    """A model answer that rates the index built from the same number of texts."""
    data = {"ratings": [{"id": f"c0mmit{i}-1", "priority": p} for i, p in enumerate(priorities)]}
    data.update(overrides)
    return data


def normalized(answer, index):
    """What `build_review` does: read the ratings, assemble, then validate the result.

    The order matters — assembling rewrites the answer in place — so the tests go through
    this rather than each getting it right on their own.
    """
    ratings = release.extract_ratings(answer)
    data = release.normalize_review(answer, index)
    data["evidence"] = {key: "none observed" for key in release.EVIDENCE_KEYS}
    return data, ratings


def review(**overrides):
    data = {
        "checklist": [],
        "breaking_changes": [],
        "evidence": {key: "none observed" for key in release.EVIDENCE_KEYS},
    }
    data.update(overrides)
    return data


class ReleaseReviewValidation(unittest.TestCase):
    def test_a_complete_review_passes(self):
        self.assertEqual(release.review_errors(review(), []), [])

    def test_no_verdict_is_stored_however_hard_the_model_tries(self):
        """Whether a release ships is the release admin's call. A stored one-word answer is
        frozen at the moment the model ran, so it is wrong the first time somebody works an
        item — and a reader who sees it ships on it."""
        index = todo_index("Fix the installer.")
        data = release.normalize_review(
            {"verdict": "Ready", "verdict_reason": "Nothing blocks the release.", **rated("P0")},
            index)
        self.assertNotIn("verdict", data)
        self.assertNotIn("verdict_reason", data)

    def test_every_to_do_reaches_the_checklist_whatever_the_model_sent_back(self):
        # The model rates; the caller assembles. An item it forgot is still on the list, in
        # P1, and the omission is reported rather than silently honoured as a deletion.
        index = todo_index("Fix the installer.", "Tidy the imports.")
        data = release.normalize_review(rated("P0"), index)
        self.assertEqual([t["priority"] for t in data["checklist"]], ["P0", "P1"])
        self.assertEqual([t["text"] for t in data["checklist"]],
                         ["Fix the installer.", "Tidy the imports."])
        _, ratings = normalized(rated("P0"), index)
        errors = release.review_errors({**review(), **data}, [], index, ratings)
        self.assertTrue(any("have no rating" in e for e in errors), errors)

    def test_the_model_cannot_reword_a_to_do(self):
        index = todo_index("Install the Fedora package and start the server.")
        data = release.normalize_review(
            {**rated("P1"), "checklist": [{"priority": "P0", "text": "Something else."}]}, index)
        self.assertEqual([t["text"] for t in data["checklist"]],
                         ["Install the Fedora package and start the server."])

    def test_a_to_do_carries_the_commit_it_came_from(self):
        index = todo_index("Fix the installer.")
        item = release.normalize_review(rated("P0"), index)["checklist"][0]
        self.assertEqual(item["commit"], "0" * 40)
        self.assertEqual(item["pr_number"], 3500)

    def test_a_rating_for_a_to_do_that_does_not_exist_is_caught(self):
        index = todo_index("Fix the installer.")
        data, ratings = normalized(
            {"ratings": [{"id": "nosuch-9", "priority": "P0"}]}, index)
        errors = release.review_errors(data, [], index, ratings)
        self.assertTrue(any("not a to-do id" in e for e in errors), errors)

    def test_a_tester_report_is_the_one_item_the_model_writes(self):
        data = release.normalize_review(
            {"extra_items": [{"priority": "P0", "text": "Tester report #4120: Snap fails."}]}, {})
        self.assertEqual(data["checklist"][0]["text"], "Tester report #4120: Snap fails.")
        self.assertEqual(data["checklist"][0]["commit"], "")
        self.assertEqual(data["checklist"][0]["priority"], "P0")

    def test_a_misnamed_ratings_list_is_still_found(self):
        # Pi files the same answer under two or three names; a misnamed one must never
        # collapse every to-do into the P1 default.
        index = todo_index("Fix it.")
        data = release.normalize_review(
            {"priorities": [{"id": "c0mmit0-1", "priority": "P0"}]}, index)
        self.assertEqual(data["checklist"][0]["priority"], "P0")

    def test_a_bare_mapping_of_id_to_priority_is_read_too(self):
        index = todo_index("Fix it.")
        data = release.normalize_review({"ratings": {"c0mmit0-1": "P0"}}, index)
        self.assertEqual(data["checklist"][0]["priority"], "P0")

    def test_a_list_of_only_P2s_is_still_a_list_of_work(self):
        # Nothing on a release checklist is somebody else's problem: a P2 is the last thing
        # a tester gets to before shipping, not the first thing after.
        index = todo_index("Update the internal CI document.")
        data, ratings = normalized(rated("P2"), index)
        self.assertEqual([t["priority"] for t in data["checklist"]], ["P2"])
        self.assertEqual(release.review_errors(data, [], index, ratings), [])

    def test_an_empty_breaking_list_under_breaking_prose_is_caught(self):
        errors = release.review_errors(review(
            evidence={**{k: "x" for k in release.EVIDENCE_KEYS},
                      "breaking_changes": "The --foo flag was removed, a breaking change."}
        ), [])
        self.assertTrue(any("breaking_changes is empty" in e for e in errors))

    def test_saying_there_are_no_breaking_changes_is_not_a_contradiction(self):
        errors = release.review_errors(review(
            evidence={**{k: "x" for k in release.EVIDENCE_KEYS},
                      "breaking_changes": "No user-facing breaking change ships here."}
        ), [])
        self.assertEqual(errors, [])

    def test_an_item_that_names_no_platform_applies_everywhere(self):
        # A tester should never have to guess whether silence means "all of them" or "we
        # forgot", so the absent case is spelled out rather than left empty.
        data = release.normalize_review(rated("P1"), todo_index("Check X."))
        self.assertEqual(data["checklist"][0]["platforms"], ["all"])

    def test_platforms_are_spelled_and_ordered_the_caller_way(self):
        data = release.normalize_review({"ratings": [
            {"id": "c0mmit0-1", "priority": "P1", "platforms": ["fedora", "WINDOWS"]}]},
            todo_index("Check X."))
        self.assertEqual(data["checklist"][0]["platforms"], ["Windows", "Fedora"])

    def test_all_beats_a_list_of_names(self):
        data = release.normalize_review({"ratings": [
            {"id": "c0mmit0-1", "priority": "P1", "platforms": ["Windows", "all"]}]},
            todo_index("Check X."))
        self.assertEqual(data["checklist"][0]["platforms"], ["all"])

    def test_a_platform_this_project_does_not_test_on_is_caught(self):
        errors = release.review_errors(review(checklist=[
            {"priority": "P1", "platforms": ["Solaris"], "text": "Check X."}]), [])
        self.assertTrue(any("not a platform" in e for e in errors), errors)

    def test_every_tester_report_must_reach_the_checklist(self):
        issues = [{"number": 3600, "title": "Snap fails to start"}]
        errors = release.review_errors(review(), issues)
        self.assertTrue(any("#3600" in e for e in errors))
        # The item is the reproduction, which is what a tester can do. What to do about the
        # result is the release admin's call and is not a checklist line.
        passed = release.review_errors(review(checklist=[
            {"priority": "P0", "platforms": ["Snap"],
             "text": "Install the Snap build on Ubuntu 24.04 and confirm the server starts "
                     "(#3600 reports it does not)."}
        ]), issues)
        self.assertEqual(passed, [])

    def test_a_tester_report_written_as_a_decision_is_rejected(self):
        issues = [{"number": 3600, "title": "Snap fails to start"}]
        errors = release.review_errors(review(checklist=[
            {"priority": "P0", "platforms": ["Snap"],
             "text": "Decide whether to hotfix or revert the snap change (#3600)."}
        ]), issues)
        self.assertTrue(any("no pass and no fail" in e for e in errors), errors)


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


class TheClosingLink(unittest.TestCase):
    """Banning the wrong link without requiring a right one is how the post ended up with
    none: told only that `releases/tag/v2026.39` was wrong, the model dropped the link and
    closed with "check out the full release notes on GitHub" pointing at nothing."""

    POST = "## Lemonade v2026.39\n\n@everyone here we go.\n\n"

    def errors(self, tail):
        return release.announcement_errors(
            self.POST + tail, [], hotfix=False, bucket="v2026.39", repo="lemonade-sdk/lemonade")

    def test_a_post_with_no_link_at_all_is_rejected(self):
        self.assertTrue(any("no link" in e for e in self.errors("Full notes on GitHub!\n")))

    def test_the_releases_page_satisfies_it(self):
        self.assertEqual(self.errors("https://github.com/lemonade-sdk/lemonade/releases\n"), [])

    def test_the_bucket_link_is_still_rejected_even_though_it_is_a_link(self):
        errors = self.errors("https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.39\n")
        self.assertTrue(any("will never exist" in e for e in errors))
        self.assertFalse(any("no link" in e for e in errors))


class BotsAreNotContributors(unittest.TestCase):
    def test_a_bot_author_is_not_offered_to_the_announcement(self):
        rows = [{"author": "@github-actions[bot]", "summary": "Bump the pins.",
                 "shout_outs": ["@dependabot"], "evidence": {}}]
        digest = release.announcement_digest(rows)
        self.assertEqual(digest[0]["author"], "")
        self.assertEqual(digest[0]["credits"], [])

    def test_a_person_is_still_offered(self):
        rows = [{"author": "@popey", "summary": "Fix the filter.",
                 "shout_outs": ["@bitgamma"], "evidence": {}}]
        digest = release.announcement_digest(rows)
        self.assertEqual(digest[0]["author"], "@popey")
        self.assertEqual(digest[0]["credits"], ["@bitgamma"])


class PlatformShorthand(unittest.TestCase):
    def test_naming_every_platform_collapses_to_all(self):
        data = release.normalize_review({"ratings": [
            {"id": "c0mmit0-1", "priority": "P1", "platforms": list(release.PLATFORMS)}]},
            todo_index("Check the version."))
        self.assertEqual(data["checklist"][0]["platforms"], ["all"])

    def test_naming_most_of_them_does_not(self):
        data = release.normalize_review({"ratings": [
            {"id": "c0mmit0-1", "priority": "P1", "platforms": list(release.PLATFORMS[:-1])}]},
            todo_index("Check the version."))
        self.assertEqual(data["checklist"][0]["platforms"], list(release.PLATFORMS[:-1]))
