from __future__ import annotations

import argparse
import re
from pathlib import Path


def _normalize_tag(tag: str) -> str:
    tag = tag.strip()
    if not tag:
        raise ValueError("Empty tag")
    if not tag.startswith("v"):
        raise ValueError(f"Expected tag to start with 'v', got: {tag!r}")
    return tag[1:]


def _read_project_version(pyproject_path: Path) -> str:
    """Parse `[project].version` from `pyproject.toml` (no external deps)."""

    text = pyproject_path.read_text(encoding="utf-8")
    in_project = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if not in_project:
            continue
        if not line.startswith("version"):
            continue
        key, _, value = line.partition("=")
        if key.strip() != "version":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        raise ValueError(f"Unrecognized version format in {pyproject_path}: {raw_line!r}")
    raise ValueError(f"Failed to find [project].version in {pyproject_path}")


def _extract_changelog_section(changelog_path: Path, version: str) -> str:
    header_re = re.compile(r"^## \\[(?P<version>[^\\]]+)\\](?:\\s+-\\s+.*)?$")

    lines = changelog_path.read_text(encoding="utf-8").splitlines(keepends=True)
    start: int | None = None
    end: int | None = None

    for idx, line in enumerate(lines):
        m = header_re.match(line.rstrip("\n"))
        if not m:
            continue
        found = m.group("version").strip()
        if start is None:
            if found == version:
                start = idx
            continue
        end = idx
        break

    if start is None:
        raise ValueError(f"Missing changelog entry for version {version} in {changelog_path}")
    section = "".join(lines[start : end if end is not None else len(lines)])
    section = section.strip() + "\n"
    return section


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare GitHub release notes from CHANGELOG.md.")
    parser.add_argument("--tag", required=True, help="Git tag name like v0.1.0")
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="Path to pyproject.toml (default: pyproject.toml)",
    )
    parser.add_argument(
        "--changelog",
        default="CHANGELOG.md",
        help="Path to CHANGELOG.md (default: CHANGELOG.md)",
    )
    parser.add_argument(
        "--out",
        default="release_notes.md",
        help="Output file path (default: release_notes.md)",
    )
    args = parser.parse_args()

    tag = str(args.tag)
    version = _normalize_tag(tag)

    pyproject_path = Path(args.pyproject)
    changelog_path = Path(args.changelog)
    out_path = Path(args.out)

    project_version = _read_project_version(pyproject_path)
    if project_version != version:
        raise SystemExit(
            f"Tag/version mismatch: tag={tag!r} implies version={version!r}, "
            f"but {pyproject_path} has version={project_version!r}"
        )

    notes = _extract_changelog_section(changelog_path, version)
    out_path.write_text(notes, encoding="utf-8")
    print(f"Wrote release notes to {out_path}")


if __name__ == "__main__":
    main()
