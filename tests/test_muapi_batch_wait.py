"""MUAPI batch wait policy — shared 20m deadline used to kill serial clips."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import muapi_client as muapi


def test_batch_max_wait_scales_with_clip_count():
    assert muapi.batch_max_wait_seconds(1) == 40 * 60
    assert muapi.batch_max_wait_seconds(4) == 100 * 60
    assert muapi.batch_max_wait_seconds(8) == 180 * 60


def test_queue_stall_waits_for_first_start():
    jobs = [{"last_status": "queued"}, {"last_status": "pending"}]
    assert muapi.queue_is_stalled(jobs, 19 * 60) is False
    assert muapi.queue_is_stalled(jobs, 20 * 60) is True


def test_queue_does_not_stall_once_a_job_is_processing():
    jobs = [{"last_status": "queued"}, {"last_status": "processing"}]
    assert muapi.queue_is_stalled(jobs, 25 * 60) is False


def test_process_timeout_starts_when_processing_begins():
    job = {"processing_at": 0.0, "last_status": "processing"}
    assert muapi.job_process_timed_out(job, 19 * 60) is False
    assert muapi.job_process_timed_out(job, 20 * 60) is True
    assert muapi.job_process_timed_out({"last_status": "queued"}, 40 * 60) is False


def _job(index, request_id):
    return {"index": index, "request_id": request_id, "result": None, "error": ""}


def test_serial_four_clip_batch_survives_past_twenty_minutes():
    """Old shared 20m clock would kill clips 3–4. New clock lets them finish."""
    statuses = {
        "a": ["queued"] * 2 + ["processing"] * 8 + ["completed"],
        "b": ["queued"] * 11 + ["processing"] * 8 + ["completed"],
        "c": ["queued"] * 20 + ["processing"] * 8 + ["completed"],
        "d": ["queued"] * 29 + ["processing"] * 8 + ["completed"],
    }
    calls = {key: 0 for key in statuses}

    def poll_fn(request_id):
        idx = calls[request_id]
        calls[request_id] = idx + 1
        sequence = statuses[request_id]
        status = sequence[min(idx, len(sequence) - 1)]
        return {"status": status, "outputs": [f"https://cdn.example/{request_id}.mp4"]}

    clock = {"t": 0.0}

    def now_fn():
        return clock["t"]

    def sleep_fn(seconds):
        clock["t"] += seconds

    jobs = [_job(i, rid) for i, rid in enumerate(("a", "b", "c", "d"))]
    muapi.run_batch_poll(
        jobs,
        poll_fn,
        batch_count=4,
        now_fn=now_fn,
        sleep_fn=sleep_fn,
        poll_interval=60,
    )

    assert clock["t"] > 20 * 60
    assert all(job.get("result") for job in jobs)
    assert all(not job.get("error") for job in jobs)


def test_dead_queue_fails_after_stall_window():
    clock = {"t": 0.0}

    def poll_fn(_request_id):
        return {"status": "queued"}

    def now_fn():
        return clock["t"]

    def sleep_fn(seconds):
        clock["t"] += seconds

    jobs = [_job(0, "stuck-1"), _job(1, "stuck-2")]
    muapi.run_batch_poll(
        jobs,
        poll_fn,
        batch_count=2,
        now_fn=now_fn,
        sleep_fn=sleep_fn,
        poll_interval=60,
    )

    assert all("queue stalled" in job["error"] for job in jobs)
    assert all("last status=queued" in job["error"] for job in jobs)
    assert clock["t"] == 20 * 60


def test_job_that_queues_eighteen_minutes_still_completes():
    """Process clock starts at processing, not at submit."""
    clock = {"t": 0.0}
    polls = {"n": 0}

    def poll_fn(_request_id):
        polls["n"] += 1
        if clock["t"] < 18 * 60:
            return {"status": "queued"}
        if clock["t"] < 22 * 60:
            return {"status": "processing"}
        return {"status": "completed", "outputs": ["https://cdn.example/clip.mp4"]}

    def now_fn():
        return clock["t"]

    def sleep_fn(seconds):
        clock["t"] += seconds

    jobs = [_job(0, "late-start")]
    muapi.run_batch_poll(
        jobs,
        poll_fn,
        batch_count=1,
        now_fn=now_fn,
        sleep_fn=sleep_fn,
        poll_interval=60,
    )

    assert jobs[0].get("result")
    assert not jobs[0].get("error")
