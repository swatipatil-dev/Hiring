"""
📝 QUESTION GENERATOR ROUTER
Complete question generation router - ported from New folder (7)
Only change: uses hiring system's MySQL database with UUID-based IDs
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
import json
import csv
import io
import random
import uuid
from datetime import datetime

from src.models.models import Interview
from src.models.question_models import Question as QuestionModel, QuestionStatus, QuestionSetAssignment
from src.config.database_config_mysql import get_db
from src.models.question_schemas import (
    QuestionGenerationRequest, QuestionGenerationResponse,
    QuestionListResponse as QuestionListResponseBase, Question, QuestionUpdate,
    OllamaHealthCheck, QuestionGenerationStats,
    GeneratePoolRequest, AssembleSetsRequest, AssembleSetsResponse,
    TestSetResponse, TestSetQuestionResponse,
    PoolTargetStatus, ConfigureAssemblyResponse, SkillTypeTarget,
    ImportQuestionsRequest, ManualMCQItem, ManualQuestionsRequest
)
from src.mcq_options import normalize_mcq_options
from src.prompt_builder import PromptBuilder, normalize_coding_language
from src.services.gemini_service import gemini_service, is_rate_limit_error
from src.judge.starter_sanitizer import sanitize_starter

router = APIRouter(prefix="/interviews", tags=["Question Generator"])


def _revalidate_coding_testcases(q, language=None):
    """Best-effort quality gate: run the reference solution against the generated
    test cases and keep only the ones it passes (LLMs often emit wrong expected
    outputs). Skipped when disabled (JUDGE_VALIDATE_GENERATED=0), no cases, or
    Docker is unavailable. Never raises.

    ``language`` is the coding language of ``q`` (python/java/cpp/javascript);
    the reference is executed in THAT language, defaulting to python.

    SQL questions take a different path: there's no Docker needed (the reference
    runs in-process against SQLite), expected outputs are RECOMPUTED from the
    reference query, and missing seed data is synthesized — so a SQL question
    always ends up with at least one gradeable sample case."""
    import os
    import logging
    if os.getenv("JUDGE_VALIDATE_GENERATED", "1") != "1":
        return

    # Minimum hidden (is_sample=false) cases a coding question should carry so a
    # candidate can't just hard-code the visible sample. Tunable via env.
    MIN_HIDDEN = int(os.getenv("JUDGE_MIN_HIDDEN", "3"))

    def _hidden(cs):
        return sum(1 for c in cs if isinstance(c, dict) and not c.get("is_sample"))

    cases = q.test_cases if isinstance(q.test_cases, list) else []

    # ── SQL questions: recompute / synthesize via the in-process SQLite runner ──
    if q.sample_answer:
        try:
            from src.judge.sql_testgen import looks_like_sql
            if looks_like_sql(q.sample_answer, getattr(q, "category", None)):
                from src.judge.hidden_testgen import ensure_hidden_testcases
                fixed = ensure_hidden_testcases(
                    q.question_text, q.sample_answer, getattr(q, "category", None),
                    cases, min_hidden=MIN_HIDDEN)
                if fixed:
                    q.test_cases = fixed
                return
        except Exception as e:
            logging.getLogger("generator").warning("sql test-case validation skipped: %s", e)
            return

    # A runnable reference is required to validate cases / compute expected outputs.
    if not q.sample_answer:
        return
    run_lang = (language or "python").lower()
    try:
        from src.judge.runner import backend_available, judge_submission, TestCaseInput
        # Works under either backend: Docker must be up locally, Judge0 is remote
        # (validated per call). Skip only when neither can execute code.
        if not backend_available():
            return

        # Validate any LLM-provided cases against the reference solution — in the
        # question's OWN language — and keep only the ones it reproduces (LLMs often
        # emit wrong outputs). If the language image isn't runnable here, keep the
        # originals for manual review rather than dropping everything.
        if cases:
            tcs = [
                TestCaseInput(id=str(i), input=c.get("input", ""),
                              expected_output=c.get("expected_output", ""),
                              is_sample=bool(c.get("is_sample")), ordinal=i)
                for i, c in enumerate(cases)
            ]
            result = judge_submission(run_lang, q.sample_answer, tcs,
                                      time_limit_ms=4000, memory_limit_mb=256)
            kept = [cases[int(cr.test_case_id)] for cr in result.cases if cr.verdict == "AC"]
            if kept:
                q.test_cases = kept   # else keep originals for manual review

        # Top up hidden cases when the set is too thin OR empty. The synthesizer
        # (hidden_testgen) assumes a python reference, so only run it for python —
        # for other languages we keep the validated LLM cases as-is.
        if run_lang == "python":
            current = q.test_cases if isinstance(q.test_cases, list) else cases
            if _hidden(current) < MIN_HIDDEN:
                from src.judge.hidden_testgen import ensure_hidden_testcases
                topped = ensure_hidden_testcases(
                    q.question_text, q.sample_answer, getattr(q, "category", None),
                    current, min_hidden=MIN_HIDDEN)
                if topped:
                    q.test_cases = topped
    except Exception as e:
        logging.getLogger("generator").warning("coding test-case validation skipped: %s", e)


def _normalize_text(s) -> str:
    """Normalize question text for duplicate detection: lowercase, collapse all
    runs of whitespace to a single space, and strip. Used by every dedup check so
    trivial spacing/case differences still count as the same question."""
    if not s:
        return ""
    return " ".join(str(s).split()).strip().lower()


# Fallback minutes per question when a test carries no configured time for that type.
# These are the values the generator used before the time was configurable, so tests created
# earlier keep the clock they already had.
_TYPE_TIME_DEFAULT = 5
_TYPE_TIME_DEFAULTS = {"coding": 15}


def _configured_time(interview, q_type: str) -> int:
    """Minutes to grant for one question of q_type on this test.

    Reads interview_configuration.time_per_question_type — the map HR sets in the create-test
    wizard, from which the test's total duration is computed. Falls back to the type default
    for tests saved before the setting existed.
    """
    try:
        cfg = json.loads(interview.interview_config_data) if interview and interview.interview_config_data else {}
        configured = (cfg.get("time_per_question_type") or {}).get(q_type)
        if configured:
            return int(configured)
    except (ValueError, TypeError):
        pass
    return _TYPE_TIME_DEFAULTS.get(q_type, _TYPE_TIME_DEFAULT)


def _validate_question_content(q, target_type: str, language=None):
    """Validate and fix question content to match its assigned type.

    ``language`` (coding only) is the target coding language used when
    re-validating/executing test cases."""
    if target_type == 'multiple_choice':
        if not q.correct_answer and q.options:
            q.correct_answer = "A"
    elif target_type == 'coding':
        if not q.sample_answer:
            q.sample_answer = q.correct_answer or ""
        # Strip any leaked solution out of the starter (the generator sometimes drops the
        # full answer into code_template); also supplies a default stub when missing.
        q.code_template = sanitize_starter(
            getattr(q, "code_template", None),
            category=getattr(q, "category", None),
            sample_answer=q.sample_answer,
        )
        q.options = None          # Strip MCQ options from coding
        q.correct_answer = None   # No letter answer for coding
        _revalidate_coding_testcases(q, language)
    elif target_type == 'short_answer':
        q.options = None
        if not q.sample_answer:
            q.sample_answer = q.correct_answer or ""
    elif target_type == 'case_study':
        q.options = None
        if not q.sample_answer:
            q.sample_answer = q.correct_answer or ""
    elif target_type == 'aptitude':
        if not q.correct_answer and q.options:
            q.correct_answer = "A"
    elif target_type == 'communication':
        q.options = None
        if not q.sample_answer:
            q.sample_answer = q.correct_answer or ""


def _generation_http_error(e: Exception) -> HTTPException:
    """Map an AI generation failure to a helpful HTTP error. Rate limits become a
    429 with a clear retry hint instead of an opaque 500."""
    if is_rate_limit_error(e) or "RATE_LIMIT" in str(e):
        return HTTPException(
            status_code=429,
            detail=("The AI provider is rate-limited (quota exceeded). Please wait a "
                    "minute and try again, or generate fewer questions at a time."),
        )
    return HTTPException(
        status_code=500,
        detail=(f"Failed to generate questions — the model may have returned an "
                f"incomplete response. Try again (coding questions are generated in "
                f"small batches). Details: {str(e)}"),
    )


def _db_question_to_schema(db_q) -> Question:
    """Convert DB question model to schema"""
    return Question(
        id=db_q.id,
        interview_id=db_q.interview_id,
        question_text=db_q.question_text,
        question_type=db_q.question_type,
        difficulty_level=db_q.difficulty_level,
        category=db_q.category,
        bloom_taxonomy_level=db_q.bloom_taxonomy_level,
        options=json.loads(db_q.options) if db_q.options else None,
        correct_answer=db_q.correct_answer,
        sample_answer=db_q.sample_answer,
        code_template=db_q.code_template,
        test_cases=json.loads(db_q.test_cases) if db_q.test_cases else None,
        language=getattr(db_q, "language", None),
        time_limit_minutes=db_q.time_limit_minutes,
        points=db_q.points,
        status=db_q.status,
        is_approved=db_q.is_approved,
        approved_by=db_q.approved_by,
        approval_notes=db_q.approval_notes,
        skill_name=db_q.skill_name,
        generation_model=db_q.generation_model,
        generation_temperature=float(db_q.generation_temperature) if db_q.generation_temperature else None,
        created_at=db_q.created_at,
        updated_at=db_q.updated_at
    )


@router.get("/test-ollama")
async def test_ollama():
    """Test Ollama connection"""
    try:
        is_healthy = await gemini_service.check_health()
        return {"ollama_healthy": is_healthy, "model": gemini_service.model}
    except Exception as e:
        return {"ollama_healthy": False, "error": str(e)}


@router.get("/health")
async def get_service_health():
    """Get question generation service health"""
    try:
        ollama_healthy = await gemini_service.check_health()
        return {
            "status": "healthy" if ollama_healthy else "degraded",
            "gemini_service": ollama_healthy,
            "database": "connected",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "timestamp": datetime.now().isoformat()}


@router.post("/{interview_id}/generate-questions", response_model=QuestionGenerationResponse)
async def generate_questions(
    interview_id: int,
    request: QuestionGenerationRequest,
    db: Session = Depends(get_db)
):
    """Generate questions for an interview using AI"""

    # Get interview details
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Check if questions already exist — only one-time generation allowed
    existing_count = db.query(QuestionModel).filter(QuestionModel.interview_id == interview_id).count()
    if existing_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Questions already exist for this interview ({existing_count} questions). You can edit existing questions instead of regenerating."
        )

    # Build configuration for prompt builder from interview data
    from src.models.question_schemas import QuestionGenerationConfig as QGConfig
    config = QGConfig(
        job_position=interview.job_position or "Software Engineer",
        interview_type=interview.interview_type or "technical",
        difficulty_level=interview.difficulty_level or "Medium",
        skills_config=json.loads(interview.skills_config_data) if interview.skills_config_data else {},
        question_format=json.loads(interview.question_format_data) if interview.question_format_data else {},
        evaluation_rules=json.loads(interview.evaluation_rules_data) if interview.evaluation_rules_data else {},
        ai_generation_rules=json.loads(interview.ai_generation_rules_data) if interview.ai_generation_rules_data else {}
    )

    # Resolve per-type counts (3-level fallback)
    per_type_counts = {}
    if request.per_type_counts:
        # Level 1: Frontend sent explicit per-type counts
        per_type_counts = dict(request.per_type_counts)
    elif config.question_format:
        # Level 2: Read from interview's stored question_format_data
        qf = config.question_format
        type_key_map = {'mcq': 'multiple_choice', 'coding': 'coding', 'descriptive': 'short_answer', 'case_study': 'case_study', 'aptitude': 'aptitude', 'communication': 'communication'}
        for form_key, api_type in type_key_map.items():
            count = qf.get(form_key, 0)
            if count and int(count) > 0:
                per_type_counts[api_type] = int(count)
    if not per_type_counts and request.question_types:
        # Level 3: Distribute evenly among requested types
        type_values = [qt.value for qt in request.question_types]
        base_count = request.question_count // len(type_values)
        remainder = request.question_count % len(type_values)
        for i, t in enumerate(type_values):
            per_type_counts[t] = base_count + (1 if i < remainder else 0)
    if not per_type_counts:
        # Final fallback: all questions are the first requested type or multiple_choice
        fallback_type = request.question_types[0].value if request.question_types else 'multiple_choice'
        per_type_counts = {fallback_type: request.question_count}

    # For non-technical interviews, drop coding/technical types — they don't fit the role.
    if (interview.interview_type or "").lower().replace("_", "-") == "non-technical":
        removed = {t: c for t, c in per_type_counts.items() if t in ("coding", "technical")}
        if removed:
            per_type_counts = {t: c for t, c in per_type_counts.items() if t not in ("coding", "technical")}
            if not per_type_counts:
                per_type_counts = {"multiple_choice": sum(removed.values()) or request.question_count}

    # Type ordering: MCQs first, aptitude next, case study, then coding last
    TYPE_ORDER = ['multiple_choice', 'aptitude', 'communication', 'short_answer', 'behavioral', 'technical', 'situational', 'case_study', 'coding']
    prompt_builder = PromptBuilder()
    # HR-chosen coding language (applies only to coding questions).
    code_lang = normalize_coding_language(getattr(request, 'coding_language', None))
    all_generated_questions = []

    # Extract skills detail for prompt enrichment
    skills_detail = None
    if config.skills_config and config.skills_config.get('skills'):
        skills_detail = [
            s for s in config.skills_config['skills']
            if s.get('name', '').strip()
        ]

    # Extract AI generation rules
    ai_rules = config.ai_generation_rules or {}
    coding_focus_areas = ai_rules.get('coding_focus_areas') or None
    avoid_topics = ai_rules.get('avoid_topics') or None
    real_world_scenarios = ai_rules.get('real_world_scenarios', False)

    try:
        # Generate each type separately with type-specific prompts
        sorted_types = sorted(per_type_counts.keys(), key=lambda t: TYPE_ORDER.index(t) if t in TYPE_ORDER else 99)

        for q_type in sorted_types:
            type_count = per_type_counts[q_type]
            if type_count <= 0:
                continue

            # Coding language applies only to coding; coding uses a smaller batch so
            # the (large) JSON doesn't overrun the model's output budget and truncate.
            q_lang = code_lang if q_type == 'coding' else ''
            batch_size = 2 if q_type == 'coding' else 5

            type_questions = []
            for batch_start in range(0, type_count, batch_size):
                batch_count = min(batch_size, type_count - batch_start)

                prompt = prompt_builder.build_generation_prompt(
                    job_position=config.job_position,
                    interview_type=config.interview_type,
                    difficulty_level=config.difficulty_level,
                    question_count=batch_count,
                    question_types=[q_type],  # Single type only
                    categories=request.categories,
                    skills_detail=skills_detail,
                    coding_focus_areas=coding_focus_areas,
                    avoid_topics=avoid_topics,
                    real_world_scenarios=real_world_scenarios,
                    mcq_angles=request.mcq_angles,
                    coding_language=q_lang,
                )

                generated = await gemini_service.generate_questions(prompt, batch_count)

                # Retry for shortfall
                retry_count = 0
                while len(generated) < batch_count and retry_count < 2:
                    retry_count += 1
                    shortfall = batch_count - len(generated)
                    extra_prompt = prompt_builder.build_generation_prompt(
                        job_position=config.job_position,
                        interview_type=config.interview_type,
                        difficulty_level=config.difficulty_level,
                        question_count=shortfall,
                        question_types=[q_type],
                        categories=request.categories,
                        mcq_angles=request.mcq_angles,
                        coding_language=q_lang,
                    )
                    extra = await gemini_service.generate_questions(extra_prompt, shortfall)
                    generated.extend(extra)

                type_questions.extend(generated[:batch_count])  # Trim excess

            # Validate and enforce type on each question
            for q in type_questions:
                q.question_type = q_type
                _validate_question_content(q, q_type, language=q_lang)

            # Texts already saved for this interview (across earlier type batches in this run)
            # — skip any duplicate so a single generation can't yield in-test duplicates.
            existing = {
                _normalize_text(t)
                for (t,) in db.query(QuestionModel.question_text).filter(
                    QuestionModel.interview_id == interview_id
                )
            }

            # Save to DB
            for q in type_questions:
                key = _normalize_text(q.question_text)
                if key in existing:
                    continue
                existing.add(key)
                db_question = QuestionModel(
                    interview_id=interview_id,
                    question_text=q.question_text,
                    question_type=q.question_type,
                    difficulty_level=q.difficulty_level,
                    category=q.category,
                    bloom_taxonomy_level=q.bloom_taxonomy_level,
                    time_limit_minutes=_configured_time(interview, q_type),
                    points=q.points or (20 if q_type == 'coding' else 1),
                    options=json.dumps(q.options) if q.options else None,
                    correct_answer=q.correct_answer,
                    sample_answer=q.sample_answer,
                    code_template=q.code_template,
                    test_cases=json.dumps(q.test_cases) if getattr(q, 'test_cases', None) else None,
                    language=(q_lang or None),
                    status=QuestionStatus.GENERATED,
                    generation_prompt=prompt,
                    generation_model=gemini_service.model,
                    generation_temperature="0.7"
                )
                db.add(db_question)
                all_generated_questions.append(db_question)

            db.commit()

        # Refresh and convert to response format
        response_questions = []
        for db_q in all_generated_questions:
            db.refresh(db_q)
            response_questions.append(_db_question_to_schema(db_q))

        return QuestionGenerationResponse(
            success=True,
            message=f"Successfully generated {len(response_questions)} questions",
            questions_generated=len(response_questions),
            questions=response_questions
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise _generation_http_error(e)


@router.get("/questions/search", response_model=List[Question])
def search_questions_by_skill(
    skill_name: str = Query(..., min_length=1),
    difficulty: Optional[str] = Query(None),
    question_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """Search previously generated questions by skill name with optional filters.

    Only genuine reusable bank masters (is_pool_question=True) are returned; per-interview
    assignment copies created by import are excluded so the picker doesn't grow with repeats.
    Results are de-duped by normalized question text (newest kept) as a defensive measure in
    case the bank already contains duplicate rows.
    """
    query = db.query(QuestionModel).filter(
        QuestionModel.skill_name.ilike(f"%{skill_name}%"),
        QuestionModel.is_pool_question == True,
    )
    if difficulty:
        query = query.filter(QuestionModel.difficulty_level.ilike(difficulty))
    if question_type:
        query = query.filter(QuestionModel.question_type == question_type)
    if status:
        query = query.filter(QuestionModel.status == status)

    # Pull a larger working set ordered newest-first, then collapse duplicate texts so the
    # post-dedupe result can still fill `limit`.
    rows = query.order_by(QuestionModel.created_at.desc()).limit(limit * 5).all()
    seen = set()
    deduped = []
    for q in rows:
        key = _normalize_text(q.question_text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
        if len(deduped) >= limit:
            break
    return [_db_question_to_schema(q) for q in deduped]


@router.post("/{interview_id}/import-questions", response_model=QuestionGenerationResponse)
def import_questions(
    interview_id: int,
    body: ImportQuestionsRequest,
    db: Session = Depends(get_db)
):
    """Copy previously generated questions into the current interview's pool."""
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Texts already present in this interview — skip importing any that match so a test never
    # holds the same question twice (covers re-opening/re-importing the same selection).
    existing = {
        _normalize_text(t)
        for (t,) in db.query(QuestionModel.question_text).filter(
            QuestionModel.interview_id == interview_id
        )
    }

    imported = []
    skipped = 0
    for qid in body.question_ids:
        src = db.query(QuestionModel).filter(QuestionModel.id == qid).first()
        if not src:
            continue
        key = _normalize_text(src.question_text)
        if key in existing:
            skipped += 1
            continue
        existing.add(key)  # guard against duplicate ids within this same request
        copy = QuestionModel(
            id=str(uuid.uuid4()),
            interview_id=interview_id,
            question_text=src.question_text,
            question_type=src.question_type,
            difficulty_level=src.difficulty_level,
            category=src.category,
            bloom_taxonomy_level=src.bloom_taxonomy_level,
            options=src.options,
            correct_answer=src.correct_answer,
            sample_answer=src.sample_answer,
            code_template=src.code_template,
            test_cases=src.test_cases,
            # Re-stamped from this test's configured per-type time rather than copied: an
            # imported question carrying the source test's minutes would skew this test's total.
            time_limit_minutes=_configured_time(interview, src.question_type),
            points=src.points,
            skill_name=src.skill_name,
            # Imported questions join THIS interview's pool so set assembly (here) and
            # invite-time assembly (hr_routes) can use them, exactly like AI-generated
            # pool questions. They arrive pre-vetted (the user picked them), so approve
            # them. The "Use Previous Questions" search de-dupes by normalized text, so
            # these copies don't clutter the picker despite being pool questions.
            is_pool_question=True,
            status="approved",
            is_approved=True,
            generation_model=src.generation_model,
            generation_temperature=src.generation_temperature,
        )
        db.add(copy)
        imported.append(copy)

    db.commit()
    for q in imported:
        db.refresh(q)

    msg = f"Imported {len(imported)} question(s) into interview {interview_id}"
    if skipped:
        msg += f", skipped {skipped} duplicate(s) already present"
    return QuestionGenerationResponse(
        success=True,
        message=msg,
        questions_generated=len(imported),
        questions=[_db_question_to_schema(q) for q in imported]
    )


