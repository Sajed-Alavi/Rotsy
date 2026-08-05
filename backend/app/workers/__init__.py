"""Background job handlers for the DevSecOps intelligence workflow.

Registered with the existing :class:`app.core.jobs.JobRunner` in
``main.py`` — this package is not a separate process framework, just where
the new job types' implementations live, mirroring
``app.services.job_handlers`` for the pre-existing job types.
"""
