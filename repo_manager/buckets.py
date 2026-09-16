"""Release buckets: `v<year>.<week>`, the two-component prefix of every shipped version.

A bucket is what repo-manager keys its state on. The final `.number` is unknown until a
human tags a candidate, so nothing stored here can depend on it. Several buckets are live
at once — the one accumulating on `main`, the one under test on a release branch, and any
older branch taking a hotfix — which is why every artifact carries its bucket rather than
the tool inferring one global "current release".

Which week `main` is accumulating toward is lemonade's decision, not repo-manager's: the
cutoff lives in lemonade's `tools/version.py`, and repo-manager loads that file from the
tracked checkout and asks it. There is no copy of the cutoff here to drift.
"""

import datetime
import re
import types


RELEASE_BRANCH_PATTERN = re.compile(r"^release-v(\d{4})\.(\d{1,2})$")
RELEASE_TAG_PATTERN = re.compile(r"^v(\d{4})\.(\d{1,2})\.(\d+)$")
# Any version tag, of either the old `v11.9.0` shape or the new `v2026.38.1` one. The
# anchors are what keep `candidate-v2026.38.1` out of the tag list: a candidate is not a
# release, and ranging from one would silently drop a week of commits.
VERSION_TAG_PATTERN = re.compile(r"^v\d+(\.\d+)+$")


VERSION_TOOL = "tools/version.py"


def load_version_tool(checkout):
    """Lemonade's `tools/version.py`, as a module, from the tip of `main` in the checkout."""
    for ref in ("origin/main", "main"):
        result = checkout.git("show", f"{ref}:{VERSION_TOOL}", check=False)
        if result.returncode == 0:
            break
    else:
        raise SystemExit(
            f"{VERSION_TOOL} was not found on main in {checkout.path}; the bucket on main is "
            "computed by that file, so repo-manager cannot tell which release it is tracking."
        )
    module = types.ModuleType("lemonade_version")
    exec(compile(result.stdout, f"{checkout.path}/{VERSION_TOOL}", "exec"), module.__dict__)
    return module


def upcoming_release_week(checkout, now):
    """(year, week) of the release the given moment is accumulating toward, as lemonade's
    `tools/version.py` computes it from its release cutoff."""
    return load_version_tool(checkout).upcoming_release_week(now)


def release_branch_name(year, week):
    return f"release-v{year}.{week}"


def bucket_name(year, week):
    return f"v{year}.{week}"


def bucket_for_branch(branch, checkout=None, now=None):
    """The bucket a branch is building.

    A `release-v<y>.<w>` branch names its own bucket. Any other branch — `main`, or a topic
    branch someone is testing from — is accumulating toward the upcoming release week, which
    only lemonade's version tool in the checkout can name.
    """
    match = RELEASE_BRANCH_PATTERN.fullmatch(str(branch or ""))
    if match:
        return bucket_name(int(match.group(1)), int(match.group(2)))
    if checkout is None:
        raise ValueError(f"A checkout is needed to name the bucket {branch!r} is building.")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return bucket_name(*upcoming_release_week(checkout, now.astimezone(datetime.timezone.utc)))


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
