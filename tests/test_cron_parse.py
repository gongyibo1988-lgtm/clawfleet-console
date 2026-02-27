from app.cron_manager import build_job_id, extract_output_hints, parse_cron_lines


def test_build_job_id_stable() -> None:
    first = build_job_id("root_crontab", "*/5 * * * *", "/usr/bin/python /x.py", "root")
    second = build_job_id("root_crontab", "*/5 * * * *", "/usr/bin/python /x.py", "root")
    assert first == second
    assert len(first) == 16


def test_parse_root_cron_lines() -> None:
    jobs = parse_cron_lines("root_crontab", ["*/10 * * * * /usr/bin/echo hi >> /tmp/a.log"], has_user_field=False)
    assert len(jobs) == 1
    assert jobs[0]["schedule"] == "*/10 * * * *"
    assert jobs[0]["user"] is None
    assert jobs[0]["output_hints"] == ["/tmp/a.log"]


def test_parse_system_cron_lines() -> None:
    jobs = parse_cron_lines("etc_crontab", ["0 * * * * root /usr/local/bin/run.sh > /tmp/out.md"], has_user_field=True)
    assert len(jobs) == 1
    assert jobs[0]["user"] == "root"
    assert jobs[0]["output_hints"] == ["/tmp/out.md"]


def test_extract_output_hints_filters_non_text() -> None:
    hints = extract_output_hints("/bin/echo a > /tmp/a.bin 2> /tmp/err.log")
    assert hints == ["/tmp/err.log"]
