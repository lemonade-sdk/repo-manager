"""Release buckets: `v<year>.<week>`, the two-component prefix of every shipped version.

A bucket is what repo-manager keys its state on. The final `.number` is unknown until a
human tags a candidate, so nothing stored here can depend on it. Several buckets are live
at once — the one accumulating on `main`, the one under test on a release branch, and any
older branch taking a hotfix — which is why every artifact carries its bucket rather than
the tool inferring one global "current release".

`upcoming_release_week` is ported from lemonade's `tools/version.py` so the two agree on
which week a commit belongs to. It is copied, not imported: repo-manager runs from a
different checkout than the repo it tracks, and often with no lemonade checkout at all.
"""

import datetime
import re


RELEASE_BRANCH_PATTERN = re.compile(r"^release-v(\d{4})\.(\d{1,2})$")
RELEASE_TAG_PATTERN = re.compile(r"^v(\d{4})\.(\d{1,2})\.(\d+)$")
# Any version tag, of either the old `v11.9.0` shape or the new `v2026.38.1` one. The
# anchors are what keep `candidate-v2026.38.1` out of the tag list: a candidate is not a
# release, and ranging from one would silently drop a week of commits.
VERSION_TAG_PATTERN = re.compile(r"^v\d+(\.\d+)+$")


def upcoming_release_week(now):
    """(year, week) of the release the given moment is accumulating toward.

    A cron cuts `release-v<year>.<week>` from main every Wednesday at 19:00 UTC, and that
    branch ships the following week. So the bucket on `main` is the ISO week of the Wednesday
    *after* the next cutoff — a commit merged at 18:59 lands in this week's branch, and one
    merged at 19:01 lands in the next.
    """
    days_until_wednesday = (2 - now.weekday()) % 7
    cutoff = (now + datetime.timedelta(days=days_until_wednesday)).replace(
        hour=19, minute=0, second=0, microsecond=0
    )
    if now >= cutoff:
        cutoff += datetime.timedelta(days=7)
    release_date = cutoff + datetime.timedelta(days=7)
    iso_date = release_date.isocalendar()
    return iso_date.year, iso_date.week


def release_branch_name(year, week):
    return f"release-v{year}.{week}"


def bucket_name(year, week):
    return f"v{year}.{week}"


def bucket_for_branch(branch, now=None):
    """The bucket a branch is building.

    A `release-v<y>.<w>` branch names its own bucket. Any other branch — `main`, or a topic
    branch someone is testing from — is accumulating toward the upcoming release week.
    """
    match = RELEASE_BRANCH_PATTERN.fullmatch(str(branch or ""))
    if match:
        return bucket_name(int(match.group(1)), int(match.group(2)))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return bucket_name(*upcoming_release_week(now.astimezone(datetime.timezone.utc)))


def branch_for_bucket(bucket):
    parts = version_parts(bucket)
    if len(parts) < 2:
        raise ValueError(f"Not a bucket: {bucket!r}")
    return release_branch_name(parts[0], parts[1])


def version_parts(tag):
    """Sort key for a version string.

    Numeric, component by component, so `v11.9.0` sorts before `v2026.38` (11 < 2026) and
    `v2026.38` before `v2026.38.2` (a shorter prefix is the earlier version). Both orders
    matter: the tag list spans the old scheme and the new one, and a bucket has to sort
    against the tags inside it.
    """
    return tuple(int(part) for part in re.findall(r"\d+", str(tag or "")))


def is_version_tag(tag):
    return bool(VERSION_TAG_PATTERN.fullmatch(str(tag or "")))


def tag_bucket(tag):
    """The bucket a tag belongs to: its first two components."""
    parts = version_parts(tag)
    if len(parts) < 2:
        return ""
    return bucket_name(parts[0], parts[1])


def sort_tags(tags):
    return sorted((tag for tag in tags if is_version_tag(tag)), key=version_parts)


def range_start(tags, bucket):
    """The newest version tag belonging to a bucket earlier than this one.

    Never this bucket's own tag. On a hotfix or a tag build, bucket `v2026.38` with a stable
    `v2026.38.1` still ranges from the newest `v2026.37`-or-earlier tag, so the range
    describes everything the bucket ships rather than only what came after its first tag.
    """
    current = version_parts(bucket)
    earlier = [tag for tag in sort_tags(tags) if version_parts(tag_bucket(tag)) < current]
    return earlier[-1] if earlier else ""


def stable_tags_in_bucket(tags, bucket):
    """Tags already cut inside this bucket, oldest first — the hotfix signal."""
    return [tag for tag in sort_tags(tags) if tag_bucket(tag) == bucket]
