"""Application shell: HTTP API, job queue, worker processes, event streaming.

    python -m server.app --root ./jobs --port 8765

The library never imports this package. It shows how a runtime becomes a
service: one worker process per job, a SQLite job table, a control file
per workspace, and server-sent events read from the workspace event log.
"""
