"""
🔑 STARTUP BOOTSTRAP

Optionally seed the platform's own org (the "innomax" company) and its first users
from env vars — the cloud-friendly way to seed when you can't run scripts against
the DB (e.g. Cloud Run). Innomax is a full company: superadmin + admin are seeded
here; HR/interviewers are created by the admin; candidates self-register.

Superadmin (runs the platform AND belongs to the innomax company):
    SUPERADMIN_EMAIL=you@example.com
    SUPERADMIN_PASSWORD=<strong-password>
    SUPERADMIN_FIRST_NAME=Super   (optional)
    SUPERADMIN_LAST_NAME=Admin    (optional)

Platform admin (admin of the innomax company — runs Innomax's own hiring):
    PLATFORM_ADMIN_EMAIL=admin@innomax...
    PLATFORM_ADMIN_PASSWORD=<strong-password>
    PLATFORM_ADMIN_FIRST_NAME=Admin           (optional)
    PLATFORM_ADMIN_LAST_NAME=User             (optional)

Platform company (shared by both of the above):
    PLATFORM_COMPANY_NAME="Innomax Skills"  (optional)
    PLATFORM_COMPANY_SLUG=innomax                 (optional, default "innomax")

Both creators are idempotent — safe to leave the vars set, or remove the *_PASSWORD
vars after the first successful boot.
"""

import os
import logging

logger = logging.getLogger(__name__)


def _password_ok(password: str, var: str) -> bool:
    """Apply the same password rules as every other path; log and refuse if weak.

    A seeded superadmin with a weak password is the single most valuable account on
    the platform, so a weak *_PASSWORD skips the seed rather than creating it.
    """
    from fastapi import HTTPException
    from src.auth import validate_password_strength
    try:
        validate_password_strength(password)
        return True
    except HTTPException as exc:
        logger.error("%s rejected (%s) — account not seeded. Set a stronger value.",
                     var, exc.detail)
        return False


def _get_or_create_platform_company(session):
    """Return the platform's own company (by slug), creating it if missing.

    Delegates to the shared helper so the get-or-create logic lives in exactly
    one place (``src.tenancy.platform_company``).
    """
    from src.tenancy.platform_company import ensure_platform_company
    return ensure_platform_company(session)


def bootstrap_superadmin() -> None:
    """Seed the superadmin as a member of the platform (innomax) company.

    The superadmin runs the whole platform (onboards client companies) AND belongs
    to the Innomax org. Tenant filtering is domain-driven, so a non-null company_id
    here does not scope the superadmin's cross-company views.
    """
    email = os.getenv("SUPERADMIN_EMAIL")
    password = os.getenv("SUPERADMIN_PASSWORD")
    if not email or not password:
        return  # feature disabled unless both are set
    if not _password_ok(password, "SUPERADMIN_PASSWORD"):
        return

    from src.config.database_config_mysql import db_manager
    from src.models.models import User
    from src.auth import get_password_hash

    session = db_manager.get_db_session()
    try:
        company = _get_or_create_platform_company(session)

        existing = session.query(User).filter(User.email == email).first()
        if existing:
            logger.info("Superadmin bootstrap: '%s' already exists (role=%s) — skipping",
                        email, existing.role)
            session.commit()  # persist the company if it was just created
            return

        user = User(
            email=email,
            password_hash=get_password_hash(password),
            role="superadmin",
            first_name=os.getenv("SUPERADMIN_FIRST_NAME", "Super"),
            last_name=os.getenv("SUPERADMIN_LAST_NAME", "Admin"),
            is_active=True,
            email_verified=True,   # so login isn't blocked by OTP verification
            company_id=company.id,  # superadmin belongs to the platform (innomax) org
        )
        session.add(user)
        session.commit()
        logger.info("Superadmin bootstrap: created '%s' for company '%s' (id=%s)",
                    email, company.slug, company.id)
    except Exception as e:
        session.rollback()
        logger.error("Superadmin bootstrap failed: %s", e)
    finally:
        session.close()


def bootstrap_platform_admin() -> None:
    """Seed an admin for the platform's own org (the 'innomax' company).

    Creates a verified admin under the innomax company; that admin then creates
    HR/interviewers from the admin dashboard and runs Innomax's own hiring.
    """
    email = os.getenv("PLATFORM_ADMIN_EMAIL")
    password = os.getenv("PLATFORM_ADMIN_PASSWORD")
    if not email or not password:
        return  # feature disabled unless both are set
    if not _password_ok(password, "PLATFORM_ADMIN_PASSWORD"):
        return

    from src.config.database_config_mysql import db_manager
    from src.models.models import User
    from src.auth import get_password_hash

    session = db_manager.get_db_session()
    try:
        company = _get_or_create_platform_company(session)

        existing = session.query(User).filter(User.email == email).first()
        if existing:
            logger.info("Platform admin bootstrap: '%s' already exists (role=%s) — skipping",
                        email, existing.role)
            session.commit()  # persist the company if it was just created
            return

        admin = User(
            email=email,
            password_hash=get_password_hash(password),
            role="admin",
            first_name=os.getenv("PLATFORM_ADMIN_FIRST_NAME", "Admin"),
            last_name=os.getenv("PLATFORM_ADMIN_LAST_NAME", "User"),
            is_active=True,
            email_verified=True,
            company_id=company.id,
        )
        session.add(admin)
        session.commit()
        logger.info("Platform admin bootstrap: created admin '%s' for company '%s' (id=%s)",
                    email, company.slug, company.id)
    except Exception as e:
        session.rollback()
        logger.error("Platform admin bootstrap failed: %s", e)
    finally:
        session.close()
