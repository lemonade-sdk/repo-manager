---
name: release-edits
description: List the decisions a release admin made when they rewrote a repo-manager draft, as checks any later draft of the same release can be held to. Used by scripts/eval-release.py; not part of the release pipeline.
---

# Release Edits

repo-manager wrote a draft of a release's notes or Discord post, and the release admin
rewrote it before it shipped. Their edits are decisions about what this release should say.
Your job is to record the decisions a later draft, written without seeing the admin's
version, could make on its own.

You are given the draft and the published version in full. Compare them and fill in only
these fields:

- `lead` — the story the published version leads with, in a few words, when the admin chose a
  different lead from the draft's. Empty when the lead did not change.
- `added_stories` — stories the admin added to the headline (or gave a section in the post)
  that the draft left out or buried. Name each in a few words.
- `removed_stories` — stories the draft featured that the admin cut from the headline (or from
  the post's sections). A story moved down to a short bullet counts as removed.
- `added_breaks` — breaking changes the admin added.
- `removed_breaks` — changes the draft called breaking that the admin took off the list.
- `style` — at most two patterns the admin applied across many lines, such as "improvement
  bullets are one short clause". Leave it empty unless the same edit repeats.

Name the thing itself (the feature, the flag, the model family), never the admin or the draft.
Leave out what no writer could know from the release's commits: links, videos, contributor
lists, personal remarks, counts the admin corrected. A rewording that changes nothing a reader
learns is not a decision. When nothing fits a field, leave it empty.

Write a JSON object to the path the caller names, and nothing else:

```json
{"lead": "the new container backends for Strix Halo", "added_stories": [], "removed_stories": ["the TheNoise upscale factor"], "added_breaks": ["the native DwarfStar4 backend is replaced by its container backend"], "removed_breaks": [], "style": []}
```
