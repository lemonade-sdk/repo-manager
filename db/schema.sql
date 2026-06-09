CREATE TABLE IF NOT EXISTS commit_reviews (
  repo TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  branch TEXT,
  tag_start TEXT,
  pr_number INTEGER,
  author TEXT,
  summary TEXT,
  verdict TEXT,
  verdict_reason TEXT,
  maintainer_todos TEXT NOT NULL DEFAULT '',
  shout_outs TEXT NOT NULL DEFAULT '',
  raw_output TEXT NOT NULL,
  json_path TEXT NOT NULL DEFAULT '',
  reviewed_at TEXT NOT NULL,
  skill_version TEXT NOT NULL,
  rubric_version TEXT NOT NULL,
  PRIMARY KEY (repo, commit_sha, rubric_version)
);

CREATE INDEX IF NOT EXISTS idx_commit_reviews_repo_reviewed_at
ON commit_reviews (repo, reviewed_at);

CREATE INDEX IF NOT EXISTS idx_commit_reviews_repo_verdict
ON commit_reviews (repo, verdict);

CREATE TABLE IF NOT EXISTS release_reviews (
  repo TEXT NOT NULL,
  branch TEXT NOT NULL,
  tag_start TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  verdict TEXT,
  raw_output TEXT NOT NULL,
  json_path TEXT NOT NULL DEFAULT '',
  reviewed_at TEXT NOT NULL,
  skill_version TEXT NOT NULL,
  rubric_version TEXT NOT NULL,
  PRIMARY KEY (repo, branch, tag_start, head_sha, rubric_version)
);

CREATE TABLE IF NOT EXISTS release_announcements (
  repo TEXT NOT NULL,
  branch TEXT NOT NULL,
  tag_start TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  raw_output TEXT NOT NULL,
  markdown_path TEXT NOT NULL DEFAULT '',
  generated_at TEXT NOT NULL,
  skill_version TEXT NOT NULL,
  PRIMARY KEY (repo, branch, tag_start, head_sha, skill_version)
);
