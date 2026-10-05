"""Standalone sessions for code that runs outside a request.

The outbox publisher, the dispatcher and the delivery worker all need a session
of their own: the after-commit publisher cannot reuse the session that just
committed, and the worker has no request at all. They open it here, in one
place, so the test suite can point it at the test connection — the same reason
``email_log._open_session`` exists (see conftest.py).
"""
from __future__ import annotations


def open_session():
    """Open a session on the application database. The caller closes it.

    Deliberately not ``next(get_db())``: that leaves the dependency generator
    suspended, so its ``finally: db.close()`` never runs and the connection leaks.
    """
    from src.config.database_config_mysql import db_manager
    return db_manager.get_db_session()
