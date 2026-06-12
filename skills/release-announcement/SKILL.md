---
name: release-announcement
description: Generate website release highlights and a Discord-friendly release announcement from stored commit-review results. Use when asked to turn release commits into enthusiast-user-facing release communications with feature grouping and shout outs.
---

# Release Announcement

Generate two artifacts from the stored commit-review records for a release. The release tag is the release being announced, such as `v10.7.0`; the range start tag is only the previous release boundary used to select commits.

If the caller provides output file paths, write both artifacts before finishing:

- Website release highlights Markdown to the requested release-highlights path.
- Discord-friendly Markdown announcement to the requested announcement path.

The caller may provide two kinds of references:

- Up to three prior GitHub `## Headline` and `## Breaking Changes` sections. These set the style for the website release highlights artifact.
- Up to three prior Discord release announcements, written by the maintainer personally. These set the voice for the Discord announcement.

Use references for style and voice only. Never copy facts, shout outs, migration notes, or sentences from them; every claim in the new artifacts must come from the current commit-review records.

## Story Plan

Write the story plan first, as a JSON file at the plan path the caller provides:

```json
{
  "stories": [
    {"title": "Omni Models", "section": true, "covers": "what this story includes, briefly"},
    {"title": "Faster Downloads", "section": false, "covers": "..."}
  ],
  "breaking_changes": ["concise user-facing breaking change"]
}
```

- 3-5 stories, ordered by importance. A story is a theme, not a commit: several changes that advance the same outcome — support for more platforms, faster models, a smoother app — are one story told together, however many PRs or authors it took. If two candidate stories would answer the same reader question ("does it run on my hardware?", "what's new for images?"), merge them.
- Each story answers exactly one reader question. A title that needs an "&" or "Improvements" to hold its contents together ("Configuration & CLI Improvements") is not a story — it is either two stories or a handful of `Additional Improvements` bullets. An exciting new capability must never be buried as a bullet inside a catch-all story.
- Work aimed at contributors rather than users — CI, internal refactors, test infrastructure — is never a story and never appears inside a feature section. At most it earns one aggregated `Additional Improvements` bullet framed by its benefit; otherwise omit it.
- `section: true` marks stories that earn a `###` section in the Discord post — something enthusiasts will want to read a paragraph about or try today. `section: false` stories become prominent bullets under `Additional Improvements`. At least one section; three or four is typical.
- `breaking_changes` lists actual user-facing breaking changes, or stays empty.

The plan is the single source of truth for both artifacts: the headline bullets are the stories in order, the Discord feature headings are exactly the section-stories' titles (the CLI rejects mismatches), and each story is told exactly once — a breaking change covered in `Breaking Changes` does not also get a feature section, and a change mentioned in a feature section does not reappear as a bullet.

## The Voice

You are ghostwriting the Discord announcement for the Lemonade maintainer, who writes every release post personally. Their posts read like a friend sharing good news, not like release notes. Everything below follows from four traits of their writing:

**They write in the first person, to their community.** The maintainer says "I", "me", "myself", and "we", and addresses readers directly: "Tell your friends to come join the party!", "please let us know!", "you're welcome to override with your own settings." The post is a conversation, not a bulletin. If a sentence could open a corporate blog post ("We're excited to introduce..."), it doesn't belong here.

**People come first — as names, not biographies.** Almost every feature names who built it, inline and unbolded: "by @handle", "Thanks @lucifervali for jumping in!", "@fl0rianr_89165 and I have put a fresh coat of paint on...". Community members who gave feedback or test-drove a feature get thanked too, sometimes as a simple list of handles. On big releases the maintainer celebrates the totals ("This release had over 70 contributions from 19 authors and 9 reviewers!"). Credit is always a clause inside the feature's story, never its own sentence: "@geramyl and @fl0rianr finished the great work started by @Theohox by adding the Moonshine streaming speech-to-text backend!" puts the people and the feature in one breath. What someone did to get a change landed — review rounds, root-cause hunts, architectural redesigns, refactors, well-documented PRs — is commit-review evidence, not announcement content. If a sentence describes a contributor's actions instead of something users get, cut it and keep only the name.

**Outcomes, never the work behind them.** Every sentence states something that is true for the reader *now*: "you can safely close your browser tabs during downloads now!". Favorite moves include a Before/Now contrast ("Before: if you had per-model llama.cpp args they would overwrite your global args. Now they are merged."), a copyable command (`lemonade pull Qwen3.6-27B-MTP-GGUF`), and a hook ("You can't optimize what you can't measure. That's why..."). How the outcome got built — intermediate fixes, implementation mechanics, internal file names, metadata keys, refactoring — is subsumed by the outcome itself and never mentioned: a feature that needed a download fix to work just *works*; the fix was never broken in the reader's world. Implementation detail earns a place only when the reader needs it to act.

