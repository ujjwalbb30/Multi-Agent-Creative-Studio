"""Fails if a secret or project identifier appears in the repo or in extra files.

Checks every git-tracked file (text and binary), plus any extra paths given on
the command line (e.g. a PR description file), for:
  - token-shaped text: a Notion token prefix followed by 20+ token characters, or an
    HTTP bearer header followed by a 20+ character token (plain words like
    "secret_id" or a placeholder such as "ntn_..." do not match)
  - the actual values of NOTION_TOKEN, NOTION_*_DATABASE_ID (with and without
    dashes), GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_PROJECT_NUMBER and
    GCS_IMAGES_BUCKET, loaded from .env in-process only

It prints only file:line and the NAME of what matched, never the matched text.

Usage (from workshop/starter/):
  uv run python scripts/check_secrets.py [extra files...]
Exit code 1 if anything is found.
"""

import os
import pathlib
import re
import subprocess
import sys

STARTER_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = STARTER_DIR.parents[1]

# Built by concatenation so this file does not match itself.
MARKERS = {
    "notion token": re.compile(rb"ntn" + rb"_[A-Za-z0-9]{20,}"),
    "notion legacy secret": re.compile(rb"secret" + rb"_[A-Za-z0-9]{20,}"),
    "bearer header with token": re.compile(rb"Bear" + rb"er [A-Za-z0-9_.\-]{20,}"),
}
VALUE_VARS = ("NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID",
              "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_NUMBER", "GCS_IMAGES_BUCKET")


def load_values() -> dict[str, bytes]:
    from dotenv import dotenv_values

    env = {**dotenv_values(STARTER_DIR / ".env"), **os.environ}
    values = {}
    for name in VALUE_VARS:
        v = (env.get(name) or "").strip()
        if len(v) >= 6:
            values[name] = v.encode()
            if "DATABASE_ID" in name and "-" in v:
                values[f"{name} (no dashes)"] = v.replace("-", "").encode()
    return values


def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True).stdout
    return [REPO_ROOT / p for p in out.decode().split("\0") if p]


def scan(paths: list[pathlib.Path], values: dict[str, bytes]) -> list[str]:
    needles = {name: re.compile(re.escape(v)) for name, v in values.items()} | MARKERS
    hits = []
    for path in paths:
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        for name, pattern in needles.items():
            for m in pattern.finditer(data):
                line = data.count(b"\n", 0, m.start()) + 1
                try:
                    shown = path.relative_to(REPO_ROOT)
                except ValueError:
                    shown = path
                hits.append(f"{shown}:{line}: {name}")
    return hits


def main() -> int:
    values = load_values()
    paths = tracked_files() + [pathlib.Path(p).resolve() for p in sys.argv[1:]]
    hits = scan(paths, values)
    print(f"check_secrets: scanned {len(paths)} files for {len(MARKERS)} markers and "
          f"{len(values)} configured values ({', '.join(sorted(values)) or 'none set'})")
    if hits:
        print(f"FAIL: {len(hits)} finding(s):")
        for h in hits:
            print(f"  {h}")
        return 1
    print("OK: no secrets or identifiers found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
