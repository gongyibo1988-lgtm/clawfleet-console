from pathlib import Path

from app.cron_manager import build_local_output_path, is_safe_output_path


def test_is_safe_output_path() -> None:
    assert is_safe_output_path("/root/files/reports/daily.md")
    assert is_safe_output_path("/tmp/x.log")
    assert not is_safe_output_path("relative/path.md")
    assert not is_safe_output_path("/root/files/../secret.txt")
    assert not is_safe_output_path("/root/files/image.png")


def test_build_local_output_path() -> None:
    root = Path("/tmp/project")
    local = build_local_output_path(root, "广州服务器", "/root/files/reports/daily.md")
    assert str(local).startswith("/tmp/project/.cache/cron_outputs/")
    assert local.suffix == ".md"
