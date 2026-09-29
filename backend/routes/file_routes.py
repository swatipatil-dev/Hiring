"""
File download routes — the only way to fetch an uploaded resume, cover letter, photo,
certificate or voice answer. Access rules and signed links: src/services/file_access.py.

Every route is keyed by the DB row that owns the file, never by the storage key, so a
leaked key is worthless and access follows the row's company and job.
"""
import mimetypes
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from src.config.database_config_mysql import get_db
from src.models.models import (
    Candidate, CandidateCertification, CandidateProfileExt, Company, Interview, User,
)
from src.models.question_models import CommunicationAnswer
from src.services.file_access import (
    APPLICATION_KINDS, PROFILE_KINDS,
    can_access_application, can_access_interview_files, can_access_profile,
    file_headers, verify_signature,
)
from src.tenancy import set_current_company, tenant_bypass

router = APIRouter(prefix="/api/v1/files", tags=["Files"])

_optional_bearer = HTTPBearer(auto_error=False)

# One answer for "no such row", "no file", "not yours" and "bad link": telling them
# apart would let a caller probe which ids exist.
_NOT_FOUND = HTTPException(status_code=404, detail="File not found")


def _resolve_viewer(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    db: Session = Depends(get_db),
) -> User:
    """The user a download is for: from the Bearer header, or from a signed link."""
    if credentials is not None:
        from src.auth import _authenticate_request
        return _authenticate_request(request, credentials, db)

    params = request.query_params
    user_id = params.get("u")
    if not verify_signature(request.url.path, user_id, params.get("e"), params.get("s")):
        raise _NOT_FOUND
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise _NOT_FOUND
    if user.company_id:
        company = db.query(Company).filter(Company.id == user.company_id).first()
        if company is not None and company.is_active is False:
            raise _NOT_FOUND
    return user


async def get_viewer(user: User = Depends(_resolve_viewer)) -> User:
    """Bind the viewer's company as the tenant. Async for the reason given on
    src.auth.get_current_user: a contextvar set in a sync dependency is discarded."""
    if (user.role or "").lower() == "superadmin":
        raise _NOT_FOUND
    set_current_company(user.company_id)
    return user


def _serve(key: Optional[str], download_name: str) -> Response:
    from src.services.storage import storage
    if not key:
        raise _NOT_FOUND
    try:
        data = storage.read_bytes(key)
    except Exception:
        raise _NOT_FOUND
    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    ext = os.path.splitext(key)[1]
    return Response(content=data, media_type=content_type,
                    headers=file_headers(f"{download_name}{ext}", content_type))


def _person_name(first, last, fallback: str) -> str:
    name = f"{first or ''} {last or ''}".strip().replace(" ", "_")
    return name or fallback


@router.get("/applications/{candidate_id}/{kind}")
def download_application_file(
    candidate_id: str,
    kind: str,
    viewer: User = Depends(get_viewer),
    db: Session = Depends(get_db),
):
    """Resume or photo attached to one application."""
    if kind not in APPLICATION_KINDS:
        raise _NOT_FOUND
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not can_access_application(db, viewer, candidate):
        raise _NOT_FOUND
    name = _person_name(candidate.first_name, candidate.last_name, "candidate")
    return _serve(getattr(candidate, APPLICATION_KINDS[kind]), f"{name}_{kind}")


@router.get("/profiles/{user_id}/{kind}")
def download_profile_file(
    user_id: str,
    kind: str,
    viewer: User = Depends(get_viewer),
    db: Session = Depends(get_db),
):
    """Resume, cover letter, photo or portfolio from a candidate's profile."""
    if kind not in PROFILE_KINDS:
        raise _NOT_FOUND
    if not can_access_profile(db, viewer, user_id):
        raise _NOT_FOUND
    # Authorised above. The profile lives in the person's own company, which for a
    # candidate who applied from another portal is not the viewer's, so the tenant
    # filter would hide it.
    with tenant_bypass():
        profile = db.query(CandidateProfileExt).filter(
            CandidateProfileExt.user_id == user_id).first()
        owner = db.query(User).filter(User.id == user_id).first()
    if profile is None:
        raise _NOT_FOUND
    name = _person_name(getattr(owner, "first_name", None), getattr(owner, "last_name", None),
                        "candidate")
    return _serve(getattr(profile, PROFILE_KINDS[kind]), f"{name}_{kind}")


@router.get("/certificates/{certificate_id}")
def download_certificate(
    certificate_id: str,
    viewer: User = Depends(get_viewer),
    db: Session = Depends(get_db),
):
    with tenant_bypass():
        cert = db.query(CandidateCertification).filter(
            CandidateCertification.id == certificate_id).first()
        profile = (db.query(CandidateProfileExt).filter(
            CandidateProfileExt.id == cert.profile_id).first() if cert else None)
    if cert is None or profile is None or not can_access_profile(db, viewer, profile.user_id):
        raise _NOT_FOUND
    return _serve(cert.certificate_file_path, "certificate")


@router.get("/voice-answers/{answer_id}")
def download_voice_answer(
    answer_id: str,
    viewer: User = Depends(get_viewer),
    db: Session = Depends(get_db),
):
    """A candidate's recorded answer to a communication question."""
    answer = db.query(CommunicationAnswer).filter(CommunicationAnswer.id == answer_id).first()
    interview = (db.query(Interview).filter(
        Interview.id == answer.interview_id,
        Interview.company_id == viewer.company_id,
    ).first() if answer else None)
    if not can_access_interview_files(db, viewer, interview):
        raise _NOT_FOUND
    return _serve(answer.audio_path, f"answer_{answer.question_id}")
