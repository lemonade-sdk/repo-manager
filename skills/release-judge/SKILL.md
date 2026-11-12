---
name: release-judge
description: Check a regenerated release draft against the decisions the release admin made on an earlier draft of the same release. Used by scripts/eval-release.py; not part of the release pipeline.
---

# Release Judge

The release admin rewrote a draft of this release before it shipped, and their decisions are
written down as numbered statements: which story leads, what is or is not a breaking change,
what to cut, how to say it. A new draft has since been written without seeing the admin's
version. Say, for each statement, whether the new draft makes that decision on its own.

- `made` — the statement is true of the new draft. Judge the substance, not the words: a
  differently worded bullet that leads with the same story, leaves out the same false
  breaking change, or cuts the same detail counts.
- `missed` — the statement is false of the new draft, whatever it does instead.

A statement is about one decision; hold the draft to that decision and nothing else. "It leads
with the six new container backends" is made by a draft that leads with the container
backends, whatever count it gives. "Leads with" means the first story: the opening line and
the Breaking Changes section are not stories, so a post that opens, lists its breaking
changes, and then tells the container backends first leads with them. When a statement is
about absence, search the whole draft before answering `made`.

Write a JSON object to the path the caller names, and nothing else:

```json
{"verdicts": [{"id": 1, "verdict": "made", "why": "Its first headline bullet is the container backends."}]}
```

One entry per statement, every id the caller listed, `why` in one short sentence that points
at what the new draft says.
