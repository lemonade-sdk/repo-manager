---
name: release-notes
description: Turn stored commit-review results into the website release highlights — the Headline and Breaking Changes sections the lemonade release page is built from. Use when asked for release notes for a release bucket.
---

# Release Notes

Write the website release highlights for one release bucket. This file is machine-consumed
metadata: the lemonade release action reads it and puts it on the release page, and
lemonade-server.ai parses it. Its format is rigid.

Its other job is to shape the release. The Discord announcement is written from these
headline bullets, so the stories you pick here — and the order you put them in — are the
stories that release tells everywhere. Decide them here, once.

The input is the release bucket, the range it covers, the release review's canonical
breaking-change list, up to three prior releases' sections as a style reference, and a
per-commit digest of the stored commit reviews. The digest is the only source of the facts.

## Shaping the release into stories

- Aim for 3-5 stories, ordered by importance. A story is a theme, not a commit: several
  changes that advance the same outcome — support for more platforms, faster models, a
  smoother app — are one story told together, however many PRs or authors it took. If two
  candidate stories would answer the same reader question ("does it run on my hardware?",
  "what's new for images?"), merge them.
- Each story answers exactly one reader question. A title that needs an "&" or
  "Improvements" to hold its contents together is not a story — it is either two real
  stories or a handful of leftovers that do not belong in the highlights at all.
- Work aimed at contributors rather than users — CI, internal refactors, test
  infrastructure — is never a story.
- A story is a capability that *debuts in this release*. Check every candidate against the
  prior releases above: if it already appears there, this release only refined it, and a
  refinement is not a headline bullet unless the refinement itself is what users get.

## The file

Exactly these two sections and nothing else — no title, no separators, no body text:

```markdown
## Headline

- Crisp user-facing headline.
- Crisp user-facing headline.
- Crisp user-facing headline.

## Breaking Changes

- Concise breaking change and migration pointer when needed.
```

Headline rules:

- 3-5 bullets covering the most noteworthy aspects of the release, most important first.
- Each bullet is one short sentence of polished product copy describing what users get, like
  the references: "MTP support added for up to 2x performance increase on supported models."
  Not how it was built.
- Inline code is fine for commands, flags, and model names (`lemonade bench`). No bold,
  italics, links, @handles, or shout outs.
- Keep the Discord post's playfulness out: no "finally", "huge", "massive", "awesome",
  "fresh", or "glow up" here.

Breaking Changes rules:

- One concise bullet per entry in the caller's canonical breaking-changes list, which comes
  from the release review and is the source of truth. Cover every entry, one bullet each —
  the bullet count must equal the list's. Do not drop, merge, or add changes.
- Add the migration action when the entry names one.
- If the list is empty, leave the section heading present with no bullets. Do not write "None".

## Validation

The CLI enforces the machine-readable structure: exactly `## Headline` then
`## Breaking Changes`, single-depth `-` bullets, 3-5 headline bullets, and a breaking-change
bullet count equal to the canonical list's. Wording is yours to get right. If you receive
validation feedback, fix the listed problems and rewrite the file.

Writing the file to the caller-provided path is mandatory before finishing; the CLI reads it
after the skill exits.
