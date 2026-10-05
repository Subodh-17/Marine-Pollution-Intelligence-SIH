"""Repo-relative path helper for checkpoint/training/evaluation metadata.

Used by app/ml/train.py and app/ml/evaluate_real.py so `dataset_root`,
`checkpoint_dir`, `checkpoint_path`, `manifest_dir`, and `test_manifest`
written into TrainingConfig / evaluation_report.json record a portable
path instead of the training machine's absolute path (which broke on any
other machine and leaked local directory layout - see work/FINAL_AUDIT.md
item 1 and scripts/sanitize_metadata_paths.py, which cleaned up the
metadata this had already written before this fix existed).
"""
import os


def portable_path(path: str) -> str:
    """Repo-relative (forward-slashed) when `path` is inside this checkout;
    otherwise an absolute path, since a path genuinely outside the repo
    (e.g. a dataset mounted elsewhere) cannot be expressed relatively and
    a bare machine-specific string presented as portable would be worse
    than an honest absolute one."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    abs_path = os.path.abspath(path)
    try:
        rel = os.path.relpath(abs_path, repo_root)
    except ValueError:
        return abs_path  # different drive on Windows - relpath can't express it
    return rel.replace(os.sep, "/") if not rel.startswith("..") else abs_path