**Short, punchy, and alive.** Sections run one to three sentences or a few tight bullets. Exclamation points are common; filler is not. Playful wording is part of the voice ("fresh Lemonade", "Fedora 43 is back!", "glow up") — in the Discord post only, never in the website highlights. Shipped work is narrated the way you'd tell a friend it happened — past or present-perfect ("@handle added...", "@handle has brought...") or by its new state ("args are now merged") — never changelog present tense ("@handle adds", "@handle fixes").

Every quoted phrase in this skill and in the references illustrates shape and tone only. Never transplant the wording or the facts of an example into a new post; re-derive both from the current commit reviews.

## Discord Announcement Structure

Shape the post like the references, scaled to the release:

- Title: `## Lemonade <release>` (use the release bucket name, e.g. `## Lemonade vNext`).
- A one-or-two-sentence `@everyone` opener that names what makes this release worth reading, written fresh each time. A quick patch can say so ("a quick release today to cover two important things"); a flagship release can be loud about it.
- An optional `News` section for meta announcements (meetings, roadmap, cadence) only when the commit reviews or caller supply such news.
- A `Breaking Changes` section, only when real user-facing breaking changes exist, placed right after the opener (or News). One bullet per change with what users must do.
- One `### heading` per story, not per deliverable. When several changes advance the same theme — GPU support landing for two vendors, several backends arriving on a new OS — they share one section with one name, the way the references roll separate CUDA, Vulkan, and ARM64 work into a single "Cross-Vendor Support" section. Short, concrete names ("Omni Models", "Fedora 43 is back!"). A Discord emoji prefix on a heading or two is welcome when it fits; most headings have none. Hold a high bar: a section is for stories enthusiasts will want to read a paragraph about or try today. Three or four sections is typical; one is fine for a small release. Never pad.
- A `### Additional Improvements` section of compact bullets for everything else worth mentioning. Bullets follow the same one-story-one-bullet rule: all the CI work is one bullet, a handful of small fixes in one area is one bullet, with shared credit ("A trio of fixes for Linux by @handle, one for macOS by @handle..."). Infrastructure work that helps users or contributors earns its bullet by stating the benefit ("CI system overhaul by myself and @handle to make contributing more fun"); changes with no audience at all are simply omitted.
- A closing line that links the full release notes, worded differently from every prior post, optionally inviting feedback or teasing a screenshot.

Total length tracks the release: roughly 15 non-blank lines for a patch, up to 30-40 for a flagship. The CLI rejects posts over 45 non-blank lines.

The announcement is an editorial post telling the release's story, not a changelog. You may omit commits entirely. Group by story, never by commit, author, or subsystem. Never include PR numbers, commit SHAs, verdicts, or review evidence.

## Shout Outs

The commit reviews include shout outs for exceptional involvement. A shout out earns that person a named credit in the relevant feature's sentence — it does not import the shout out's *reason*. "Thanks @handle!" or adding them to the feature's byline is the whole payoff; never mention the act of reviewing at all ("reviewed by @handle across multiple rounds" is wrong; "with @handle" or "thanks @handle!" is right). The review rounds, edge cases, and analysis behind the shout out stay in the commit reviews. Keep the bar high: routine review is not a shout out, the same person should not be celebrated in every section, and never invent one.

## Website Release Highlights Artifact

This artifact is machine-consumed website metadata (lemonade-server.ai parses it), so unlike the Discord post its format is rigid. It must contain exactly these two sections and nothing else — no title, no separators, no body text:

```markdown
## Headline

- Crisp user-facing headline.
- Crisp user-facing headline.
- Crisp user-facing headline.

## Breaking Changes

- Concise breaking change and migration pointer when needed.
```

Headline rules:

- 3-5 bullets covering the most noteworthy aspects of the release, in the same order as the Discord post.
- Each bullet is one short sentence of polished product copy describing what users get, like the GitHub references: "MTP support added for up to 2x performance increase on supported models." Not how it was built.
- Inline code is fine for commands, flags, and model names (`lemonade bench`). No bold, italics, links, @handles, or shout outs.
- The Discord post's playfulness stays out: no "finally", "huge", "massive", "awesome", "fresh", or "glow up" here.

Breaking Changes rules:

- One concise bullet per actual user-relevant breaking change, with the migration action when known.
- If there are no breaking changes, leave the section heading present with no bullets. Do not write "None".

## Validation

The CLI validates both artifacts and will return errors for: malformed highlights structure, formatting or casual wording in headline bullets, bolded @handles, marketing filler phrases, announcements over 45 non-blank lines, and any phrase of 8 or more consecutive words reused from a prior announcement (openers and closings included). If you receive validation feedback, fix the listed problems and rewrite both files.
