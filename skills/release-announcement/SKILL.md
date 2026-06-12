---
name: release-announcement
description: Generate website release highlights and a Discord-friendly release announcement from stored commit-review results. Use when asked to turn release commits into enthusiast-user-facing release communications with feature grouping and shout outs.
---

# Release Announcement

Generate website release highlights and a Discord-friendly markdown announcement from commit-review records for a release. The release tag is the release being announced, such as `v10.7.0`; the range start tag is only the previous release boundary used to select commits.

If the caller provides output file paths, write both artifacts before finishing:

- Website release highlights Markdown to the requested release-highlights path.
- Discord-friendly Markdown announcement to the requested announcement path.

The caller may provide two kinds of references:

- Up to three prior GitHub `## Headline` and `## Breaking Changes` sections. Use these only to match the style, concision, ordering, and level of abstraction for the new website release highlights artifact.
- Up to three prior Discord release announcements. Use these only to match voice, section density, formatting, and level of detail for the Discord announcement.

Do not copy old facts, old shout outs, old migration notes, old closing sentences, or old feature claims into the current release unless the current commit-review records independently support them.

## Editorial Planning

Before writing either artifact, make an internal editorial plan. Do not print this plan unless explicitly asked.

The plan should identify:

- `headline_candidates`: the 3-5 user-facing changes users should know happened.
- `breaking_changes`: actual user-facing compatibility or migration changes.
- `announcement_sections`: the subset of headline candidates that deserve a full Discord section.
- `additional_improvements_groups`: lower-salience headline candidates and other user-visible changes grouped by outcome.

Use the same ordering and subject judgment across both artifacts. However, not every headline candidate should become a Discord section. The fourth and fifth headline candidates often belong under `Additional Improvements` in the announcement.

## Audience

Write for enthusiastic users of the project, not contributors. Avoid development artifacts such as PR numbers, internal filenames, CI details, or implementation minutiae unless they are necessary to explain a user-visible change.

## Voice and Style

Prefer the house style shown by recent Lemonade announcements:

- Start with `## Lemonade vX.Y.Z` or `## Lemonade vX.Y` and a short `@everyone` opener.
- Sound like a maintainer posting in Discord: upbeat, direct, and conversational, not corporate or exhaustive.
- Keep sections short. Use 2-5 major headings for the release unless the release is unusually large.
- Use feature headings that name the user-visible theme, such as `Omni Models`, `Auto Tuning`, `Cross-Vendor Support`, or `Fedora 43 is back!`.
- Explain the user value first, then add one or two concrete commands, links, or examples only when they help users try the feature.
- Put credits naturally in the paragraph or bullet where the work is discussed. Use `Thanks @handle...` or `by @handle...` rather than a separate long contributor roll.
- Use `Additional Improvements` for compact bullets. Mention contributors inline in those bullets when appropriate.
- End with a full release notes link when the release tag is known, but vary the closing sentence from prior announcements. Never copy an earlier final thank-you or release-notes sentence verbatim.
- Avoid long tables, exhaustive changelogs, CI details, raw commit counts, PR numbers, and formal “Security/Performance/Reliability” taxonomies unless the supplied changes clearly justify those as user-facing themes.
- Do not include emojis in every heading. Use them only if they fit the style of the specific release.

## Grouping

- If the release includes user-relevant breaking changes, add a `Breaking Changes` section before the first feature section.
- Omit `Breaking Changes` entirely when there are no actual breaking changes.
- In `Breaking Changes`, explain each breaking change at a user level and include required migration or compatibility action when known.
- Create one heading per major feature in the release, but use a high bar for headline sections.
- A change merits its own heading only when it is likely to be exciting or immediately useful to enthusiast users, such as a new backend, major app/workflow capability, important platform support, a notable model/omni feature, or a highly visible compatibility win.
- Do not create a heading just because a change is technically solid, contributor-noteworthy, or well-reviewed. Internal fixes, telemetry/stat accuracy, CI/release infrastructure, docs, small compatibility fixes, persistence fixes, and reliability polish usually belong under `Additional Improvements`.
- If there is only one truly headline-worthy feature, make one feature section plus `Additional Improvements`. Do not pad the announcement with extra headline sections.
- Decide major features by user-visible scope, user excitement, and release communication prominence, not implementation effort.
- Coalesce multiple commits into one feature when they are highly related.
- Add a final `Additional Improvements` heading for minor features and fixes.
- Under `Additional Improvements`, use one bullet per coalesced feature or fix.

