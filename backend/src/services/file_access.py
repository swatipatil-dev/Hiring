"""
📎 FILE ACCESS — who may download an uploaded file, and the links that carry it

Resumes, cover letters, photos, certificates and voice answers used to be served as
static files at /resumes, /uploads and /cover_letters to anyone holding the path. They
are now served only by routes/file_routes.py, keyed by the DB row that owns the file,
after the rules below. Company logos stay public (/logos).

Access rules (explicit company filters; the tenant auto-filter is a second layer):

* candidate    — only their own files (their Candidate rows by user_id, their profile,
                 their own interviews' voice answers).
* admin        — files attached to their company: its applications, the profile of
                 anyone who applied to it or sits in its resume pool, its interviews.
* hr           — as admin, but only for jobs they own or co-own (can_access_job);
                 a pool entry in their company also grants the person's profile files.
* interviewer  — only candidates they are on the interview for (lead, panel or creator).
* superadmin and anyone else — no. Support access to client files is not a route.

Signed links. <img>, <audio> and <a href> can't send the Bearer header, so every URL an
API response hands out carries a short-lived HMAC signature bound to the path, the
viewer's user id and an expiry (``?u=&e=&s=``). The download route re-checks the rules
above for that user on every request, so a link stops working as soon as the viewer
loses access or is deactivated — the signature only stands in for the header, it never
grants anything by itself. A Bearer header is accepted too, for API clients.
"""

import hashlib
import hmac
import os
import time
from typing import Iterable, List, Optional
from urllib.parse import quote, urlencode

from sqlalchemy.orm import Session

from src.models.models import Candidate, Interview, JobPosting, ResumePoolEntry

FILE_URL_PREFIX = "/api/v1/files"
# Long enough to read a page and click a link on it, short enough that a link copied
# out of a page (or a proxy log) is soon useless.
FILE_URL_TTL_SECONDS = int(os.getenv("FILE_URL_TTL_SECONDS", "1800"))

PROFILE_KINDS = {
    "resume": "resume_path",
    "cover-letter": "cover_letter_path",
    "photo": "profile_photo_path",
    "portfolio": "portfolio_file_path",
}
APPLICATION_KINDS = {
    "resume": "resume_path",
    "photo": "photo_path",
}

STAFF_ROLES = ("admin", "hr", "interviewer")


# ── Signing ───────────────────────────────────────────────────────────────────

def _signing_key() -> bytes:
    from src.auth import SECRET_KEY
    # Derived, so a file-link signature can never be replayed as anything else signed
    # with SECRET_KEY (and vice versa).
    return hmac.new(SECRET_KEY.encode(), b"hireflow-file-url-v1", hashlib.sha256).digest()


def _signature(path: str, user_id: str, expires: int) -> str:
    msg = f"{path}\n{user_id}\n{expires}".encode()
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def sign_path(path: str, user_id: str, ttl: Optional[int] = None) -> str:
    """`path` plus a signature letting `user_id` fetch it until the expiry."""
    expires = int(time.time()) + (ttl if ttl is not None else FILE_URL_TTL_SECONDS)
    query = urlencode({"u": user_id, "e": expires, "s": _signature(path, str(user_id), expires)})
    return f"{path}?{query}"


def verify_signature(path: str, user_id: Optional[str], expires: Optional[str],
                     signature: Optional[str]) -> bool:
    if not (user_id and expires and signature):
        return False
    try:
        exp = int(expires)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(_signature(path, str(user_id), exp), str(signature))


# ── URL builders (for API responses) ─────────────────────────────────────────

def application_file_url(candidate, kind: str, viewer) -> Optional[str]:
    """Signed link to an application's resume/photo for `viewer`, or None if absent."""
    column = APPLICATION_KINDS[kind]
    if candidate is None or not getattr(candidate, column, None) or viewer is None:
        return None
    return sign_path(f"{FILE_URL_PREFIX}/applications/{candidate.id}/{kind}", viewer.id)


def profile_file_url(profile, kind: str, viewer) -> Optional[str]:
    column = PROFILE_KINDS[kind]
    if profile is None or not getattr(profile, column, None) or viewer is None:
        return None
    return sign_path(f"{FILE_URL_PREFIX}/profiles/{profile.user_id}/{kind}", viewer.id)


def certificate_file_url(cert, viewer) -> Optional[str]:
    if cert is None or not cert.certificate_file_path or viewer is None:
        return None
    return sign_path(f"{FILE_URL_PREFIX}/certificates/{cert.id}", viewer.id)


def voice_answer_url(answer_id: Optional[str], viewer) -> Optional[str]:
    if not answer_id or viewer is None:
        return None
    return sign_path(f"{FILE_URL_PREFIX}/voice-answers/{answer_id}", viewer.id)


# ── Access rules ──────────────────────────────────────────────────────────────

def _role(user) -> str:
    return str(getattr(user, "role", "") or "").lower()


