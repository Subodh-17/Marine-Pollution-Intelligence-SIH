"""
Sanitize absolute local-machine paths out of shipped checkpoint metadata.

    python -m scripts.sanitize_metadata_paths

WHY: the training machine's absolute path (e.g. `E:\\gitfinal\\...`) got
written into `evaluation_report.json`/`.md` and the `*_training_config.json`
/ `*_dataset_manifest.json` sidecar files. That breaks portability (the path
doesn't exist on any other machine) and leaks local directory layout.

WHAT THIS DOES: rewrites exactly the path-shaped fields
(`checkpoint_path`, `dataset_root`, `manifest_dir`, `checkpoint_dir`,
`test_manifest`) in the JSON metadata files, and the one path mention in
evaluation_report.md, to be relative to the repository root. It does NOT
touch metrics, hashes, dates, class mappings, or any other field, and it
does NOT touch the checkpoint files themselves.

Every substitution is logged to work/PATH_SANITIZATION_LOG.json so the
change is auditable rather than silent.
"""
from __future__ import annotations

import json
import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CKPT_DIR = os.path.join(REPO_ROOT, "backend", "data", "ml_checkpoints")

# Fields known to hold filesystem paths in these specific sidecar files.
PATH_FIELDS = {"checkpoint_path", "dataset_root", "manifest_dir", "checkpoint_dir", "test_manifest"}

JSON_FILES = [
    "evaluation_report.json",
    "classifier_real_training_config.json",
    "classifier_real_dataset_manifest.json",
    "spill_unet_real_training_config.json",
    "spill_unet_real_dataset_manifest.json",
]

# Matches an absolute Windows path so we can find the tail we should keep
# (everything from "backend" onward) regardless of the drive/prefix used on
# the machine that produced it.
_WINDOWS_ABS = re.compile(r"^[A-Za-z]:\\\\?.*?(?=backend)", re.IGNORECASE)


def _to_relative(value: str) -> tuple[str, bool]:
    """Best-effort: keep everything from 'backend' onward, forward-slashed.
    Returns (new_value, changed)."""
    if not isinstance(value, str):
        return value, False
    normalized = value.replace("\\\\", "\\")
    m = _WINDOWS_ABS.match(normalized)
    if not m:
        return value, False
    tail = normalized[m.end():].lstrip("\\/")
    rel = tail.replace("\\", "/").rstrip("/")
    return rel, rel != value


def sanitize_json_file(path: str, log: list) -> None:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = False

    def walk(obj):
        nonlocal changed
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in PATH_FIELDS and isinstance(v, str):
                    new_v, did_change = _to_relative(v)
                    if did_change:
                        log.append({"file": os.path.relpath(path, REPO_ROOT),
                                     "field": k, "old": v, "new": new_v})
                        obj[k] = new_v
                        changed = True
                else:
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.write("\n")


def sanitize_markdown_file(path: str, log: list) -> None:
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
        encoding = "cp1252"
    new_text, n = re.subn(
        r"[A-Za-z]:\\\\?(?:[^`\s\\\r\n]+\\\\?)*backend\\\\?(?:[^`\r\n]*)",
        lambda m: _to_relative(m.group(0))[0],
        text,
    )
    if n:
        log.append({"file": os.path.relpath(path, REPO_ROOT), "field": "(markdown body)",
                     "count": n})
        with open(path, "w", encoding=encoding, newline="\n") as f:
            f.write(new_text)


def main() -> int:
    log: list = []
    for name in JSON_FILES:
        p = os.path.join(CKPT_DIR, name)
        if os.path.exists(p):
            sanitize_json_file(p, log)
    md = os.path.join(CKPT_DIR, "evaluation_report.md")
    if os.path.exists(md):
        sanitize_markdown_file(md, log)

    out_dir = os.path.join(REPO_ROOT, "work")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "PATH_SANITIZATION_LOG.json"), "w") as f:
        json.dump({"changes": log}, f, indent=2)

    for entry in log:
        print(f"sanitized {entry['file']}: {entry.get('field')}")
    print(f"\n{len(log)} field(s) changed. Log: work/PATH_SANITIZATION_LOG.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