# ==================== MANUAL / CSV MCQ ENTRY ====================

_DIFFICULTY_MAP = {"easy": "Easy", "medium": "Medium", "hard": "Hard"}


def _letter_for(correct_raw, options):
    """Normalize a correct-answer value to an option letter ('A'..).

    Accepts a single letter (A-H, any case), a 1-based number, or the exact
    option text (case-insensitive). Returns None if it can't be resolved.
    """
    if correct_raw is None:
        return None
    s = str(correct_raw).strip()
    if not s:
        return None
    # Single letter, e.g. "B" / "b"
    if len(s) == 1 and s.upper().isalpha() and "A" <= s.upper() <= "H":
        return s.upper()
    # 1-based index, e.g. "2" -> "B"
    if s.isdigit():
        idx = int(s) - 1
        if 0 <= idx < len(options):
            return chr(65 + idx)
        return None
    # Exact option text match
    for i, opt in enumerate(options):
        if str(opt).strip().lower() == s.lower():
            return chr(65 + i)
    return None


def _create_manual_pool_questions(db, interview_id, skill_name, question_type, difficulty, marks, items):
    """Create pool MCQs from manual/CSV items. Returns (created, skipped, errors)."""
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    existing = {
        _normalize_text(t)
        for (t,) in db.query(QuestionModel.question_text).filter(
            QuestionModel.interview_id == interview_id
        )
    }
    q_type = question_type or "multiple_choice"
    diff = _DIFFICULTY_MAP.get(str(difficulty or "").strip().lower(), "Medium")
    created, skipped, errors = [], 0, []

    for i, it in enumerate(items):
        row = i + 1
        qtext = (it.question_text or "").strip()
        opts = normalize_mcq_options(it.options)
        if not qtext:
            errors.append(f"Row {row}: missing question text")
            continue
        if len(opts) < 2:
            errors.append(f"Row {row}: needs at least 2 options")
            continue
        letter = _letter_for(it.correct_answer, opts)
        if letter is None or (ord(letter) - 65) >= len(opts):
            errors.append(f"Row {row}: correct option '{it.correct_answer}' is invalid")
            continue

        key = _normalize_text(qtext)
        if key in existing:
            skipped += 1
            continue
        existing.add(key)

        db_q = QuestionModel(
            interview_id=interview_id,
            question_text=qtext,
            question_type=q_type,
            difficulty_level=diff,
            category=skill_name,
            time_limit_minutes=_configured_time(interview, q_type),
            points=marks or 1,
            options=json.dumps(opts),
            correct_answer=letter,
            skill_name=skill_name,
            is_pool_question=True,
            status="approved",
            is_approved=True,
            generation_model="manual",
        )
        db.add(db_q)
        created.append(db_q)

    db.commit()
    for q in created:
        db.refresh(q)
    return created, skipped, errors


