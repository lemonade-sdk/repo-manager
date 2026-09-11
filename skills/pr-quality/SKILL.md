---
name: pr-quality
description: Tier 3 of PR triage. Check a pull request against lemonade's documentation.md and testing.md and report the gaps as imperative to-dos. Use when the caller provides the surfaces, the guide excerpts, the doc and test trees, and the diff inline.
---

# PR Quality

Two questions, each judged against the guide's written rules and nothing else: does the documentation describe the code as this PR leaves it, and does a test that could catch a regression of this change run in CI.

Everything you need is in the prompt: the surfaces the facts pass found, the relevant sections of both guides, the documentation tree, the test and CI tree, and the diff. Fetch a file with the `gh api` command in the prompt whenever a verdict depends on what it says. A file the PR changes is read at the head SHA; a page the PR does not touch is read on the base branch.

## Documentation

The rule: documentation for a new or changed feature ships in the same PR, in the page that owns that kind of thing. A flag belongs in the CLI reference, an endpoint or field in the API reference, a config key in the configuration reference, a backend beside its peers.

- For each user-facing surface, find where its peers are documented and check that page. Cite the page you opened.
- A page the diff never opened can be the gap: if the change makes an existing page untrue, that page owes an edit.
- Some docs are generated from code (a generation marker at the top of the file says so); when the PR changes the source without regenerating, the fix is to run the generator.
- No peer documented anywhere means no obligation. Do not point a gap at a page that does not exist.
- `n/a` for internal refactors, CI, test-only changes, and fixes that add no behavior.
- A style complaint is a gap only if you can quote the rule in the guide excerpt that it breaks.

## Testing

Three questions in order, each answered from the diff and the trees, not from an impression:

1. **Did a test ship with the change?** Credit only tests in the changed-file list, or an existing case you can name that exercises the changed path.
2. **Would CI run it?** Find the workflow or CTest label that executes that suite. A test file no workflow runs is a gap whose action is to wire it up.
3. **Could it fail?** An assertion that also passes on the old code is not coverage.

Restraint: a row added to a data table an existing mechanism consumes (a model entry, a version pin, a GPU id) is covered by that mechanism's tests. A change with no testable surface (docs, comments, assets) is `n/a`. A behavior change inside existing code always has a testable surface: its regression test belongs in the suite that already owns that code.

## Output

Write this JSON to the path in the prompt, nothing else in the file:

```json
{
  "docs": {
    "status": "gaps",
    "gaps": [
      {"what": "the new --arch option is not in the CLI reference", "where": "docs/guide/cli.md", "action": "Add --arch to the `lemonade backends install` table in docs/guide/cli.md"}
    ]
  },
  "tests": {
    "status": "ok",
    "gaps": []
  },
  "explanation": "Two sentences: the pages and suites you checked, and what you found. Do not list every file."
}
```

`status` is `ok`, `gaps`, or `n/a`, and is `gaps` exactly when the list is non-empty. Each `action` is one imperative sentence naming the file, under about a hundred characters. One gap per edit; do not file the same edit twice.
