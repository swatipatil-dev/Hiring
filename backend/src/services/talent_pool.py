"""Outcome vocabulary for the HR Talent Pool.

The pool answers one question per person: **what is the best thing that has ever
happened to them here?** That is what `outcome` means, and the precedence below is
what implements it:

    hired > offer > in_process > declined > rejected > never_applied

The ordering is deliberate. Someone rejected from one role but currently interviewing
for another must read as *in process*, never as *rejected* — HR browses this screen to
find people worth resurfacing, and burying a live candidate under a stale rejection is
the worst thing the screen can do. `declined` outranks `rejected` because "they turned
us down" is a warmer signal than "we turned them down".

On the stage strings: `Candidate.current_stage` is a bare VARCHAR with no enum and no
constraint, written by a dozen code paths. Three other stage lists exist in this repo
(`advanced_features_routes.py:51` and `:532`, `hr_routes.py:2064`) and **none of them
match what the application actually writes** — they omit `ats_screening`, `hr_review`,
`interview_scheduled` and more. They are deliberately not consulted here; these sets are
derived from the values the live code really assigns.

Two implementations of the same precedence live here: `derive_outcome` for display and
`person_applied_exists` for SQL filtering. Filtering has to happen in SQL or pagination
silently breaks (you cannot filter a page and still report a correct total). They are
kept structurally parallel on purpose — that is what stops them drifting apart.
"""

from typing import Iterable, Optional, Set

from sqlalchemy import and_, or_

# Terminal outcomes.
HIRED_STAGES: Set[str] = {"hired"}
OFFER_STAGES: Set[str] = {"offer_sent", "offer"}   # both strings occur in the wild
DECLINED_STAGES: Set[str] = {"offer_rejected"}     # the candidate said no to us
REJECTED_STAGES: Set[str] = {"rejected"}           # we said no to them

TERMINAL_STAGES: Set[str] = HIRED_STAGES | OFFER_STAGES | DECLINED_STAGES | REJECTED_STAGES

# The bucket a person falls into, best-first. Also the order the UI rail shows.
OUTCOMES = ("hired", "offer", "in_process", "declined", "rejected", "never_applied")


def derive_outcome(stages: Iterable[str]) -> str:
    """The best thing that ever happened to this person, from their application stages.

    `stages` must already exclude the signup placeholder (`job_id IS NULL`), or every
    registered person reads as in-process.
    """
    stages = {s for s in stages if s}
    if not stages:
        return "never_applied"
    if stages & HIRED_STAGES:
        return "hired"
    if stages & OFFER_STAGES:
        return "offer"
    if stages - TERMINAL_STAGES:      # anything still moving
        return "in_process"
    if stages & DECLINED_STAGES:
        return "declined"
    if stages & REJECTED_STAGES:
        return "rejected"
    return "in_process"


def person_applied_exists(db, company_id: str, *,
                          stages: Optional[Iterable[str]] = None,
                          exclude_stages: Optional[Iterable[str]] = None):
    """A correlated EXISTS: does the person this `resume_pool` row represents have an
    application in (or outside) the given stages?

    Returns two EXISTS joined by OR rather than one EXISTS containing an OR — MySQL
    optimises the former far better, and each branch can use its own index
    (`candidates.user_id` via its FK index, `candidates.email` via ix_candidates_email).

    The email branch is gated on `Candidate.user_id IS NULL` deliberately.
    `Candidate.email` is free-form — HR types it into the Move-to-ATS modal — so a bare
    email match could attribute one person's rejection to a different person who happens
    to share an address. When a Candidate has a user_id, that is authoritative and email
    is not consulted. Email is the fallback identity for account-less candidates only.
    """
    from src.models.models import Candidate, ResumePoolEntry

    def _base():
        # Naming candidates.company_id explicitly makes the tenant auto-filter's
        # _query_already_filters_company heuristic skip this query, so we don't get a
        # second, redundant company predicate injected (src/tenancy/events.py:51).
        q = db.query(Candidate.id).filter(
            Candidate.company_id == company_id,
            # Never the signup placeholder — without this every registered person
            # would read as in_process.
            Candidate.job_id.isnot(None),
        )
        if stages:
            q = q.filter(Candidate.current_stage.in_(list(stages)))
        if exclude_stages:
            q = q.filter(Candidate.current_stage.notin_(list(exclude_stages)))
        return q

    by_user = _base().filter(
        ResumePoolEntry.source_user_id.isnot(None),
        Candidate.user_id == ResumePoolEntry.source_user_id,
    ).exists()

    by_email = _base().filter(
        ResumePoolEntry.email.isnot(None),
        Candidate.user_id.is_(None),
        # utf8mb4_unicode_ci compares case-insensitively; LOWER() here would kill
        # ix_candidates_email.
        Candidate.email == ResumePoolEntry.email,
    ).exists()

    return or_(by_user, by_email)


def outcome_filter(db, company_id: str, outcome: str):
    """SQL predicate selecting exactly the people in one outcome bucket.

    The predicates mirror `derive_outcome`'s precedence, which makes the buckets
    mutually exclusive and exhaustive: the per-bucket totals sum to the unfiltered
    total. That invariant is asserted in test_resume_pool.py.

    Returns None for an unknown bucket so the caller can ignore it rather than
    silently returning an empty list.
    """
    has_any = person_applied_exists(db, company_id)
    has_hired = person_applied_exists(db, company_id, stages=HIRED_STAGES)
    has_offer = person_applied_exists(db, company_id, stages=OFFER_STAGES)
    has_live = person_applied_exists(db, company_id, exclude_stages=TERMINAL_STAGES)
    has_declined = person_applied_exists(db, company_id, stages=DECLINED_STAGES)
    has_rejected = person_applied_exists(db, company_id, stages=REJECTED_STAGES)

    if outcome == "never_applied":
        return ~has_any
    if outcome == "applied":                      # any application at all
        return has_any
    if outcome == "hired":
        return has_hired
    if outcome == "offer":
        return and_(has_offer, ~has_hired)
    if outcome == "in_process":
        return and_(has_live, ~has_hired, ~has_offer)
    if outcome == "declined":
        return and_(has_declined, ~has_hired, ~has_offer, ~has_live)
    if outcome == "rejected":
        return and_(has_rejected, ~has_hired, ~has_offer, ~has_live, ~has_declined)
    return None