def _parse_mcq_csv(text):
    """Parse an MCQ CSV into ManualMCQItem list. Returns (items, errors).

    Accepts an optional header row (auto-detected by a 'question' cell). With a
    header, columns are matched by fuzzy names; otherwise positional:
    col0 = question, middle cols = options, last col = correct option.
    """
    items, errors = [], []
    rows = [r for r in csv.reader(io.StringIO(text)) if any((c or "").strip() for c in r)]
    if not rows:
        return items, errors

    def _norm(h):
        return "".join(ch for ch in str(h).strip().lower() if ch.isalnum())

    first = [_norm(c) for c in rows[0]]
    has_header = any("question" in c for c in first)

    if has_header:
        header = first
        q_idx = next((i for i, c in enumerate(header) if "question" in c), 0)
        corr_idx = next(
            (i for i, c in enumerate(header) if c in ("correct", "correctoption", "correctanswer", "answer", "correctopt")),
            len(header) - 1,
        )
        opt_idxs = [i for i, c in enumerate(header) if c.startswith("option") or (len(c) == 1 and "a" <= c <= "h")]
        opt_idxs = [i for i in opt_idxs if i not in (q_idx, corr_idx)]
        if not opt_idxs:
            opt_idxs = [i for i in range(len(header)) if i not in (q_idx, corr_idx)]
        data_rows = rows[1:]
    else:
        q_idx, corr_idx = 0, len(rows[0]) - 1
        opt_idxs = list(range(1, len(rows[0]) - 1))
        data_rows = rows

    for n, r in enumerate(data_rows, start=(2 if has_header else 1)):
        def cell(i):
            return r[i].strip() if i < len(r) and r[i] is not None else ""
        qtext = cell(q_idx)
        opts = [cell(i) for i in opt_idxs if cell(i)]
        correct = cell(corr_idx)
        if not qtext and not opts:
            continue
        items.append(ManualMCQItem(question_text=qtext, options=opts, correct_answer=correct))
    return items, errors


