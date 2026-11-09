# Verbatim _FireAudit from cron/scheduler.py at hermes-agent v0.21.5
# (f97608f); the patch tests run it with the module's helpers stubbed.
class _FireAudit:
    """One usage_audit.jsonl line per fire (created once the agent exists; fire id + start clock)."""

    def __init__(self, job: dict, job_id: str, model: str):
        self.job, self.job_id, self.model = job, job_id, model
        self.fire_id = uuid.uuid4().hex
        self.t_start = time.monotonic()

    def write(self, result: dict, error: Optional[str]) -> None:
        _write_usage_audit({
            "ts": _utcnow_iso_ms(),
            "job_id": self.job_id,
            "fire_id": self.fire_id,
            "prompt_tokens": result.get("prompt_tokens"),
            "completion_tokens": result.get("completion_tokens"),
            "total_tokens": result.get("total_tokens"),
            "response_silent": bool(result.get("response_silent")),
            "deliver_target": self.job.get("deliver"),
            "model": self.model or None,
            "duration_ms": int((time.monotonic() - self.t_start) * 1000),
            "error": error})


# Not upstream: a wrapper around the one run_job line that builds the audit,
# kept verbatim at its own indentation, so the tests can reach it.
def _run_job_call_site(job, job_id, model, agent):
    if True:
        _audit = _FireAudit(job, job_id, model)
        return _audit
