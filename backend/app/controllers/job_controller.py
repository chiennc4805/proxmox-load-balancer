from backend.app.jobs.store import JobStore, get_job_store


class JobController:
    def __init__(self, job_store: JobStore | None = None):
        self.job_store = job_store or get_job_store()

    def get_job(self, job_id: str) -> dict:
        return self.job_store.get_job(job_id)