"""Running a skill, and refusing to store what it produced until it validates.

Every artifact repo-manager writes comes from one of these runs. The retry loop is the
important part: a failed validation is handed back to the model as the list of problems
plus its own previous attempt, and the loop keeps the attempts in memory — nothing
half-validated is ever written into the state directory.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def package_root():
    """Where the bundled skills and scripts live, in a checkout or an install."""
    override = os.environ.get("REPO_MANAGER_HOME")
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve().parent.parent
    if (here / "skills").exists():
        return here
    installed = Path(sys.prefix) / "share" / "repo-manager"
    if (installed / "skills").exists():
        return installed
    return Path.cwd()


def skill_path(name):
    path = package_root() / "skills" / name / "SKILL.md"
    if not path.exists():
        raise SystemExit(f"Skill not found: {path}")
    return path


def scripts_dir():
    return package_root() / "scripts"


def cache_dir():
    """Where fetched project docs are cached. Never inside the state directory: that is a
    git checkout whose every file is an artifact, and a doc cache is neither."""
    override = os.environ.get("REPO_MANAGER_CACHE_DIR")
    if override:
        return Path(override).resolve()
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base).resolve() / "repo-manager"


def model():
    return os.environ.get("REPO_MANAGER_PI_MODEL", "")


# Where a run's wall clock went. Populated per `run_pi` call and read by the caller
# immediately after.
RUN_STATS = {}


def reset_stats():
    global RUN_STATS
    RUN_STATS = {"tool_seconds": 0.0, "tool_calls": {}, "tool_errors": 0, "generated_chars": 0}


def environment_block(checkout=""):
    lines = [
        "## This environment",
        f"Bundled helper scripts are at: {scripts_dir()}",
        "Invoke them by absolute path, for example "
        f"`{scripts_dir()}/get-commit-diff.sh OWNER/REPO SHA`.",
    ]
    if checkout:
        lines.append(f"A git clone of the repository under review is at: {checkout}")
    return "\n".join(lines) + "\n\n"


def run_pi(skill_name, prompt, cwd, checkout="", base_ref=""):
    """One `pi` run with the skill as its system prompt. Returns the assistant text.

    The skill is appended to the system prompt rather than offered with `--skill`: a run that
    never opened its skill writes whatever shape it last saw, and `--no-skills` keeps the
    other skills in this package from being listed alongside it.
    """
    pi = shutil.which("pi")
    if not pi:
        raise SystemExit("`pi` was not found on PATH. Run `repo-manager pi setup` or install pi.")
    env = dict(os.environ)
    env["REPO_MANAGER_CACHE_DIR"] = str(cache_dir())
    env["REPO_MANAGER_SCRIPTS"] = str(scripts_dir())
    if checkout:
        env["REPO_MANAGER_CHECKOUT"] = str(Path(checkout).resolve())
    if base_ref:
        env["REPO_MANAGER_BASE_REF"] = base_ref
    cache_dir().mkdir(parents=True, exist_ok=True)
    cmd = [pi, "--mode", "json", "--no-skills", "--append-system-prompt", str(skill_path(skill_name))]
    if model():
        cmd += ["--model", model()]

    body = environment_block(checkout) + prompt
    saw_text = False
    assistant_text = []

    def feed_prompt(proc):
        try:
            proc.stdin.write(body)
            proc.stdin.close()
        except BrokenPipeError:
            pass

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None, text=True, bufsize=1,
        )
        writer = threading.Thread(target=feed_prompt, args=(proc,), daemon=True)
        writer.start()
        print(f"Running Pi skill: {skill_name}", flush=True)
        reset_stats()
        tool_started, tool_open = None, ""
        for line in proc.stdout:
            # Cheap substring guard first: text_delta events are by far the most numerous and
            # none of them need parsing for stats.
            if "tool_execution_" in line:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {}
                kind = event.get("type")
                name = event.get("toolName", "tool")
                if kind == "tool_execution_start":
                    tool_started, tool_open = time.monotonic(), name
                elif kind == "tool_execution_end":
                    if tool_started is not None:
                        RUN_STATS["tool_seconds"] += time.monotonic() - tool_started
                        tool_started = None
                    RUN_STATS["tool_calls"][name] = RUN_STATS["tool_calls"].get(name, 0) + 1
                    if event.get("isError"):
                        RUN_STATS["tool_errors"] += 1
            rendered = render_event(line)
            if rendered:
                saw_text = True
                assistant_text.append(rendered)
                RUN_STATS["generated_chars"] += len(rendered)
        returncode = proc.wait()
        writer.join(timeout=1)
    except KeyboardInterrupt:
        if "proc" in locals() and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise SystemExit(130)
    if saw_text:
        print()
    if returncode != 0:
        raise PiFailed(f"Pi exited with code {returncode} before completing {skill_name}.")
    return "".join(assistant_text)


class PiFailed(RuntimeError):
    pass


def truncate(value, limit=180):
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def summarize_args(tool_name, args):
    if not isinstance(args, dict):
        return ""
    for key in ("cmd", "command", "path", "file_path", "pattern", "query"):
        if args.get(key):
            return truncate(args[key])
    if tool_name == "bash" and args.get("args"):
        return truncate(args["args"])
    return ""


def render_event(line):
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        text = line.strip()
        if text:
            print(text, flush=True)
            return text + "\n"
        return ""
    kind = event.get("type")
    if kind == "agent_start":
        print("Pi started.", flush=True)
    elif kind == "agent_end":
        print("\nPi finished.", flush=True)
    elif kind == "tool_execution_start":
        name = event.get("toolName", "tool")
        detail = summarize_args(name, event.get("args"))
        print(f"\nRunning {name}: {detail}" if detail else f"\nRunning {name}", flush=True)
    elif kind == "tool_execution_end":
        name = event.get("toolName", "tool")
        print(f"{name} {'failed' if event.get('isError') else 'done'}.", flush=True)
    elif kind == "message_update":
        assistant = event.get("assistantMessageEvent") or {}
        if assistant.get("type") == "text_delta":
            delta = assistant.get("delta", "")
            print(delta, end="", flush=True)
            return delta
        if assistant.get("type") == "error":
            message = (assistant.get("error") or {}).get("errorMessage")
            if message:
                print(f"\nPi error: {message}", flush=True)
    return ""


def extract_json_object(text):
    stripped = (text or "").strip()
    if not stripped:
        return None
    candidates = [stripped]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def feedback_block(errors, previous):
    """What a failed attempt hands the next one: the problems, then its own last output."""
    sections = []
    for name, text in (previous or {}).items():
        if (text or "").strip():
            sections.append(f"Previous attempt ({name}):\n```\n{text.strip()}\n```")
    listed = "\n".join(f"- {error}" for error in errors)
    return (
        "A previous attempt at this task failed validation. Fix every problem listed below and "
        "write corrected files to the paths given above.\n"
        f"Validation problems:\n{listed}\n\n" + "\n\n".join(sections) + "\n\n"
    )


def generate(skill, outputs, build_prompt, validate, checkout="", attempts=3, base_ref="",
             notes=None):
    """Run a skill until what it wrote validates, or until the attempts run out, and return
    the best artifact it produced.

    `outputs` maps a name to a filename suffix; each attempt gets a fresh path per name
    inside a scratch directory, so nothing reaches the state directory until this returns.
    `build_prompt(paths, feedback)` writes the prompt, `validate(contents)` returns
    `(value, errors)`.

    Validation guides; it does not gate. Each failed attempt is handed back to the skill with
    the problems listed, and an artifact that still has problems on the last attempt is
    returned anyway, with those problems appended to `notes` for the caller to store on it.
    A reader is better served by a review that says what its checks could not confirm than
    by no review at all. The only way out with nothing is nothing to return: Pi failing to
    run, or no attempt producing a parseable artifact.
    """
    with tempfile.TemporaryDirectory(prefix="repo-manager-") as work:
        work = Path(work)
        feedback = ""
        best = None
        for attempt in range(1, attempts + 1):
            paths = {name: work / f"{name}.{attempt}{suffix}" for name, suffix in outputs.items()}
            prompt = build_prompt(paths, feedback)
            try:
                output = run_pi(skill, prompt, work, checkout=checkout, base_ref=base_ref)
            except PiFailed as exc:
                if attempt == attempts:
                    raise SystemExit(str(exc))
                print(f"{exc} Retrying with the same instructions.", flush=True)
                continue
            # A model that answered in the chat instead of writing the file has still done
            # the work; salvage it when the artifact is a single JSON object.
            if len(paths) == 1:
                only = next(iter(paths.values()))
                if not only.exists() and only.suffix == ".json":
                    parsed = extract_json_object(output)
                    if parsed is not None:
                        only.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
                        print(f"Recovered the artifact from Pi's output: {only.name}", flush=True)
            contents = {
                name: (path.read_text(encoding="utf-8") if path.exists() else "")
                for name, path in paths.items()
            }
            missing = [
                f"Expected file was not created: {path}" for name, path in paths.items()
                if not path.exists()
            ]
            value, errors = (None, missing) if missing else validate(contents)
            if not errors:
                return value
            if value is not None:
                best = (value, errors)
            listed = "\n".join(f"- {error}" for error in errors)
            if attempt < attempts:
                print(f"\nAttempt {attempt} failed validation; asking Pi to revise:\n{listed}\n",
                      flush=True)
                feedback = feedback_block(errors, contents)
    if best is None:
        raise SystemExit(f"{skill} produced no usable artifact in {attempts} attempts:\n{listed}")
    value, errors = best
    print(f"\n{skill}: kept the last usable attempt with {len(errors)} unresolved problem(s):\n"
          + "\n".join(f"- {error}" for error in errors), flush=True)
    if notes is not None:
        notes.extend(errors)
    return value
