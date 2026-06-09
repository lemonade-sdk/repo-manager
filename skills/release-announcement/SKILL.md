---
name: release-announcement
description: Generate a Discord-friendly markdown release announcement from stored commit-review results. Use when asked to turn release commits into enthusiast-user-facing release notes with feature grouping and shout outs.
---

# Release Announcement

Generate a Discord-friendly markdown announcement from commit-review records for a release range.

If the caller provides an output file path, write the final Discord-friendly Markdown announcement to that path before finishing.

## Audience

Write for enthusiastic users of the project, not contributors. Avoid development artifacts such as PR numbers, internal filenames, CI details, or implementation minutiae unless they are necessary to explain a user-visible change.

## Grouping

- Create one heading per major feature in the release.
- Decide major features by user-visible scope and importance.
- Coalesce multiple commits into one feature when they are highly related.
- Add a final `Additional Improvements` heading for minor features and fixes.
- Under `Additional Improvements`, use one bullet per coalesced feature or fix.

## Shout Outs

Use shout outs from the underlying commit reviews, but keep the high bar:

- Include a shout out only for exceptional involvement beyond routine review.
- Do not invent shout outs.
- Place shout outs under the relevant feature heading.
- Do not repeat the same shout out excessively; combine where sensible.

## Output Format

Use Discord-friendly Markdown:

```markdown
# Release Highlights

## Major Feature Name

One short paragraph explaining the feature at a user level.

Shout out: @handle for ...

## Additional Improvements

- Concise one-sentence user-level explanation. Shout out: @handle for ...
```

Do not include a verdict, internal evidence, raw PR lists, or exhaustive commit inventories.
