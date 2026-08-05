"""
ATS enums — ATSUser and ATSInterview tables removed; use main users/interviews tables instead.
"""
import enum


class InterviewStatus(str, enum.Enum):
    DRAFT        = "draft"
    GENERATING   = "generating"
    READY        = "ready"
    ERROR        = "error"
    SCHEDULED    = "scheduled"
    IN_PROGRESS  = "in_progress"
    COMPLETED    = "completed"
    CANCELLED    = "cancelled"
    RESCHEDULED  = "rescheduled"


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
