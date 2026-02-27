from app.cron_manager import summarize_job_logs


def test_summarize_job_logs_ok_and_error() -> None:
    job = {"command": "/usr/bin/python /root/jobs/report.py > /tmp/report.log"}
    logs_24h = [
        "Feb 27 01:00:00 host CRON[1]: CMD (/usr/bin/python /root/jobs/report.py)",
        "Feb 27 02:00:00 host CRON[2]: CMD (/usr/bin/python /root/jobs/report.py) failed exit 1",
    ]
    logs_7d = ["Feb 25 01:00:00 host CRON[3]: CMD (/usr/bin/python /root/jobs/report.py)"] + logs_24h
    summary = summarize_job_logs(job, logs_24h, logs_7d)
    assert summary["runs_24h"] == 2
    assert summary["errors_24h"] == 1
    assert summary["runs_7d"] == 3
    assert summary["errors_7d"] == 1
    assert summary["last_status"] == "error"


def test_summarize_job_logs_unknown_when_no_match() -> None:
    job = {"command": "/usr/local/bin/cleanup.sh"}
    logs = ["Feb 27 01:00:00 host something else"]
    summary = summarize_job_logs(job, logs, logs)
    assert summary["runs_24h"] == 0
    assert summary["errors_24h"] == 0
    assert summary["runs_7d"] == 0
    assert summary["errors_7d"] == 0
    assert summary["last_status"] == "unknown"