@router.post("/{interview_id}/manual-questions", response_model=QuestionGenerationResponse)
def add_manual_questions(
    interview_id: int,
    body: ManualQuestionsRequest,
    db: Session = Depends(get_db)
):
    """Add manually-entered MCQs into this interview's pool (assembly-ready)."""
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    created, skipped, errors = _create_manual_pool_questions(
        db, interview_id, body.skill_name, body.question_type,
        body.difficulty, body.marks_per_question, body.questions,
    )
    if not created and errors:
        raise HTTPException(status_code=400, detail="; ".join(errors[:5]))

    msg = f"Added {len(created)} question(s)"
    if skipped:
        msg += f", skipped {skipped} duplicate(s)"
    if errors:
        msg += f", {len(errors)} row(s) had errors"
    return QuestionGenerationResponse(
        success=True,
        message=msg,
        questions_generated=len(created),
        questions=[_db_question_to_schema(q) for q in created],
    )


@router.post("/{interview_id}/manual-questions/csv", response_model=QuestionGenerationResponse)
async def add_manual_questions_csv(
    interview_id: int,
    file: UploadFile = File(...),
    skill_name: str = Form(...),
    question_type: str = Form("multiple_choice"),
    difficulty: str = Form("MEDIUM"),
    marks_per_question: int = Form(1),
    db: Session = Depends(get_db),
):
    """Bulk-add MCQs from a CSV (columns: question, option_a..option_d, correct_option)."""
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    items, parse_errors = _parse_mcq_csv(text)
    if not items:
        raise HTTPException(
            status_code=400,
            detail="No valid rows found. Expected columns: question, option_a, option_b, option_c, option_d, correct_option",
        )

    created, skipped, errors = _create_manual_pool_questions(
        db, interview_id, skill_name, question_type, difficulty, marks_per_question, items,
    )
    all_errors = parse_errors + errors
    if not created and all_errors:
        raise HTTPException(status_code=400, detail="; ".join(all_errors[:5]))

    msg = f"Imported {len(created)} question(s) from CSV"
    if skipped:
        msg += f", skipped {skipped} duplicate(s)"
    if all_errors:
        msg += f", {len(all_errors)} row(s) had errors"
    return QuestionGenerationResponse(
        success=True,
        message=msg,
        questions_generated=len(created),
        questions=[_db_question_to_schema(q) for q in created],
    )