## Website Release Highlights Artifact

The website release highlights artifact is small website metadata, not release notes, not a changelog, and not the Discord announcement. It must contain exactly these sections, in this order:

```markdown
## Headline

- Crisp user-facing headline.
- Crisp user-facing headline.
- Crisp user-facing headline.

## Breaking Changes

- Concise breaking change and migration pointer when needed.
```

`## Headline` is required and must contain a single-depth bulleted list, because lemonade-server.ai looks for that pattern.

Do not include any other content in this artifact. In particular:

- Do not include a title such as `### vNext`.
- Do not include `---` separators.
- Do not include a body section, feature sections, contributor credits, explanations, or additional notes.
- Do not include any heading except exactly `## Headline` and `## Breaking Changes`.

Headline rules:

- Include the 3-5 most noteworthy aspects of the release that users should know about.
- Keep items crisp, concise, direct, and user-facing.
- Match the style and level of abstraction of the provided GitHub Headline and Breaking Changes references where possible.
- Do not include meta information, contributor shout outs, PR numbers, internal filenames, implementation details, links, markdown emphasis, inline code, or other special formatting.
- Do not use casual hype words such as `finally`, `huge`, `massive`, `awesome`, `fresh`, or `glow up`. Website release highlights should read like polished product copy, not a Discord post.

Breaking Changes rules:

- Include `## Breaking Changes` after `## Headline`.
- Use one bullet per actual user-relevant breaking change.
- Keep bullets concise.
- Link to a wiki article for migration only if more details are needed.
- If there are no breaking changes, leave the section present with no bullets. Do not write `None this release`.

The Discord announcement may have a richer structure and may omit its own `Breaking Changes` section when there are no breaking changes, but it should use the same subject ordering as the website release highlights artifact.

## Announcement Editorial Rules

The Discord announcement is an editorial post, not a complete summary.

- Announcement sections should be reserved for headline candidates that users may want to read a paragraph about or try immediately.
- Lower-salience headline candidates should usually become bullets under `Additional Improvements`.
- You may omit commits entirely when they are not meaningful to users.
- Group work by user-facing outcome, not by commit, PR, author, subsystem, or implementation area.
- CI, build, test-only, formatting, and workflow-only changes are usually evidence, not announcement material.
- If CI/build/release infrastructure changes materially improve install reliability, platform availability, or contributor experience, aggregate them into one broad bullet.
- Several small fixes in one area should become one broader bullet.
- A standalone bullet needs a user-visible reason to exist.
- Do not create a section or bullet just to account for every commit.

## Shout Outs

Use shout outs from the underlying commit reviews, but keep the high bar:

- Include a shout out only for exceptional involvement beyond routine review.
- Do not invent shout outs.
- Place shout outs under the relevant feature heading.
- Do not repeat the same shout out excessively; combine where sensible.

## Discord Announcement Output Format

Use Discord-friendly Markdown:

```markdown
# Release Highlights

## Breaking Changes

- Concise user-level breaking change and what users need to do.

## Major Feature Name

One short paragraph explaining the feature at a user level.

Shout out: @handle for ...

## Additional Improvements

- Concise one-sentence user-level explanation. Shout out: @handle for ...
```

Only include the `Breaking Changes` section when it has at least one real item.

Do not include a verdict, internal evidence, raw PR lists, or exhaustive commit inventories.
