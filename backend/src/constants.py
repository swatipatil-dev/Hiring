"""
🔧 CONSTANTS AND CONFIGURATION
Application constants and configuration values
"""

# API Configuration
API_PREFIX = "/api/v1"
API_VERSION = "2.0.0"

# Database Configuration
DATABASE_NAME = "recruitment_db"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_LIMIT = 100

# Authentication Configuration
DEFAULT_TOKEN_EXPIRE_MINUTES = 1440
BCRYPT_ROUNDS = 12

# Vertex AI Configuration (reference defaults only — the live values are read
# from env in src/services/gemini_service.py; nothing imports these).
VERTEX_LOCATION = "us-central1"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TIMEOUT = 300

# File Paths
BACKUP_DIR = "backups"
LOGS_DIR = "logs"

# Question Configuration
DEFAULT_QUESTION_COUNT = 10
DEFAULT_QUESTION_TIME_LIMIT = 5
DEFAULT_QUESTION_POINTS = 10

# Status Codes
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"

# Question Types
QUESTION_TYPE_MULTIPLE_CHOICE = "multiple_choice"
QUESTION_TYPE_SHORT_ANSWER = "short_answer"
QUESTION_TYPE_CODING = "coding"
QUESTION_TYPE_BEHAVIORAL = "behavioral"
QUESTION_TYPE_TECHNICAL = "technical"
QUESTION_TYPE_SITUATIONAL = "situational"

# Difficulty Levels
DIFFICULTY_EASY = "Easy"
DIFFICULTY_MEDIUM = "Medium"
DIFFICULTY_HARD = "Hard"

# User Roles
ROLE_ADMIN = "admin"
ROLE_HR = "hr"
ROLE_INTERVIEWER = "interviewer"
ROLE_CANDIDATE = "candidate"

# ==================== MCQ ANGLES ====================
# An "angle" classifies *how* a multiple-choice question tests a topic (e.g. Python),
# as opposed to *what* the topic is. The selected angle is written to the `category`
# column on a generated question; its Bloom level is written to `bloom_taxonomy_level`.
# All angle questions remain question_type = "multiple_choice".

# slug -> human label + short definition used in the generation prompt
MCQ_ANGLES = {
    "theory":            {"label": "Theory / Conceptual", "desc": "definitions, rules, and 'which statement is true' facts about the topic"},
    "syntax":            {"label": "Syntax",              "desc": "valid vs invalid syntax and correct keyword/construct usage"},
    "output_prediction": {"label": "Output Prediction",   "desc": "mentally execute a short snippet and pick the exact printed/returned result"},
    "code_debugging":    {"label": "Code Debugging",      "desc": "spot the bug, the failing line, or the cause of an error in a snippet"},
    "logic":             {"label": "Logic / Problem-solving", "desc": "reason about algorithm behavior or pick the snippet that solves a problem"},
    "best_practice":     {"label": "Best Practice",       "desc": "choose the idiomatic / preferred / most maintainable approach"},
    "complexity":        {"label": "Complexity / Performance", "desc": "time/space complexity (Big-O) and performance trade-offs"},
    "edge_cases":        {"label": "Edge Cases",          "desc": "mutability, scoping, truthiness, default-argument and boundary traps"},
}

# slug -> Bloom's taxonomy level recorded on the generated question
ANGLE_TO_BLOOM = {
    "theory":            "Understand",
    "syntax":            "Remember",
    "output_prediction": "Apply",
    "code_debugging":    "Analyze",
    "logic":             "Apply",
    "best_practice":     "Evaluate",
    "complexity":        "Analyze",
    "edge_cases":        "Analyze",
}

VALID_ANGLE_SLUGS = set(MCQ_ANGLES.keys())


# ==================== PROGRAMMING-LANGUAGE DETECTION ====================
# When a focus skill is a programming language, MCQ generation switches from
# plain theory recall to CODE-REASONING questions (code tracing, output
# prediction, dry run, program-flow analysis) built around a short snippet.
# Map of normalized aliases -> canonical display name used in the prompt.
PROGRAMMING_LANGUAGE_ALIASES = {
    "python": "Python", "py": "Python", "python3": "Python",
    "java": "Java",
    "javascript": "JavaScript", "js": "JavaScript", "node": "JavaScript", "nodejs": "JavaScript", "node.js": "JavaScript",
    "typescript": "TypeScript", "ts": "TypeScript",
    "c": "C",
    "c++": "C++", "cpp": "C++", "cplusplus": "C++",
    "c#": "C#", "csharp": "C#", "c sharp": "C#", "dotnet": "C#", ".net": "C#",
    "go": "Go", "golang": "Go",
    "rust": "Rust",
    "ruby": "Ruby",
    "php": "PHP",
    "swift": "Swift",
    "kotlin": "Kotlin",
    "scala": "Scala",
    "r": "R",
    "perl": "Perl",
    "dart": "Dart",
    "objective-c": "Objective-C", "objc": "Objective-C",
    "bash": "Bash", "shell": "Bash", "sh": "Bash",
    "matlab": "MATLAB",
    "julia": "Julia",
    "haskell": "Haskell",
    "lua": "Lua",
    "groovy": "Groovy",
    "elixir": "Elixir",
    "erlang": "Erlang",
    "clojure": "Clojure",
    "f#": "F#", "fsharp": "F#",
    "visual basic": "Visual Basic", "vb": "Visual Basic", "vb.net": "Visual Basic",
    "assembly": "Assembly", "asm": "Assembly",
    "fortran": "Fortran",
    "cobol": "COBOL",
}

# Styles applied to code-based MCQs (slug -> human label + prompt definition).
CODE_MCQ_STYLES = {
    "code_tracing":    {"label": "Code Tracing",            "desc": "trace the snippet step-by-step and pick the correct final state / return value"},
    "output_prediction": {"label": "Output Prediction",    "desc": "mentally execute the snippet and pick the EXACT printed/returned output"},
    "dry_run":         {"label": "Dry Run",                 "desc": "given specific input values, pick the value of a variable / loop counter after the run"},
    "program_flow":    {"label": "Program Flow Analysis",   "desc": "pick which branch/path executes, how many times a loop runs, or the order of calls"},
}


def detect_programming_languages(skill_names):
    """Return the canonical display names of any programming languages found in
    ``skill_names`` (a list of raw skill strings), preserving first-seen order.

    A skill matches if it equals an alias exactly, or contains an alias as a
    whole word (so "Python Programming" or "Advanced Java" still match) — but
    short aliases like "c", "r", "go" only match on an exact token to avoid
    false positives inside unrelated words.
    """
    import re

    found = []
    seen = set()
    for raw in skill_names or []:
        name = str(raw).strip().lower()
        if not name:
            continue
        tokens = set(re.split(r"[^a-z0-9+#.]+", name))
        tokens.discard("")
        for alias, canonical in PROGRAMMING_LANGUAGE_ALIASES.items():
            if canonical in seen:
                continue
            # Short/ambiguous aliases must be an exact standalone token.
            if len(alias) <= 2:
                matched = alias in tokens
            else:
                matched = alias == name or alias in tokens or alias in name
            if matched:
                seen.add(canonical)
                found.append(canonical)
    return found


def normalize_angles(angles):
    """Return the subset of `angles` that are recognised slugs, preserving order."""
    if not angles:
        return []
    seen = set()
    result = []
    for a in angles:
        slug = str(a).strip().lower()
        if slug in VALID_ANGLE_SLUGS and slug not in seen:
            seen.add(slug)
            result.append(slug)
    return result
