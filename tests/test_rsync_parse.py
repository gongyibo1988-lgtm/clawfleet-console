from app.rsync_planner import parse_itemized_changes


def test_parse_itemized_changes_add_update_delete() -> None:
    text = """sending incremental file list
>f+++++++++ new.txt
>f.st...... changed.txt
deleting old.txt

sent 100 bytes  received 20 bytes  total size 20
"""
    parsed = parse_itemized_changes(text)
    assert parsed == [
        {"type": "add", "path": "new.txt"},
        {"type": "update", "path": "changed.txt"},
        {"type": "delete", "path": "old.txt"},
    ]
