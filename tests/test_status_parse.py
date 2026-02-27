from app.parsers import parse_kv_output


def test_parse_kv_output() -> None:
    output = "hostname=node-a\nloadavg=0.11 0.22 0.33\ninvalid\n"
    parsed = parse_kv_output(output)
    assert parsed == {
        "hostname": "node-a",
        "loadavg": "0.11 0.22 0.33",
    }