def _same_company(user, company_id) -> bool:
    return bool(company_id) and bool(user.company_id) and str(company_id) == str(user.company_id)


def _hr_can_access_job(db: Session, user, job_id) -> bool:
    if not job_id:
        return False
    from src.services.job_access import can_access_job
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id, JobPosting.company_id == user.company_id,
    ).first()
    hr_profile = getattr(user, "hr_profile", None)
    return bool(job and hr_profile and can_access_job(db, job, hr_profile))


def _panel_includes(interview, user) -> bool:
    from src.services.interview_panel import parse_panel
    email = (user.email or "").lower()
    for member in parse_panel(interview):
        if str(member.get("user_id") or "") == str(user.id):
            return True
        if email and (member.get("email") or "").lower() == email:
            return True
    return False


def interviewer_assigned(db: Session, user, candidate_ids: Iterable[str]) -> bool:
    """True if `user` leads, sits on the panel of, or created an interview for any of
    these candidates in their own company."""
    ids = [c for c in candidate_ids if c]
    if not ids:
        return False
    email = (user.email or "").lower()
    for iv in db.query(Interview).filter(
        Interview.company_id == user.company_id,
        Interview.candidate_id.in_(ids),
    ).all():
        if email and ((iv.interviewer_email or "").lower() == email
                      or (iv.created_by or "").lower() == email):
            return True
        if _panel_includes(iv, user):
            return True
    return False


def can_access_application(db: Session, user, candidate) -> bool:
    """May `user` download files attached to this application (Candidate row)?"""
    if candidate is None or user is None:
        return False
    role = _role(user)
    if role == "candidate":
        return bool(candidate.user_id) and str(candidate.user_id) == str(user.id)
    if role not in STAFF_ROLES or not _same_company(user, candidate.company_id):
        return False
    if role == "admin":
        return True
    if role == "hr":
        return _hr_can_access_job(db, user, candidate.job_id)
    return interviewer_assigned(db, user, [candidate.id])


def can_access_profile(db: Session, user, owner_user_id: str) -> bool:
    """May `user` download files from `owner_user_id`'s candidate profile?

    Staff need a relationship with the person inside their own company: an application
    to one of its jobs, or an entry in its resume pool. A profile is never browsable
    just because it exists.
    """
    if user is None or not owner_user_id:
        return False
    role = _role(user)
    if role == "candidate":
        return str(owner_user_id) == str(user.id)
    if role not in STAFF_ROLES or not user.company_id:
        return False

    applications: List[Candidate] = db.query(Candidate).filter(
        Candidate.user_id == owner_user_id,
        Candidate.company_id == user.company_id,
        Candidate.job_id.isnot(None),
    ).all()
    if role == "interviewer":
        return interviewer_assigned(db, user, [c.id for c in applications])

    in_pool = db.query(ResumePoolEntry.id).filter(
        ResumePoolEntry.company_id == user.company_id,
        ResumePoolEntry.source_user_id == owner_user_id,
    ).first() is not None
    if role == "admin":
        return bool(applications) or in_pool
    return in_pool or any(_hr_can_access_job(db, user, c.job_id) for c in applications)


def can_access_interview_files(db: Session, user, interview) -> bool:
    """May `user` download recordings from this interview (voice answers)?"""
    if interview is None or user is None:
        return False
    role = _role(user)
    if role == "candidate":
        from src.services.interview_access import candidate_owns_interview
        return candidate_owns_interview(db, user, interview.id) is not None
    if role not in STAFF_ROLES or not _same_company(user, interview.company_id):
        return False
    if role == "admin":
        return True
    if role == "hr":
        return _hr_can_access_job(db, user, interview.job_id)
    if interview.candidate_id and interviewer_assigned(db, user, [interview.candidate_id]):
        return True
    email = (user.email or "").lower()
    return bool(email) and ((interview.interviewer_email or "").lower() == email
                            or (interview.created_by or "").lower() == email
                            or _panel_includes(interview, user))


# ── Response headers ──────────────────────────────────────────────────────────

# Types a browser may render inline from our origin. Everything else is forced to a
# download: an uploaded .html or .svg served inline would run script on our origin.
INLINE_TYPES = frozenset({
    "application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp",
    "audio/webm", "audio/ogg", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4",
    "video/webm",
})


def content_disposition(filename: str, inline: bool) -> str:
    """RFC 6266/5987 header value: an ASCII fallback plus the exact UTF-8 name."""
    kind = "inline" if inline else "attachment"
    fallback = "".join(c if 32 <= ord(c) < 127 and c not in '"\\;' else "_" for c in filename) or "file"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def file_headers(filename: str, content_type: str) -> dict:
    headers = {
        "Content-Disposition": content_disposition(filename, content_type in INLINE_TYPES),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
    }
    # If a file is ever rendered anyway, give it no script and no same-origin access.
    # Not for PDFs: a sandboxed document can't load Chrome's PDF viewer, and PDF
    # scripting already runs inside the viewer's own sandbox.
    if content_type != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'"
    return headers
