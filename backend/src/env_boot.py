"""
Tolerant .env loading.

Why this exists
---------------
``load_dotenv()`` raises ``PermissionError`` when the file exists but the running
user cannot read it. That is a normal state in this deployment, not a fault:

  * ``backend/.env`` is mode 0600 owned by the API user, because it holds
    SECRET_KEY, the database password and the Gemini key.
  * The judge worker runs as a DIFFERENT user (``hfjudge``), which has Docker
    access the API deliberately lacks.
  * systemd already injects the file's contents into that worker through
    ``EnvironmentFile=``, reading it as root before dropping privileges.

So by the time the worker's Python starts, every variable is already in
``os.environ`` — and the ``load_dotenv()`` call is redundant. Left unguarded it
crashed the worker on import with ``Permission denied: '.../.env'``, which
surfaced to candidates as a judge error.

Loading is therefore best-effort: if the file cannot be read we log and carry on,
because the environment is expected to be populated already. Genuinely missing
configuration still fails loudly and specifically later — ``src/auth.py`` refuses
to start without SECRET_KEY, and the database URL failure names the connection.
"""

import logging
import os

logger = logging.getLogger(__name__)

_loaded = False


def load_env(**kwargs) -> bool:
    """Load .env if readable. Returns True if the file was actually read.

    Safe to call more than once; only the first call does work.
    """
    global _loaded
    if _loaded:
        return True

    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("python-dotenv not installed — relying on the process environment")
        return False

    try:
        result = bool(load_dotenv(**kwargs))
        _loaded = result
        return result
    except PermissionError:
        # Expected for the judge worker: different user, file is 0600, and
        # systemd already supplied these variables via EnvironmentFile=.
        logger.info(
            ".env is not readable by uid=%s — using the environment already "
            "provided by the service manager", os.getuid() if hasattr(os, "getuid") else "?",
        )
        return False
    except OSError as exc:
        logger.warning("could not read .env (%s) — using the process environment", exc)
        return False
