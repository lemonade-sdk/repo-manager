import json
import unittest
from unittest import mock

from repo_manager import pi


class Advising(unittest.TestCase):
    """Validation guides the model and then gets out of the way: what the skill wrote on its
    last attempt is returned with the unresolved problems, never withheld."""

    def generate(self, artifacts, validate, attempts=3):
        artifacts = list(artifacts)
        seen = []

        def run(skill, prompt, work, checkout="", base_ref=""):
            seen.append(prompt)
            artifact = artifacts.pop(0)
            if artifact is not None:
                (work / f"review.{len(seen)}.json").write_text(artifact, encoding="utf-8")
            return ""

        def build_prompt(paths, feedback):
            return f"write to {paths['review']}\n{feedback}"

        notes = []
        with mock.patch.object(pi, "run_pi", side_effect=run):
            value = pi.generate("skill", {"review": ".json"}, build_prompt, validate,
                                attempts=attempts, notes=notes)
        return value, notes, seen

    def test_a_valid_artifact_returns_with_no_notes(self):
        value, notes, seen = self.generate(['{"ok": true}'], lambda c: (json.loads(c["review"]), []))
        self.assertEqual(value, {"ok": True})
        self.assertEqual(notes, [])
        self.assertEqual(len(seen), 1)

    def test_the_problems_are_handed_back_and_a_fixed_attempt_carries_no_notes(self):
        def validate(contents):
            data = json.loads(contents["review"])
            return data, [] if data.get("fixed") else ["Say it is fixed."]

        value, notes, seen = self.generate(['{"fixed": false}', '{"fixed": true}'], validate)
        self.assertEqual(value, {"fixed": True})
        self.assertEqual(notes, [])
        self.assertIn("Say it is fixed.", seen[1])

    def test_an_artifact_still_wrong_on_the_last_attempt_is_returned_with_its_problems(self):
        def validate(contents):
            return json.loads(contents["review"]), ["The opener must ping `@everyone`."]

        value, notes, seen = self.generate(['{"n": 1}', '{"n": 2}', '{"n": 3}'], validate)
        self.assertEqual(value, {"n": 3})
        self.assertEqual(notes, ["The opener must ping `@everyone`."])
        self.assertEqual(len(seen), 3)

    def test_a_usable_earlier_attempt_survives_a_later_one_that_wrote_nothing(self):
        def validate(contents):
            data = pi.extract_json_object(contents["review"])
            if data is None:
                return None, ["The artifact file must contain a valid JSON object."]
            return data, ["Still not right."]

        value, notes, _ = self.generate(['{"n": 1}', None, "not json"], validate)
        self.assertEqual(value, {"n": 1})
        self.assertEqual(notes, ["Still not right."])

    def test_nothing_usable_in_any_attempt_is_still_a_failure(self):
        def validate(contents):
            return None, ["The artifact file must contain a valid JSON object."]

        with self.assertRaises(SystemExit) as raised:
            self.generate([None, "x", None], validate)
        self.assertIn("no usable artifact", str(raised.exception))

    def test_a_caller_that_passes_no_notes_list_still_gets_the_artifact(self):
        def run(skill, prompt, work, checkout="", base_ref=""):
            (work / "review.1.json").write_text('{"n": 1}', encoding="utf-8")
            return ""

        with mock.patch.object(pi, "run_pi", side_effect=run):
            value = pi.generate("skill", {"review": ".json"}, lambda p, f: "",
                                lambda c: (json.loads(c["review"]), ["a problem"]), attempts=1)
        self.assertEqual(value, {"n": 1})


if __name__ == "__main__":
    unittest.main()
