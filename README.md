# Multilingual AI Transcription Platform

This is the project workspace directory for the high-performance multilingual AI transcription platform.

## Subdirectories

- `/desktop_agent`: PySide6 Windows desktop client for Microsoft Word COM automation.
- `/backend`: Django + DRF web application, Celery workers, and AI inference models.
- `/frontend`: React client SPA uploader and transcript manager.

## Active Workspace Recommendation

Please open this directory as your active workspace in your IDE to start building and executing the system.

## Troubleshooting

See [`RUNBOOK.md`](./RUNBOOK.md) for how to diagnose and fix things that have
actually gone wrong while running this platform — leaked/zombie server
processes, jobs stuck "in progress" forever, how to run the regression test
suite, and how to restart the backend cleanly.
