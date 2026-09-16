"""
ATS enums — ATSUser and ATSInterview tables removed; use main users/interviews tables instead.
"""
import enum


class InterviewStatus(str, enum.Enum):
    """Values are UPPERCASE to match what is actually written to interviews.status
    (and the same-named enum in routes/enhanced_interviewer_routes.py). These used to be
    lowercase, which made the two definitions disagree about the same column."""
    DRAFT        = "DRAFT"
    GENERATING   = "GENERATING"
    READY        = "READY"
    ERROR        = "ERROR"
    SCHEDULED    = "SCHEDULED"
    IN_PROGRESS  = "IN_PROGRESS"
    COMPLETED    = "COMPLETED"
    CANCELLED    = "CANCELLED"
    RESCHEDULED  = "RESCHEDULED"


class InterviewType(str, enum.Enum):
    TECHNICAL     = "technical"
    NON_TECHNICAL = "non-technical"
    MIXED         = "mixed"


class DifficultyLevel(str, enum.Enum):
    EASY   = "Easy"
    MEDIUM = "Medium"
    HARD   = "Hard"


class RoleType(str, enum.Enum):
    FULL_TIME  = "Full-time"
    INTERNSHIP = "Internship"
    CONTRACT   = "Contract"
    PART_TIME  = "Part-time"


class BloomTaxonomyLevel(str, enum.Enum):
    REMEMBER   = "Remember"
    UNDERSTAND = "Understand"
    APPLY      = "Apply"
    ANALYZE    = "Analyze"
    EVALUATE   = "Evaluate"
    CREATE     = "Create"