@router.get("/{interview_id}/questions", response_model=QuestionListResponseBase)
def get_questions(
    interview_id: int,
    status: Optional[str] = Query(None),
    question_type: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Get all questions for an interview with optional filtering"""

    # Verify interview exists
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Build query
    query = db.query(QuestionModel).filter(QuestionModel.interview_id == interview_id).order_by(QuestionModel.created_at)

    if status:
        query = query.filter(QuestionModel.status == status)
    if question_type:
        query = query.filter(QuestionModel.question_type == question_type)

    db_questions = query.all()
    questions = [_db_question_to_schema(q) for q in db_questions]

    total = len(questions)
    approved_count = len([q for q in questions if q.is_approved])
    generated_count = len([q for q in questions if q.status == QuestionStatus.GENERATED])

    return QuestionListResponseBase(
        questions=questions,
        total=total,
        approved_count=approved_count,
        generated_count=generated_count
    )


@router.put("/{interview_id}/questions/{question_id}", response_model=Question)
def update_question(
    interview_id: int,
    question_id: str,
    question_update: QuestionUpdate,
    db: Session = Depends(get_db)
):
    """Update a specific question"""

    db_question = db.query(QuestionModel).filter(
        QuestionModel.id == question_id,
        QuestionModel.interview_id == interview_id
    ).first()

    if not db_question:
        raise HTTPException(status_code=404, detail="Question not found")

    update_data = question_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if field in ("options", "test_cases") and value is not None:
            setattr(db_question, field, json.dumps(value))
        else:
            setattr(db_question, field, value)

    if question_update.is_approved is True:
        db_question.approved_by = "current_user"
        db_question.approval_notes = question_update.approval_notes

    db_question.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_question)

    return _db_question_to_schema(db_question)


@router.post("/{interview_id}/questions/{question_id}/regenerate", response_model=Question)
async def regenerate_question(
    interview_id: int,
    question_id: str,
    db: Session = Depends(get_db)
):
    """Regenerate a single question"""

    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    db_question = db.query(QuestionModel).filter(
        QuestionModel.id == question_id,
        QuestionModel.interview_id == interview_id
    ).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="Question not found")

    context = {
        "job_position": interview.job_position or "Software Engineer",
        "interview_type": interview.interview_type or "technical",
        "difficulty_level": interview.difficulty_level or "Medium",
        "category": db_question.category or "General",
        "question_type": db_question.question_type or "multiple_choice",
    }

    try:
        new_question = await gemini_service.regenerate_single_question(
            db_question.question_text,
            context
        )

        if not new_question:
            raise HTTPException(status_code=500, detail="Failed to regenerate question")

        # Normalize/repair the regenerated question exactly like batch generation
        # does: sanitize the starter, strip mismatched fields, and validate + top up
        # coding test cases. Without this a regenerated coding question loses its
        # test cases and becomes ungradable.
        _validate_question_content(new_question, new_question.question_type)

        db_question.question_text = new_question.question_text
        db_question.question_type = new_question.question_type
        db_question.difficulty_level = new_question.difficulty_level
        db_question.category = new_question.category
        db_question.bloom_taxonomy_level = new_question.bloom_taxonomy_level
        db_question.time_limit_minutes = new_question.time_limit_minutes or 5
        db_question.points = new_question.points or (20 if (new_question.question_type or "").lower() == 'coding' else 1)
        db_question.options = json.dumps(new_question.options) if new_question.options else None
        db_question.correct_answer = new_question.correct_answer
        db_question.sample_answer = new_question.sample_answer
        db_question.code_template = new_question.code_template
        db_question.test_cases = json.dumps(new_question.test_cases) if getattr(new_question, 'test_cases', None) else None
        db_question.status = QuestionStatus.GENERATED
        db_question.is_approved = False
        db_question.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(db_question)

        return _db_question_to_schema(db_question)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to regenerate question: {str(e)}")


@router.delete("/{interview_id}/questions")
def delete_all_questions(
    interview_id: int,
    db: Session = Depends(get_db)
):
    """Delete all questions for an interview"""

    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    deleted_count = db.query(QuestionModel).filter(
        QuestionModel.interview_id == interview_id
    ).delete()

    db.commit()

    return {"message": f"Deleted {deleted_count} questions for interview {interview_id}"}


# ==================== SKILL-BASED GENERATION & TEST SETS ====================


@router.post("/{interview_id}/generate-skill-questions", response_model=QuestionGenerationResponse)
async def generate_skill_questions(
    interview_id: int,
    request: GeneratePoolRequest,
    db: Session = Depends(get_db)
):
    """Generate pool questions for a specific skill and question type"""

    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    total_to_generate = request.pool_size
    q_type = request.question_type

    # Non-technical interviews must not produce coding/technical questions.
    if (interview.interview_type or "").lower().replace("_", "-") == "non-technical" and q_type in ("coding", "technical"):
        raise HTTPException(
            status_code=400,
            detail=f"Question type '{q_type}' is not allowed for non-technical interviews"
        )

    prompt_builder = PromptBuilder()
    # Coding questions are large; generating 5 at once overruns the model's output
    # budget and truncates the JSON. Smaller batches for coding.
    code_lang = normalize_coding_language(request.language) if q_type == 'coding' else ''
    batch_size = 2 if q_type == 'coding' else 5
    all_generated = []

    # Extract AI generation rules from interview config
    ai_rules = {}
    if interview.ai_generation_rules_data:
        ai_rules = json.loads(interview.ai_generation_rules_data)

    try:
        for batch_start in range(0, total_to_generate, batch_size):
            batch_count = min(batch_size, total_to_generate - batch_start)

            prompt = prompt_builder.build_generation_prompt(
                job_position=interview.job_position or "Software Engineer",
                interview_type=interview.interview_type or "technical",
                difficulty_level=request.difficulty,
                question_count=batch_count,
                question_types=[q_type],
                categories=[request.skill_name],
                skills_detail=[{"name": request.skill_name, "question_count": batch_count}],
                coding_focus_areas=ai_rules.get('coding_focus_areas'),
                avoid_topics=ai_rules.get('avoid_topics'),
                real_world_scenarios=ai_rules.get('real_world_scenarios', False),
                coding_language=code_lang,
            )

            generated = await gemini_service.generate_questions(prompt, batch_count)

            # Retry for shortfall
            retry_count = 0
            while len(generated) < batch_count and retry_count < 2:
                retry_count += 1
                shortfall = batch_count - len(generated)
                extra = await gemini_service.generate_questions(prompt, shortfall)
                generated.extend(extra)

            all_generated.extend(generated[:batch_count])

        # Existing bank masters for this skill — skip storing any generated question whose
        # normalized text already exists so regenerating a skill never piles up duplicates.
        existing = {
            _normalize_text(t)
            for (t,) in db.query(QuestionModel.question_text).filter(
                QuestionModel.is_pool_question == True,
                QuestionModel.skill_name == request.skill_name,
            )
        }

        # Validate, enforce type, and save to DB
        saved_questions = []
        skipped = 0
        for q in all_generated:
            q.question_type = q_type
            _validate_question_content(q, q_type, language=code_lang)

            key = _normalize_text(q.question_text)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)  # also dedup within this generation batch

            db_question = QuestionModel(
                interview_id=interview_id,
                question_text=q.question_text,
                question_type=q.question_type,
                difficulty_level=q.difficulty_level,
                category=q.category or request.skill_name,
                bloom_taxonomy_level=q.bloom_taxonomy_level,
                # HR's configured time wins over whatever the model suggested — it is what the
                # test's advertised duration was computed from.
                time_limit_minutes=request.time_per_question or _configured_time(interview, q_type),
                points=request.marks_per_question,
                options=json.dumps(q.options) if q.options else None,
                correct_answer=q.correct_answer,
                sample_answer=q.sample_answer,
                code_template=q.code_template,
                test_cases=json.dumps(q.test_cases) if getattr(q, 'test_cases', None) else None,
                language=(code_lang or None),
                skill_name=request.skill_name,
                is_pool_question=True,
                status=QuestionStatus.GENERATED,
                generation_prompt=prompt,
                generation_model=gemini_service.model,
                generation_temperature="0.7"
            )
            db.add(db_question)
            saved_questions.append(db_question)

        db.commit()

        response_questions = []
        for db_q in saved_questions:
            db.refresh(db_q)
            response_questions.append(_db_question_to_schema(db_q))

        msg = f"Generated {len(response_questions)} questions for skill '{request.skill_name}'"
        if skipped:
            msg += f" (skipped {skipped} already in the question bank)"
        return QuestionGenerationResponse(
            success=True,
            message=msg,
            questions_generated=len(response_questions),
            questions=response_questions
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise _generation_http_error(e)


@router.post("/{interview_id}/assemble-sets", response_model=ConfigureAssemblyResponse)
def configure_test_assembly(
    interview_id: int,
    request: AssembleSetsRequest,
    db: Session = Depends(get_db)
):
    """Configure on-the-fly test assembly from approved pool questions.
    Saves skill_type_targets on the interview and validates pool readiness.
    Tests are assembled randomly per candidate at invite time."""

    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Promote every non-rejected question on this interview to an approved pool question:
    #  - saves the user from clicking "Accept All" before saving, and
    #  - rescues imported "previous questions" (older copies were saved as non-pool, which
    #    left them invisible to set assembly and caused "No pool questions found").
    # Idempotent for questions that are already approved pool questions.
    db.query(QuestionModel).filter(
        QuestionModel.interview_id == interview_id,
        QuestionModel.status != "rejected"
    ).update(
        {"is_pool_question": True, "is_approved": True, "status": "approved"},
        synchronize_session=False
    )

    # Re-stamp every question's time from the test's configured per-type map. This is the one
    # point every question passes through no matter where it came from (AI, import, manual CSV),
    # so it is what keeps the candidate's clock — the sum of these values, see OnlineTest.jsx —
    # equal to the duration the test advertises.
    for q in db.query(QuestionModel).filter(QuestionModel.interview_id == interview_id):
        q.time_limit_minutes = _configured_time(interview, q.question_type)
    db.commit()

    # Get all accepted pool questions grouped by (skill_name, question_type)
    accepted = db.query(QuestionModel).filter(
        QuestionModel.interview_id == interview_id,
        QuestionModel.is_pool_question == True,
        QuestionModel.is_approved == True
    ).all()

    if not accepted:
        raise HTTPException(status_code=400, detail="No pool questions found. Generate questions first.")

    by_skill_type = {}
    for q in accepted:
        key = (q.skill_name or "_unknown", q.question_type)
        by_skill_type.setdefault(key, []).append(q)

    # Validate pool readiness and build status report
    pool_status = []
    has_insufficient = False
    for target in request.skill_type_targets:
        key = (target.skill_name, target.question_type)
        available = len(by_skill_type.get(key, []))
        needed = target.count_per_set

        if available < needed:
            status = "insufficient"
            has_insufficient = True
        elif available < needed * 3:
            status = "tight"
        else:
            status = "ready"

        pool_status.append(PoolTargetStatus(
            skill_name=target.skill_name,
            question_type=target.question_type,
            needed=needed,
            available=available,
            status=status
        ))

    if has_insufficient:
        insufficient = [p for p in pool_status if p.status == "insufficient"]
        details = "; ".join(
            f"'{p.skill_name}/{p.question_type}': need {p.needed}, have {p.available}"
            for p in insufficient
        )
        raise HTTPException(status_code=400, detail=f"Insufficient approved questions: {details}")

    # Save targets on interview for on-the-fly assembly at invite time
    targets_json = [
        {"skill_name": t.skill_name, "question_type": t.question_type, "count_per_set": t.count_per_set}
        for t in request.skill_type_targets
    ]
    interview.skill_type_targets_data = json.dumps(targets_json)

    # Recompute the advertised duration from the same numbers, so a config edit between
    # generation and save can't leave duration_minutes describing an older set of times.
    interview.duration_minutes = sum(
        t.count_per_set * _configured_time(interview, t.question_type)
        for t in request.skill_type_targets
    ) or interview.duration_minutes
    db.commit()

    return ConfigureAssemblyResponse(
        success=True,
        message="Test assembly configured. Questions will be randomly assembled per candidate at invite time.",
        skill_type_targets=[
            SkillTypeTarget(skill_name=t.skill_name, question_type=t.question_type, count_per_set=t.count_per_set)
            for t in request.skill_type_targets
        ],
        pool_status=pool_status
    )


@router.get("/{interview_id}/test-sets")
def get_test_sets(
    interview_id: int,
    db: Session = Depends(get_db)
):
    """Get all assembled test sets for an interview"""

    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    set_numbers = db.query(QuestionSetAssignment.set_number).filter(
        QuestionSetAssignment.interview_id == interview_id
    ).distinct().order_by(QuestionSetAssignment.set_number).all()
    set_numbers = [r[0] for r in set_numbers]

    result = []
    for set_num in set_numbers:
        assignments = db.query(QuestionSetAssignment).filter(
            QuestionSetAssignment.interview_id == interview_id,
            QuestionSetAssignment.set_number == set_num
        ).order_by(QuestionSetAssignment.question_order).all()

        questions = []
        for asgn in assignments:
            q = db.query(QuestionModel).filter(QuestionModel.id == asgn.question_id).first()
            if q:
                questions.append({
                    "id": asgn.id,
                    "question_id": q.id,
                    "question_order": asgn.question_order,
                    "question": _db_question_to_schema(q).model_dump()
                })

        result.append({
            "set_number": set_num,
            "questions": questions
        })

    return {"sets": result, "total_sets": len(result)}
