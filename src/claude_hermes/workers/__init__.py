"""Background worker entrypoints — work that runs beside a turn rather than
inside one.

Wave 3a splits this in two. The *tasks* are plain async functions registered on
`core.tasks.TaskRegistry`; whether they run here or in a separate process is the
queue's business, not theirs. `curator.py` keeps the in-process loop that inline
mode still uses, and `taskiq_app.py` is the one file that knows taskiq exists.

See `README.md` in this package for the topology and the open debt.
"""
