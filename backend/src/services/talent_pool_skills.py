"""Skills supply and demand for the HR Talent Pool.

The pool answers one question here: **can we staff the roles we have open?**

  DEMAND  what the company's active job postings ask for
  SUPPLY  what the people currently in view can actually do
  GAP     demanded, and held by nobody

On why this recomputes instead of reading the stored answer
-----------------------------------------------------------
``resume_pool.match_results`` already holds ``matched_skills``/``missing_skills`` per
person per job, pre-lowercased, and it is tempting to just count those. It cannot be
used:

* ``enhanced_ats_service`` computes a THIRD state, ``cluster_matched_skills`` (required
  Kubernetes, candidate has Docker), and neither ``_score_against_jobs`` nor
  ``_ranked_dict_from_ats`` persists it. So ``matched + missing != required`` on every
  stored row, and those skills are in no bucket at all.
* It is stale the moment a role is posted: a brand-new Kafka requirement would read as
  "0 of 312 have it", which is indistinguishable from a real zero.
* Unscored people have no ``match_results`` whatsoever.
* It only ever mentions skills some job demanded, so it can never show what the pool
  actually knows.

On which comparison rule
------------------------
Four different skill-matching rules are live in this repo. This mirrors exactly one of
them: the rule behind the **Has / Gaps** chips on every candidate card
(``enhanced_ats_service.py:1862-1882``) — ``normalize_skill(s).lower()`` on both sides,
then plain set intersection. Agreeing with the card the user is looking at matters more
than any other property here.

Deliberately NOT the ``skills_match_score`` rule, which is a bidirectional substring
test: under it required "java" is satisfied by "javascript", and required "r" by
literally everything.

The trailing ``.lower()`` is not optional. ``normalize_skill`` returns the CANONICAL
casing on a hit and the ORIGINAL casing on a miss, so its output is only comparable
after folding.

On doing this in Python
-----------------------
Skills live inside JSON in a TEXT column, so SQL cannot group them without
``JSON_TABLE`` — which ``docs/DEPLOYMENT_NOTES.md:393-400`` forbids, to keep the app
deployable on MariaDB shared hosts. Python is also the only place the alias map and the
cluster map can be applied at all, so this is not merely a portability concession.
"""

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

# One person listing forty skills is normal; a row listing four thousand is a parser
# accident, and folding it in would swamp every count on the page.
MAX_SKILLS_PER_PERSON = 200

# Ceiling on how many pool rows one request will parse. The real scale is low thousands
# (every list endpoint caps at 200, scoring batches at 3), so this is a guard rail
# rather than a limit anyone should hit — and when it does trip the response says so,
# because a silently truncated total reads exactly like a real one.
SKILLS_SCAN_MAX = 5000


# Punctuation that only ever arrives as a serialisation artefact, never as part of a
# skill name. `admin_routes.py:751` writes the raw Pydantic value with no json.dumps, so
# a Python list can reach the column as its repr() — "['Python', 'React']" — which
# `_parse_skills` then comma-splits into "['Python'" and "'React']". Stripping these
# recovers the real names instead of discarding the row.
_TOKEN_JUNK = "[]{}'\"`\\ \t\r\n"

# Long enough for "Amazon Web Services (AWS)", short enough to reject a sentence that
# landed in the column by accident.
MAX_SKILL_LEN = 60


def _norm(raw: str) -> Optional[Tuple[str, str]]:
    """A skill token as ``(match_key, display_name)``, or None if it is not a skill.

    The key is folded for comparison; the display name keeps the vocabulary's canonical
    casing so the UI shows "Kubernetes" rather than "kubernetes" — or, for a token the
    alias map has never heard of, whatever the resume actually said.
    """
    from src.services.enhanced_ats_service import normalize_skill

    if not isinstance(raw, str):
        return None
    cleaned = raw.strip(_TOKEN_JUNK).strip()
    if not cleaned or len(cleaned) > MAX_SKILL_LEN:
        return None
    canonical = normalize_skill(cleaned)
    key = canonical.lower()
    return (key, canonical) if key else None


def _skill_set(raw_value, display: Dict[str, str]) -> set:
    """One person's (or one job's) skills as a set of match keys.

    A set, not a list: someone whose resume lists React three times, or lists both
    "React" and "ReactJS", is one candidate for React. Counting the raw list would let a
    single verbose resume outvote a dozen real people.

    Records canonical casing into `display` as a side effect, first spelling winning.
    Tolerates NULL, malformed JSON and the comma-separated form, mirroring
    ``_parse_skills`` — ``admin_routes.py:751`` can land a Python ``repr()`` in these
    columns, and one bad row must not take out the whole tab.
    """
    from routes.hr_routes import _parse_skills

    keys = set()
    for raw in _parse_skills(raw_value)[:MAX_SKILLS_PER_PERSON]:
        pair = _norm(raw)
        if pair is None:
            continue
        key, canonical = pair
        keys.add(key)
        display.setdefault(key, canonical)
    return keys


def _demand(db, company_id: str, display: Dict[str, str]) -> Tuple[Counter, Dict[str, List[str]], int]:
    """What the open roles ask for: ``(skill -> role count, skill -> titles, n_roles)``.

    Counted per ROLE, not per mention: a job listing Python twice still needs one
    Python. Only `active` postings — a draft or closed role is not a staffing need.
    """
    from src.models.models import JobPosting

    rows = (db.query(JobPosting.title, JobPosting.required_skills)
              .filter(JobPosting.company_id == company_id,
                      JobPosting.status == "active")
              .all())

    counts: Counter = Counter()
    titles: Dict[str, List[str]] = {}
    for title, raw in rows:
        for key in _skill_set(raw, display):
            counts[key] += 1
            titles.setdefault(key, []).append(title or "Untitled role")
    return counts, titles, len(rows)


