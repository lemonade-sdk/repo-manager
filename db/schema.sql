CREATE TABLE IF NOT EXISTS commit_reviews (
  repo TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  branch TEXT,
  tag_start TEXT,
  range_start TEXT NOT NULL DEFAULT '',
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
  range_start TEXT NOT NULL DEFAULT '',
  head_sha TEXT NOT NULL,
  verdict TEXT,
  raw_output TEXT NOT NULL,
  json_path TEXT NOT NULL DEFAULT '',
  reviewed_at TEXT NOT NULL,
  skill_version TEXT NOT NULL,
  rubric_version TEXT NOT NULL,
  PRIMARY KEY (repo, branch, tag_start, rubric_version)
);

CREATE TABLE IF NOT EXISTS release_announcements (
  repo TEXT NOT NULL,
  branch TEXT NOT NULL,
  tag_start TEXT NOT NULL,
  range_start TEXT NOT NULL DEFAULT '',
  head_sha TEXT NOT NULL,
  raw_output TEXT NOT NULL,
  markdown_path TEXT NOT NULL DEFAULT '',
  release_highlights_output TEXT NOT NULL DEFAULT '',
  release_highlights_path TEXT NOT NULL DEFAULT '',
  generated_at TEXT NOT NULL,
  skill_version TEXT NOT NULL,
  PRIMARY KEY (repo, branch, tag_start, skill_version)
);

CREATE TABLE IF NOT EXISTS review_todos (
  todo_id TEXT PRIMARY KEY,
  review_kind TEXT NOT NULL,
  review_key TEXT NOT NULL,
  todo_index INTEGER NOT NULL,
  todo_text TEXT NOT NULL,
  completed INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_todos_review
ON review_todos (review_kind, review_key);

CREATE TABLE IF NOT EXISTS review_read_states (
  review_key TEXT PRIMARY KEY,
  is_read INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
