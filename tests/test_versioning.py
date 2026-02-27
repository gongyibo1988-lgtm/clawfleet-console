from pathlib import Path

import app.versioning as versioning


def test_get_app_version_reads_file(tmp_path: Path) -> None:
    target = tmp_path / "VERSION"
    target.write_text("1.2.3\n", encoding="utf-8")
    original = versioning.VERSION_FILE
    try:
        versioning.VERSION_FILE = target  # type: ignore[assignment]
        assert versioning.get_app_version() == "1.2.3"
    finally:
        versioning.VERSION_FILE = original  # type: ignore[assignment]


def test_get_app_version_fallback_when_missing(tmp_path: Path) -> None:
    original = versioning.VERSION_FILE
    try:
        versioning.VERSION_FILE = tmp_path / "MISSING"  # type: ignore[assignment]
        assert versioning.get_app_version() == "0.0.0"
    finally:
        versioning.VERSION_FILE = original  # type: ignore[assignment]
