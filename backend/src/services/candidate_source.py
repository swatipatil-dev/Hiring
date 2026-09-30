"""
🧭 CANDIDATE SOURCE — where an application came from

``Candidate.source`` is one of SOURCES; ``source_detail`` names the portal, agency
or campaign; referrals also carry who referred. NULL means the row predates source
tracking — it is reported as "Not recorded", never guessed.
"""

from fastapi import HTTPException

SOURCES = {
    "direct": "Direct application",
    "referral": "Employee referral",
    "job_portal": "Job portal",
    "agency": "Recruitment agency",
    "talent_pool": "Talent pool",
    "other": "Other",
}

# A candidate applying through the career page can say how they heard about the job,
# but "talent_pool" is only ever set by HR moving someone from the pool.
CANDIDATE_SELECTABLE = {"direct", "referral", "job_portal", "agency", "other"}

# Common free-text spellings (utm_source, pool source_channel) → a source.
_ALIASES = {
    "linkedin": ("job_portal", "LinkedIn"), "naukri": ("job_portal", "Naukri"),
    "indeed": ("job_portal", "Indeed"), "monster": ("job_portal", "Monster"),
    "glassdoor": ("job_portal", "Glassdoor"), "foundit": ("job_portal", "Foundit"),
    "portal": ("job_portal", None), "job portal": ("job_portal", None),
    "referral": ("referral", None), "employee referral": ("referral", None),
    "agency": ("agency", None), "consultant": ("agency", None),
    "career page": ("direct", "Career page"), "website": ("direct", "Career page"),
    "direct": ("direct", None),
}

# Stage groups for the conversion report. A stage counts toward every milestone it has
# passed, so "reached interview" includes everyone later offered or hired.
_REACHED_SHORTLIST = {"shortlisted", "test_scheduled", "assessment", "test_submitted", "test_completed",
                      "interview", "interview_scheduled", "technical_interview", "interview_completed",
                      "offer", "offer_sent", "hired", "onboarding"}
_REACHED_INTERVIEW = {"interview", "interview_scheduled", "technical_interview", "interview_completed",
                      "offer", "offer_sent", "hired", "onboarding"}
_REACHED_OFFER = {"offer", "offer_sent", "hired", "onboarding"}
_HIRED = {"hired", "onboarding"}


def _clean(value, limit=255):
    value = (value or "").strip()
    return value[:limit] or None


def normalize_source(source=None, detail=None, referrer_name=None, referrer_email=None,
                     allowed=None):
    """Validate and normalise source fields. Returns a dict of Candidate columns.

    ``source`` may be a key of SOURCES or a common alias ("LinkedIn" → job_portal).
    Unknown values are a 400 rather than silently becoming "other", so a typo in a
    career-page link is noticed.
    """
    raw = (source or "").strip()
    detail = _clean(detail)
    key = raw.lower().replace("-", "_") if raw else "direct"
    if key not in SOURCES:
        alias = _ALIASES.get(raw.lower())
        if not alias:
            raise HTTPException(400, f"source must be one of: {sorted(SOURCES)}")
        key, alias_detail = alias
        detail = detail or alias_detail
    if allowed is not None and key not in allowed:
        raise HTTPException(400, f"source must be one of: {sorted(allowed)}")
    out = {
        "source": key,
        "source_detail": detail,
        "referrer_name": _clean(referrer_name) if key == "referral" else None,
        "referrer_email": _clean(referrer_email) if key == "referral" else None,
    }
    return out


def from_pool_channel(channel):
    """Source for a talent-pool person moved into a job: talent_pool, keeping the pool's
    own channel as the detail so the original origin is not lost."""
    return {"source": "talent_pool", "source_detail": _clean(channel),
            "referrer_name": None, "referrer_email": None}


def source_view(candidate):
    return {
        "source": candidate.source,
        "source_label": SOURCES.get(candidate.source, "Not recorded"),
        "source_detail": candidate.source_detail,
        "referrer_name": candidate.referrer_name,
        "referrer_email": candidate.referrer_email,
    }


def source_report(rows):
    """``rows`` is an iterable of (source, source_detail, current_stage).
    Returns per-source funnel counts and conversion rates, largest source first."""
    by_source = {}
    for source, detail, stage in rows:
        if stage == "merged":
            continue
        key = source or "unknown"
        b = by_source.setdefault(key, {
            "source": key, "label": SOURCES.get(source, "Not recorded"),
            "total": 0, "shortlisted": 0, "interviewed": 0, "offered": 0, "hired": 0,
            "rejected": 0, "withdrawn": 0, "details": {},
        })
        b["total"] += 1
        b["shortlisted"] += stage in _REACHED_SHORTLIST
        b["interviewed"] += stage in _REACHED_INTERVIEW
        b["offered"] += stage in _REACHED_OFFER
        b["hired"] += stage in _HIRED
        b["rejected"] += stage == "rejected"
        b["withdrawn"] += stage == "withdrawn"
        if detail:
            b["details"][detail] = b["details"].get(detail, 0) + 1

    out = []
    for b in by_source.values():
        total = b["total"] or 1
        b["shortlist_rate"] = round(b["shortlisted"] / total * 100, 1)
        b["hire_rate"] = round(b["hired"] / total * 100, 1)
        b["details"] = [{"name": k, "count": v}
                        for k, v in sorted(b["details"].items(), key=lambda kv: -kv[1])]
        out.append(b)
    out.sort(key=lambda b: -b["total"])
    return out
