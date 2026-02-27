from __future__ import annotations


def parse_kv_output(output: str) -> dict[str, str]:
    details: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        details[key.strip()] = value.strip()
    return details
