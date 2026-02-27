from app.rsync_planner import _find_conflicts


def test_find_conflicts_intersection() -> None:
    a_to_b = {
        "/root/files": [
            {"type": "update", "path": "a.txt"},
            {"type": "add", "path": "shared.txt"},
        ]
    }
    b_to_a = {
        "/root/files": [
            {"type": "update", "path": "shared.txt"},
            {"type": "add", "path": "b.txt"},
        ]
    }

    conflicts = _find_conflicts(a_to_b, b_to_a)
    assert conflicts == [
        {
            "root": "/root/files",
            "path": "shared.txt",
            "choices": ["keep_a", "keep_b", "keep_both"],
        }
    ]
