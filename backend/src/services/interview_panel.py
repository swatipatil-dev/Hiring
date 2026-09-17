"""
👥 INTERVIEW PANEL

Building a panel roster, in one place, so the create path and the edit path cannot drift.

The roster is a JSON list of ``{"id","name","email","user_id"}`` stored on
``interviews.panel_members``. Order is meaningful: ``panel[0]`` is the lead, and
``interviewer_name``/``interviewer_email`` mirror it so the older single-interviewer reads
keep working (see the comment on models.py:604).

Rules worth not losing:
* HR's selection order is preserved — first picked is the lead.
* A deactivated pick is a loud 400, never a silently smaller panel than HR chose.
* Ad-hoc panelists are deduped by lowercased email against the interviewer picks.
* An ad-hoc email resolves to a company user only when that user is ACTIVE, so a
  deactivated person typed in by hand is not notified as though they were still staff.
"""

import json

from fastapi import HTTPException

from src.models.models import InterviewerProfile
from src.services.staff_directory import active_interviewer_query, active_user_by_email


def build_panel(db, company_id, interviewer_ids=None, manual_panelists=None, job=None):
    """Return the ordered panel roster for the given picks.

    ``job`` is optional: when given and no interviewer_ids resolve, the job's default
    interviewer is used as a single-member fallback — but only while that user is active.
    """
    panel = []

    if interviewer_ids:
        profiles = active_interviewer_query(db, company_id).filter(
            InterviewerProfile.id.in_(interviewer_ids),
            InterviewerProfile.company_id == company_id,
        ).all()
        if {str(i) for i in interviewer_ids} - {str(p.id) for p in profiles}:
            raise HTTPException(
                status_code=400,
                detail="One or more selected interviewers are deactivated or unavailable.",
            )
        by_id = {str(p.id): p for p in profiles}
        for pid in interviewer_ids:
            p = by_id.get(str(pid))
            if p and p.user:
                panel.append({
                    "id": str(p.id),
                    "name": f"{p.user.first_name} {p.user.last_name}".strip(),
                    "email": p.user.email,
                    "user_id": str(p.user.id),
                })

    if not panel and job is not None and job.interviewer and job.interviewer.user \
            and job.interviewer.user.is_active:
        u = job.interviewer.user
        panel.append({
            "id": str(job.interviewer.id),
            "name": f"{u.first_name} {u.last_name}".strip(),
            "email": u.email,
            "user_id": str(u.id),
        })

    if manual_panelists:
        seen = {(p.get("email") or "").strip().lower() for p in panel}
        for mp in manual_panelists:
            email = (mp.email or "").strip()
            if not email or email.lower() in seen:
                continue
            seen.add(email.lower())
            u = active_user_by_email(db, company_id, email)
            name = (mp.name or "").strip() or (
                f"{u.first_name} {u.last_name}".strip() if u else email
            )
            panel.append({
                "id": str(u.id) if u else None,
                "name": name,
                "email": email,
                "user_id": str(u.id) if u else None,
            })

    return panel


def parse_panel(interview):
    """Read an interview's stored roster, tolerating NULL and malformed JSON."""
    try:
        return json.loads(interview.panel_members) if interview.panel_members else []
    except (ValueError, TypeError):
        return []


def apply_panel(interview, panel):
    """Write the roster and re-sync the lead mirror fields."""
    if panel:
        interview.panel_members = json.dumps(panel)
        interview.interviewer_name = panel[0].get("name") or ""
        interview.interviewer_email = panel[0].get("email") or ""
    else:
        interview.panel_members = None
        interview.interviewer_name = ""
        interview.interviewer_email = ""


def diff_panel(old_panel, new_panel):
    """Return (added, removed) member dicts, compared on lowercased email."""
    def emails(p):
        return {(m.get("email") or "").strip().lower() for m in p}

    old_e, new_e = emails(old_panel), emails(new_panel)
    added = [m for m in new_panel if (m.get("email") or "").strip().lower() not in old_e]
    removed = [m for m in old_panel if (m.get("email") or "").strip().lower() not in new_e]
    return added, removed
