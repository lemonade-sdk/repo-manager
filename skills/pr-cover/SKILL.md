---
name: pr-cover
description: Tier 2 of PR triage. For each surface a PR changes, decide what covers it under lemonade's spec-driven development policy: a statement of intended behavior already on the base branch (a fix), a ratified working-group charter, or a linked RFC. Use when the caller provides the surfaces and the cover documents inline.
---

# PR Cover

Lemonade's policy: any change to scope, surface area, or user or developer experience needs an approved RFC, unless it is a bug fix or it sits entirely inside a ratified working-group charter. The facts pass has already listed the surfaces this PR changes. Your job is narrower than deciding the label: for each surface, say what covers it, and be precise about which document and which sentence.

You do not need to read the diff. You need to read the cover documents in the prompt and, when a fix is claimed, the documentation on the base branch.

## Three kinds of cover

**Fix.** A surface is a fix only when something already on the base branch states the intended behavior that the diff now delivers: a docs page, the API reference, the OpenAI compatibility contract, a schema, a charter roadmap item, an existing test. A linked issue is evidence that behavior deviated; it is never the source of intent. If the only thing saying the current behavior is wrong is the issue, the change is a feature request and is not covered, whoever wrote the issue. To claim a fix, fetch the base-branch page with the command in the prompt and quote the line. Observable output may change in a fix: making aliases resolve where the docs already promise they do is a fix. The existing contract is itself a statement of intent: a request the base branch already failed, crashed on, or answered wrongly was never intended to be served that way, so turning that failure into a clean error or the right answer is a fix, and a linked issue describing the failure is enough evidence of it. What an issue cannot do is establish that the project wants a capability it never had. A surface marked `was: absent` is new: a flag, field, or route that did not exist. Documentation of a neighboring feature ("--force bypasses filtering") never covers a new one (`--arch`); only a charter item or an RFC can. Two notes on a surface line come from a grep of the base branch and outrank your reading of the docs: `was: failing` or `was: ignored` says the old path already broke or dropped the input, and `[already present in base-branch source: X]` says the server already reads or stores X somewhere; a surface that makes X work on one more path is a fix by the existing contract.

**Working group.** A surface is covered by a charter when a Scope or Roadmap item names it: the same capability, artifact, or surface, not a neighbor of it. Quote that bullet verbatim, from its `- ` or `- [ ]` line; headings and paragraphs are not items, and a quote that does not come from a bullet is discarded downstream. A goal or principle sentence ("improve test coverage", "support all platforms", "better performance") covers nothing by itself; if the most specific text you can quote is a goal, the surface is not covered. A charter that exists only as a table row with no charter file covers nothing. A charter edited in this same PR is read as it stood before the PR. A breaking change that migrates existing installs is never covered by a charter.

**RFC.** A surface is covered by the linked RFC when its High-level design describes that surface as it appears in the diff: the same route, the same flag, the same default. A surface the RFC never mentions is `covered: false` with `by` explaining what the RFC does say. A surface the RFC describes differently (another path, another default) is not covered. A surface that is only what the described change entails in this codebase — the build file, manifest, or installer that has to carry the new version format, the CI step that runs the new tool's tests — is covered by the design it implements; say which described change entails it. Uncovered means the PR does something the RFC's user or developer would not expect from reading it. If the RFC declares phases and this PR is one of them, later phases missing from the diff are not your concern. Whether the RFC is approved is not your concern either; the label is derived elsewhere from the RFC's own label.

Use the cover the body claims first. If the body claims nothing, look at the working groups in the prompt: when the surfaces plainly belong to one group's goal, say `kind: working-group` with that group's name, whether or not it has a charter file — a group with no charter is reported downstream as exactly that, and naming it is what lets the lead be asked. `none` is for a change no group's goal describes. Do not invent an RFC.

## Output

Write this JSON to the path in the prompt, nothing else in the file:

```json
{
  "cover": {
    "kind": "working-group",
    "name": "Auto-Tune",
    "surfaces": [
      {"index": 0, "covered": true, "by": "charter roadmap phase 2: 'CLI or config option to enable/disable auto-tune (enabled by default)'"},
      {"index": 1, "covered": false, "by": "the charter has no item about migrating existing config files, and a migration is never charter-covered"}
    ],
    "explanation": "Two or three sentences naming the document and item that covers the change, or what is missing. This is read by a maintainer; write it plainly."
  }
}
```

`kind` is the cover the PR relies on: `fix`, `working-group`, `rfc`, or `none` when nothing is claimed and no charter fits. `name` is the group name or the RFC number as `#N`, empty for a fix. Judge every non-internal surface by its index from the prompt. `by` always names a document and a sentence, or says what was looked for and not found.
