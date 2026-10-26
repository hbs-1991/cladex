"""Which conversation lane a job's reports belong to."""
from __future__ import annotations


def delivery_lane(job: dict) -> str:
    """The session key of the lane a job's reports land in: the creator's chat key
    with the job's topic id, which is exactly how build_session_key names the topic
    when the user replies there."""
    base = job["origin_session_key"].rsplit(":t", 1)[0]
    thread = job.get("deliver_thread_id")
    return f"{base}:t{thread}" if thread else base
