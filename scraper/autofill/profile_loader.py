"""Loads and validates application_profile.yaml.

Refuses to run if any field is still a FILL_IN placeholder, rather than
silently submitting a real application with missing data.
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import APPLICATION_PROFILE_PATH

_PLACEHOLDER = "FILL_IN"


class ProfileError(Exception):
    pass


def _find_placeholders(node, path=""):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_find_placeholders(value, f"{path}.{key}" if path else key))
    elif isinstance(node, str) and node == _PLACEHOLDER:
        found.append(path)
    return found


def load_profile() -> dict:
    if not APPLICATION_PROFILE_PATH.exists():
        raise ProfileError(
            f"No application profile found at {APPLICATION_PROFILE_PATH}. "
            "Create it before running autofill."
        )

    with open(APPLICATION_PROFILE_PATH, "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)

    placeholders = _find_placeholders(profile)
    if placeholders:
        raise ProfileError(
            "application_profile.yaml still has unfilled fields — fill these in "
            f"before running autofill:\n  " + "\n  ".join(placeholders)
        )

    return profile


def resolve_resume_path(profile: dict, variant: str) -> Path:
    """Resolve a suggested_resume variant (Mobile/AI/Frontend/General) to an
    actual file path, and verify the file exists — fail loudly rather than
    silently uploading nothing or the wrong resume."""
    resumes = profile.get("resumes", {})
    by_variant = resumes.get("by_variant", {})
    filename = by_variant.get(variant) or by_variant.get("General")
    if not filename:
        raise ProfileError(f"No resume mapped for variant '{variant}' and no General fallback configured.")

    path = Path(resumes["dir"]) / filename
    if not path.exists():
        raise ProfileError(f"Resume file not found: {path}")
    return path
