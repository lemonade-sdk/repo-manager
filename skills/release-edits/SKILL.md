---
name: release-edits
description: List the decisions a release admin made when they rewrote a repo-manager draft, as checks any later draft of the same release can be held to. Used by scripts/eval-release.py; not part of the release pipeline.
---

# Release Edits

repo-manager wrote a draft of a release's notes or Discord post, and the release admin
rewrote it before it shipped. Every change they made is a decision about what this release
should say: which story leads, what is or is not a breaking change, what deserves a section,
what to cut, how long a bullet should be, what tone to take. Your job is to write those
decisions down so a new draft, written later without seeing the admin's version, can be
checked against each one.

You are given the draft and the published version in full.

Read them side by side and find every decision. A decision is the reason behind an edit, not
the edit's lines:

- A story the admin moved up or down is one decision about order ("The headline leads with
  the container backends"), even though the diff shows a deletion in one place and an
  insertion in another.
- A breaking change the admin removed or added is one decision each ("Breaking changes do not
  list the new `user_configurable` flag").
- A pattern the admin applied across many lines is one decision ("Additional Improvements
  bullets are one short clause each, with no implementation detail"), not one per bullet.
- A rewording that changes nothing a reader learns is not a decision. Skip it.

Leave out anything no writer could have known from the release's commits: a URL to a video,
a personal remark, a plan for a future release, a fact about people or events outside the
code. When an edit mixes such a fact with a real decision — a moved story that also gained a
video link — keep the decision and drop the fact.

Record the decisions a writer can learn from, not the admin's exact text. The lead story is
a decision; the order of everything after it is not, unless the admin moved one story on
purpose. Who gets credited by name, and how a single bullet is worded, are decisions only when
the admin applied them as a pattern. What counts as a breaking change, what is left out, and
how much detail a bullet carries always are.

Phrase each decision as a statement about the content that a reader can check against any
draft of this release, true for the published version and false for the old draft. Name the
thing itself (the feature, the flag, the section), never "the admin" or "the draft".

Write a JSON object to the path the caller names, and nothing else:

```json
{"decisions": ["The headline leads with the new container backends for Strix Halo.", "..."]}
```

Most edits hold two to six decisions. Never pad: a decision you are unsure the admin made is
one to leave out.
