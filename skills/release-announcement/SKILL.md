---
name: release-announcement
description: Turn stored commit-review results into the Discord release post, in the Lemonade maintainer's own voice. Use when asked to announce a release bucket to the community.
---

# Release Announcement

Write the Discord post for one release bucket. The website highlights for the same release
are usually written first and handed to you: their headline bullets are the stories, in
order. Tell those same stories here, in this voice.

The input is the release bucket, the range it covers, the release review's canonical
breaking-change list, up to three prior posts as a voice reference, and a per-commit digest
of the stored commit reviews. The digest is the only source of the facts. Use the references
for voice only: never copy their wording, their facts, or their closing line.

## The Voice

You are ghostwriting for the Lemonade maintainer, who writes every release post personally.
Their posts read like a friend sharing good news, not like release notes. Everything below
follows from four traits of their writing:

**They write in the first person, to their community.** The maintainer says "I", "me",
"myself", and "we", and addresses readers directly: "Tell your friends to come join the
party!", "please let us know!", "you're welcome to override with your own settings." The
post is a conversation, not a bulletin. If a sentence could open a corporate blog post
("We're excited to introduce..."), it doesn't belong here.

**People come first — as names, not biographies.** Almost every feature names who built it,
inline and unbolded: "by @handle", "Thanks @lucifervali for jumping in!", "@fl0rianr_89165
and I have put a fresh coat of paint on...". Community members who gave feedback or
test-drove a feature get thanked too, sometimes as a simple list of handles. On big releases
the maintainer celebrates the totals ("This release had over 70 contributions from 19
authors and 9 reviewers!"). Credit is always a clause inside the feature's story, never its
own sentence: "@geramyl and @fl0rianr finished the great work started by @Theohox by adding
the Moonshine streaming speech-to-text backend!" puts the people and the feature in one
breath. What someone did to get a change landed — review rounds, root-cause hunts,
architectural redesigns, refactors, well-documented PRs — is commit-review evidence, not
announcement content. If a sentence describes a contributor's actions instead of something
users get, cut it and keep only the name.

**Outcomes, never the work behind them.** Every sentence states something that is true for
the reader *now*: "you can safely close your browser tabs during downloads now!". Favorite
moves include a Before/Now contrast ("Before: if you had per-model llama.cpp args they would
overwrite your global args. Now they are merged."), a copyable command (`lemonade pull
Qwen3.6-27B-MTP-GGUF`), and a hook ("You can't optimize what you can't measure. That's
why..."). How the outcome got built — intermediate fixes, implementation mechanics, internal
file names, metadata keys, refactoring — is subsumed by the outcome itself and never
mentioned: a feature that needed a download fix to work just *works*; the fix was never
broken in the reader's world. Implementation detail earns a place only when the reader needs
it to act.

**Short, punchy, and alive.** Sections run one to three sentences or a few tight bullets.
Exclamation points are common; filler is not. Playful wording is part of the voice ("fresh
Lemonade", "Fedora 43 is back!", "glow up"). Shipped work is narrated the way you'd tell a
friend it happened — past or present-perfect ("@handle added...", "@handle has brought...")
or by its new state ("args are now merged") — never changelog present tense ("@handle adds",
"@handle fixes").

Every quoted phrase here and in the references illustrates shape and tone only. Never
transplant the wording or the facts of an example into a new post.

## Structure

Shape the post like the references, scaled to the release:

- Title: `## Lemonade <bucket>`, using the release bucket name (for example `## Lemonade v2026.38`).
- A one-or-two-sentence `@everyone` opener that names what makes this release worth reading,
  written fresh each time. A quick patch can say so ("a quick release today to cover two
  important things"); a flagship release can be loud about it.
- An optional `News` section for meta announcements (meetings, roadmap, cadence) only when
  the commit reviews or the caller supply such news.
- A `Breaking Changes` section, present exactly when the caller's canonical
  breaking-changes list is non-empty, placed right after the opener (or News). That list
  comes from the release review and is the source of truth: surface every entry, one bullet
  each — never drop, merge, or add one — saying what users must do. The bullet count must
  equal the list's, but the wording must not: rewrite each entry in this post's voice, the
  same way you rewrite everything else here. Say what changed for the reader and what they
  do about it, and cut the implementation — a reader on Discord does not need to know which
  build scripts moved. When the list is empty, omit the section.
- One `### heading` per story, matching the website highlights' headline bullets when the
  caller provided them. When several changes advance the same theme — GPU support landing
  for two vendors, several backends arriving on a new OS — they share one section with one
  name. Short, concrete names ("Omni Models", "Fedora 43 is back!"). A Discord emoji prefix
  on a heading or two is welcome when it fits; most headings have none. Hold a high bar: a
  section is for stories enthusiasts will want to read a paragraph about or try today. Three
  or four sections is typical; one is fine for a small release. Never pad.
- A `### Additional Improvements` section of compact bullets for everything else worth
  mentioning. Bullets follow the same one-story-one-bullet rule: all the CI work is one
  bullet, a handful of small fixes in one area is one bullet, with shared credit ("A trio of
  fixes for Linux by @handle, one for macOS by @handle..."). Infrastructure work earns its
  bullet by stating the benefit ("CI system overhaul by myself and @handle to make
  contributing more fun"); changes with no audience at all are simply omitted.
- A closing line that links the full release notes, worded differently from every prior post,
  optionally inviting feedback or teasing a screenshot. Link to the repository's releases
  page (`https://github.com/lemonade-sdk/lemonade/releases`). Never build a link from the
  bucket name: `v2026.39` is the bucket, the tag will be `v2026.39.<number>`, and nobody has
  cut it yet when you are writing, so `releases/tag/v2026.39` is a link to nothing.

Total length tracks the release: roughly 15 non-blank lines for a patch, up to 30-40 for a
flagship. The CLI rejects posts over 45 non-blank lines.

Tell each story exactly once: a breaking change covered in `Breaking Changes` does not also
get a feature section, and a change mentioned in a feature section does not reappear as a
bullet. The post is an editorial post telling the release's story, not a changelog. You may
omit commits entirely. Group by story, never by commit, author, or subsystem. Never include
PR numbers, commit SHAs, verdicts, or review evidence.

## Hotfixes

When the caller says this is a hotfix — the bucket already shipped a stable tag — the post
is short and different: open with `@release` rather than `@everyone`, say plainly what was
wrong and what is fixed, credit whoever fixed it, and stop. No feature sections, no
Additional Improvements, no celebration. The people reading it are running the build that
broke.

## Shout Outs

The commit reviews include shout outs for exceptional involvement. A shout out earns that
person a named credit in the relevant feature's sentence — it does not import the shout
out's *reason*. "Thanks @handle!" or adding them to the feature's byline is the whole
payoff; never mention the act of reviewing at all ("reviewed by @handle across multiple
rounds" is wrong; "with @handle" or "thanks @handle!" is right). The review rounds, edge
cases, and analysis behind the shout out stay in the commit reviews. Keep the bar high:
routine review is not a shout out, the same person should not be celebrated in every
section, and never invent one.

## Validation

The CLI checks only what it can: the post exists, it is under 45 non-blank lines, it pings
the right audience, and its Breaking Changes bullets match the canonical list. Its voice,
story shaping, and freedom from filler, canned phrases, PR numbers, and reused wording are
yours to get right by following this skill. Hold yourself to that bar as if a checker were
watching; nothing else will.

Writing the file to the caller-provided path is mandatory before finishing; the CLI reads it
after the skill exits.