def _supply(db, preds: Iterable[Any], display: Dict[str, str]) -> Tuple[Counter, List[set], int, int, bool]:
    """What the people in view can do.

    Returns ``(skill -> candidate count, per-person skill sets, scanned,
    no_skills_on_record, truncated)``.

    Selects one TEXT column and nothing else — the row objects are never needed, and
    hydrating them would pull resume_text for the whole filtered set.
    """
    from src.models.models import ResumePoolEntry

    rows = (db.query(ResumePoolEntry.extracted_skills)
              .filter(*preds)
              .limit(SKILLS_SCAN_MAX + 1)
              .all())

    truncated = len(rows) > SKILLS_SCAN_MAX
    rows = rows[:SKILLS_SCAN_MAX]

    counts: Counter = Counter()
    people: List[set] = []
    blank = 0
    for (raw,) in rows:
        keys = _skill_set(raw, display)
        if not keys:
            # No skills ON RECORD — not "no skills". Their resume was never parsed, or
            # they arrived unscored. Counting them as a person who lacks every skill
            # would turn an unread CV into evidence of a gap.
            blank += 1
            continue
        people.append(keys)
        counts.update(keys)

    return counts, people, len(rows), blank, truncated


def _cluster_of(a: str, b: str) -> str:
    """The cluster that put these two skills together, for showing the reader why.

    The groupings in ``skill_clusters.json`` are hand-curated and some are loose — the
    `data_processing` cluster holds Apache Kafka and Pandas together, so "2 people have
    something related to Kafka" is, on inspection, two people who know Pandas. Naming
    the cluster is what lets a recruiter judge that for themselves instead of taking
    "related" as an endorsement.
    """
    from src.services.enhanced_ats_service import SKILL_CLUSTERS

    for name, members in SKILL_CLUSTERS.items():
        lowered = {m.lower() for m in members}
        if a in lowered and b in lowered:
            return name
    return ""


def _related_holders(skill_key: str, people: List[set]) -> Tuple[int, List[str], str]:
    """People who do not have `skill_key` but do have something in its cluster.

    "Nobody has Kubernetes, but twelve people have Docker" is a different situation from
    "nobody has Kubernetes", and it is the one worth acting on. Uses the ATS's own
    ``_find_cluster_match``, which walks every cluster containing the skill rather than
    the lossy single-cluster reverse map — so this column agrees with the partial credit
    the score already awards.
    """
    from src.services.enhanced_ats_service import _find_cluster_match

    n = 0
    found: Counter = Counter()
    for owned in people:
        if skill_key in owned:
            continue
        hit = _find_cluster_match(skill_key, owned)
        if hit:
            n += 1
            found[hit] += 1

    top = [s for s, _ in found.most_common(3)]
    return n, top, _cluster_of(skill_key, top[0]) if top else ""


def skill_report(db, company_id: str, preds: Iterable[Any], *, top_n: int = 20) -> Dict[str, Any]:
    """Everything behind the Skills tab, for the people the filter selects.

    `preds` is the shared predicate list from ``_pool_predicates``, so supply describes
    exactly the people the Candidates list would show. Demand is company-wide on
    purpose: filtering the pool to 2024 graduates does not change what the open roles
    need, and pretending otherwise would make the gap shrink as you narrow the search.
    """
    display: Dict[str, str] = {}

    demand_counts, demand_titles, active_roles = _demand(db, company_id, display)
    supply_counts, people, scanned, blank, truncated = _supply(db, preds, display)

    with_skills = len(people)

    rows = []
    for key, roles in demand_counts.items():
        direct = supply_counts.get(key, 0)
        # Only worth computing when it can change the reading — a well-covered skill
        # does not need a consolation prize, and this is the one O(people x clusters)
        # step in the report.
        related, examples, cluster = (
            _related_holders(key, people) if direct == 0 else (0, [], "")
        )
        rows.append({
            "skill": display.get(key, key),
            "roles": roles,
            "role_titles": sorted(set(demand_titles.get(key, [])))[:5],
            "candidates": direct,
            "related": related,
            "related_examples": [display.get(e, e) for e in examples],
            "related_cluster": cluster.replace("_", " ") if cluster else "",
            "coverage": round(direct / with_skills * 100, 1) if with_skills else 0.0,
        })

    # Worst-covered first — the working order. Ties break on demand, so a skill three
    # roles need and nobody has outranks one role needs and nobody has.
    rows.sort(key=lambda r: (r["candidates"], -r["roles"], r["skill"].lower()))

    return {
        "people": scanned,
        "people_with_skills": with_skills,
        "no_skills_on_record": blank,
        "distinct_skills": len(supply_counts),
        "active_roles": active_roles,
        "demanded_skills": len(demand_counts),
        # The headline: demanded by a live role and held by nobody, not even by cluster.
        "fully_unmet": sum(1 for r in rows if r["candidates"] == 0 and r["related"] == 0),
        "scanned": scanned,
        "truncated": truncated,
        "demand": rows,
        # Separate from `demand` on purpose: this is the only place a skill the pool has
        # in depth but no open role asks for can appear at all.
        "top_skills": [{"skill": display.get(k, k), "count": n}
                       for k, n in supply_counts.most_common(top_n)],
    }
