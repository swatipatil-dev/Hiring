"""
Coding-judge execution engine.

Public surface:
  * ``languages``  — supported-language registry (image, compile/run commands).
  * ``sandbox``    — run a single command inside a hardened, throwaway Docker container.
  * ``verdict``    — output normalization + verdict vocabulary.
  * ``runner``     — orchestrates compile + per-test-case run → aggregate verdict.
  * ``queue``      — enqueue a submission for asynchronous judging (RQ + Redis),
                     with a synchronous fallback when Redis is unavailable.

See docs and src/judge/sandbox.py for the security model.
"""
