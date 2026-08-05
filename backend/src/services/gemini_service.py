"""
🤖 GEMINI SERVICE
AI service for generating interview questions (and free-form text) using Google
Gemini via the ``google-genai`` SDK. Keeps the same public surface
(``gemini_service`` instance with ``check_health``, ``generate_questions``,
``generate_text`` and ``regenerate_single_question``, plus the module-level
``_generate_content`` and ``GEMINI_MODEL``) so the rest of the codebase is
unchanged — only this file talks to the model provider.

Two auth modes are supported, selected automatically (see ``_get_client``):
  * **API key** (``GEMINI_API_KEY``) → the public Gemini Developer API. This is
    the mode used off-GCP (e.g. cPanel), where no metadata server exists.
  * **Vertex AI** (``GOOGLE_CLOUD_PROJECT``) → Application Default Credentials.
    Retained so a GCP deployment keeps working unchanged.
The API key wins when both are set. The rest of the module is provider-agnostic:
``google-genai`` exposes the same client surface, and ``GenerateContentConfig`` /
``ThinkingConfig`` behave identically in both modes.
"""

import os
import json
import logging
from typing import List, Optional
from pydantic import BaseModel
from enum import Enum
import asyncio

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


from src.mcq_options import normalize_mcq_options

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Provider configuration. Set GEMINI_API_KEY for the Gemini Developer API (no GCP
# account needed — this is what off-GCP deployments use), or GOOGLE_CLOUD_PROJECT
# for Vertex AI with Application Default Credentials (`gcloud auth
# application-default login` locally, or an attached service account with
# roles/aiplatform.user on Cloud Run). Model/region stay overridable via env so
# ops can switch without a code change (e.g. gemini-2.5-flash-lite).
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = 300  # seconds (5 min for batch generation)
MAX_RETRIES = 3
# Vertex quota (429 / RESOURCE_EXHAUSTED) needs a much longer pause than a transient
# error — a 1-2s backoff just burns the remaining attempts against the same full bucket.
RATE_LIMIT_BACKOFF_SECONDS = 20


def is_rate_limit_error(exc: Exception) -> bool:
    """True if an exception looks like a Vertex/Gemini quota or rate-limit (429)."""
    s = f"{type(exc).__name__} {exc}".lower()
    return ("429" in s or "resource_exhausted" in s or "rate limit" in s
            or "ratelimit" in s or "quota" in s or "too many requests" in s)

def credentials_configured() -> bool:
    """True if either auth mode has been configured. Used by health checks so they
    don't have to know which provider is active."""
    return bool(GEMINI_API_KEY or GOOGLE_CLOUD_PROJECT)


def sdk_available() -> bool:
    """True if the google-genai package actually imported.

    Checked separately from credentials because the import is swallowed above: a
    requirements file that omits google-genai otherwise looks perfectly healthy
    until the first generation call fails MAX_RETRIES deep as an opaque 500.
    """
    return genai is not None


# Lazily-constructed client (cached). Built on first use rather than at import
# time so importing this module never requires credentials to be present.
_client = None


def _get_client():
    """Return a cached google-genai client.

    Prefers the API key (Gemini Developer API) and falls back to Vertex AI when
    only a GCP project is configured, so the same code runs on and off GCP.
    """
    global _client
    if _client is None:
        if genai is None:
            raise Exception("google-genai SDK is not installed")

        http_options = types.HttpOptions(timeout=GEMINI_TIMEOUT * 1000)  # ms
        if GEMINI_API_KEY:
            _client = genai.Client(api_key=GEMINI_API_KEY, http_options=http_options)
            logger.info("Gemini client initialized (Developer API, model=%s)", GEMINI_MODEL)
        elif GOOGLE_CLOUD_PROJECT:
            _client = genai.Client(
                vertexai=True,
                project=GOOGLE_CLOUD_PROJECT,
                location=VERTEX_LOCATION,
                http_options=http_options,
            )
            logger.info(
                "Gemini client initialized (Vertex AI, project=%s, location=%s, model=%s)",
                GOOGLE_CLOUD_PROJECT, VERTEX_LOCATION, GEMINI_MODEL,
            )
        else:
            raise Exception(
                "No Gemini credentials configured: set GEMINI_API_KEY (Gemini "
                "Developer API) or GOOGLE_CLOUD_PROJECT (Vertex AI)"
            )
    return _client


class QuestionType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    CODING = "coding"
    BEHAVIORAL = "behavioral"
    TECHNICAL = "technical"
    SITUATIONAL = "situational"


class DifficultyLevel(str, Enum):
    EASY = "Easy"
    MEDIUM = "Medium"
    HARD = "Hard"


class QuestionCreate(BaseModel):
    question_text: str
    question_type: str
    difficulty_level: str
    category: Optional[str] = None
    bloom_taxonomy_level: Optional[str] = None
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    sample_answer: Optional[str] = None
    code_template: Optional[str] = None
    explanation: Optional[str] = None
    test_cases: Optional[List[dict]] = None
    time_limit_minutes: Optional[int] = None
    points: Optional[int] = None


def _generate_content(prompt: str, *, temperature: float = 0.7, top_p: float = 0.9,
                      max_output_tokens: int = 4000, timeout: int = GEMINI_TIMEOUT,
                      thinking_budget: Optional[int] = None) -> str:
    """Make a single synchronous call to Gemini's generateContent and return
    the plain text. Shared by the service methods and the few callers that need a
    raw completion (JD generation, SQL test-case seeding).

    thinking_budget: when set (pass 0 to disable), caps the model's internal
    "thinking" tokens. On gemini-2.5-flash those tokens are spent out of
    max_output_tokens, so a small budget can be fully consumed by thinking and
    truncate the visible answer mid-string — disable it for short JSON tasks."""
    cfg_kwargs = dict(
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_output_tokens,
        # Honor the per-call timeout (e.g. sql_testgen uses 180s); value in ms.
        http_options=types.HttpOptions(timeout=timeout * 1000),
    )
    if thinking_budget is not None:
        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
    resp = _get_client().models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(**cfg_kwargs),
    )

    if not resp.candidates:
        # Surface safety blocks / empty completions explicitly.
        feedback = getattr(resp, "prompt_feedback", None)
        raise Exception(f"Gemini returned no candidates (feedback: {feedback})")
    return (resp.text or "").strip()


class GeminiService:
    def __init__(self, model: str = GEMINI_MODEL):
        self.model = model

    async def check_health(self) -> bool:
        """The provider is reachable as long as credentials are configured (an API
        key, or a GCP project for Vertex ADC). We avoid a network round-trip here
        because callers invoke this before every generation; missing credentials
        are the hard failure to guard."""
        if not sdk_available():
            logger.error(
                "Gemini health check failed: the google-genai package is not "
                "installed (pip install 'google-genai>=1.0,<2')"
            )
            return False
        if not credentials_configured():
            logger.warning(
                "Gemini health check failed: neither GEMINI_API_KEY nor "
                "GOOGLE_CLOUD_PROJECT is set"
            )
            return False
        return True

    async def generate_questions(self, prompt: str, question_count: int = 10) -> List[QuestionCreate]:
        """Generate interview questions using Gemini."""
        if not await self.check_health():
            raise Exception(
                "Gemini service not available: either the google-genai package is "
                "not installed or no credentials are configured (GEMINI_API_KEY / "
                "GOOGLE_CLOUD_PROJECT) — see the preceding log line for which"
            )

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                questions = await self._call_gemini(prompt)
                if questions and len(questions) > 0:
                    return questions
                # A parseable-but-empty result usually means the JSON was truncated
                # (large coding batch overran the output budget). Treat it as retryable
                # WITH a backoff instead of instantly consuming the next attempt.
                last_error = Exception("model returned no parseable questions (response may be truncated)")
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1} failed: {e}")

            if attempt < MAX_RETRIES - 1:
                delay = (RATE_LIMIT_BACKOFF_SECONDS if is_rate_limit_error(last_error)
                         else 2 ** attempt)
                await asyncio.sleep(delay)

        # Exhausted. Surface rate limits explicitly so the route can return a 429 with a
        # clear message rather than a generic 500.
        if is_rate_limit_error(last_error):
            raise Exception(f"RATE_LIMIT: AI provider quota exceeded (429) after {MAX_RETRIES} attempts: {last_error}")
        raise Exception(f"Failed to generate questions after {MAX_RETRIES} attempts: {last_error}")

    async def _call_gemini(self, prompt: str) -> List[QuestionCreate]:
        """Make an API call to Gemini and parse the JSON questions out of it."""
        try:
            # Offload the blocking SDK call to a thread so a single Vertex
            # round-trip (5-30s) doesn't freeze the worker's event loop and
            # stall every other request on this worker.
            # Disable "thinking" and give the answer a generous budget. On
            # gemini-2.5-flash thinking tokens are spent OUT OF max_output_tokens, so
            # with thinking on, a batch of large coding questions gets truncated
            # mid-JSON -> _parse_questions returns [] -> "no questions". thinking_budget=0
            # frees the whole budget for the visible JSON.
            generated_text = await asyncio.to_thread(
                _generate_content,
                prompt, temperature=0.7, top_p=0.9, max_output_tokens=8000,
                thinking_budget=0,
            )
            return self._parse_questions(generated_text)
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            raise

    def _parse_questions(self, text: str) -> List[QuestionCreate]:
        """Parse generated text into question objects - handles JSON arrays in markdown blocks"""
        questions = []

        try:
            # Find JSON array anywhere in the text (handles ```json ... ``` wrapping)
            start_idx = text.find('[')
            end_idx = text.rfind(']') + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = text[start_idx:end_idx]
                question_data = json.loads(json_str)
                if isinstance(question_data, list):
                    for q in question_data:
                        if isinstance(q, dict) and q.get("question_text"):
                            q_type = q.get("question_type", "")
                            valid_types = ["multiple_choice", "short_answer", "coding", "behavioral", "technical", "situational", "case_study", "aptitude", "communication"]
                            if q_type not in valid_types:
                                # Infer type from content instead of defaulting to "technical"
                                if q.get("options") and len(q.get("options", [])) >= 2:
                                    q_type = "multiple_choice"
                                elif q.get("code_template"):
                                    q_type = "coding"
                                else:
                                    q_type = "short_answer"
                            diff = q.get("difficulty_level", "Medium")
                            if diff not in ["Easy", "Medium", "Hard"]:
                                diff = "Medium"
                            # Split any merged "A) .. B) .. C) .." blob the model
                            # returned into a proper 4-element options list.
                            raw_options = q.get("options")
                            norm_options = normalize_mcq_options(raw_options) if raw_options else None
                            created_q = QuestionCreate(
                                question_text=q["question_text"],
                                question_type=q_type,
                                difficulty_level=diff,
                                category=q.get("category"),
                                bloom_taxonomy_level=q.get("bloom_taxonomy_level"),
                                options=norm_options,
                                correct_answer=q.get("correct_answer"),
                                sample_answer=q.get("sample_answer"),
                                code_template=q.get("code_template"),
                                explanation=q.get("explanation"),
                                test_cases=q.get("test_cases"),
                                time_limit_minutes=q.get("time_limit_minutes", 5),
                                # MCQ = 1 mark; coding keeps a higher default weight.
                                points=q.get("points") or (20 if q_type == 'coding' else 1)
                            )
                            # Validate coding questions have proper content
                            if created_q.question_type == 'coding':
                                from src.judge.starter_sanitizer import sanitize_starter
                                # Strip any leaked solution from the starter (and supply a
                                # default stub when missing) so candidates never see the answer.
                                created_q.code_template = sanitize_starter(
                                    created_q.code_template,
                                    category=getattr(created_q, "category", None),
                                    sample_answer=created_q.sample_answer or created_q.correct_answer,
                                )
                                if created_q.options:
                                    created_q.options = None
                                    created_q.correct_answer = None
                                # Keep only well-formed test cases (input + expected_output).
                                tcs = created_q.test_cases if isinstance(created_q.test_cases, list) else []
                                created_q.test_cases = [
                                    {
                                        "input": str(t.get("input", "")),
                                        "expected_output": str(t.get("expected_output", "")),
                                        "is_sample": bool(t.get("is_sample", False)),
                                        "weight": int(t.get("weight", 1) or 1),
                                    }
                                    for t in tcs
                                    if isinstance(t, dict) and t.get("expected_output") is not None
                                ]
                            questions.append(created_q)
                if questions:
                    return questions
        except Exception as e:
            logger.warning(f"JSON parse attempt failed: {e}")

        # Fallback: try single JSON object
        try:
            start_idx = text.find('{')
            end_idx = text.rfind('}') + 1
            if start_idx != -1 and end_idx > start_idx:
                q = json.loads(text[start_idx:end_idx])
                if isinstance(q, dict) and q.get("question_text"):
                    questions.append(QuestionCreate(
                        question_text=q["question_text"],
                        question_type=q.get("question_type", "technical"),
                        difficulty_level=q.get("difficulty_level", "Medium"),
                        points=q.get("points") or (20 if q.get("question_type") == 'coding' else 1)
                    ))
                    return questions
        except Exception:
            pass

        logger.error(f"Could not parse questions from response: {text[:200]}")
        return questions

    async def regenerate_single_question(self, old_question_text: str, context: dict = None) -> QuestionCreate:
        """Regenerate a single question based on the old one"""
        ctx = context or {}
        job = ctx.get("job_position", "Software Engineer")
        q_type = ctx.get("question_type", "multiple_choice")
        difficulty = ctx.get("difficulty_level", "Medium")
        category = ctx.get("category", "General")

        # Build type-specific format example and instructions
        if q_type == "multiple_choice":
            format_example = f'''{{"question_text": "Your question here",
  "question_type": "multiple_choice",
  "difficulty_level": "{difficulty}",
  "category": "{category}",
  "options": ["Option A text", "Option B text", "Option C text", "Option D text"],
  "correct_answer": "A",
  "points": 10,
  "time_limit_minutes": 5}}'''
            type_instructions = "The question MUST include exactly 4 options with real text and a correct_answer letter (A/B/C/D)."
        elif q_type == "coding":
            format_example = f'''{{"question_text": "Given a list of integers, output the second largest UNIQUE value.\\n\\nInput: The first line contains N. The second line contains N space-separated integers.\\nOutput: Print the second largest unique integer (or the largest if only one unique value exists).\\n\\nExample:\\nInput:\\n5\\n4 1 4 5 2\\nOutput:\\n4",
  "question_type": "coding",
  "difficulty_level": "{difficulty}",
  "category": "{category}",
  "code_template": "import sys\\n\\ndef main():\\n    data = sys.stdin.read().split()\\n    # TODO: read input and print the answer\\n\\nmain()",
  "sample_answer": "import sys\\n\\ndef main():\\n    data = sys.stdin.read().split()\\n    n = int(data[0])\\n    nums = list(map(int, data[1:1+n]))\\n    u = sorted(set(nums), reverse=True)\\n    print(u[1] if len(u) > 1 else u[0])\\n\\nmain()",
  "test_cases": [
    {{"input": "5\\n4 1 4 5 2", "expected_output": "4", "is_sample": true}},
    {{"input": "3\\n7 7 7", "expected_output": "7", "is_sample": true}},
    {{"input": "4\\n10 20 30 40", "expected_output": "30", "is_sample": false}},
    {{"input": "6\\n-1 -2 -3 -1 -2 -5", "expected_output": "-2", "is_sample": false}}
  ],
  "points": 20,
  "time_limit_minutes": 15}}'''
            type_instructions = (
                "The question MUST be written for STDIN/STDOUT automated judging. Include: "
                "\"code_template\" (a runnable starter that READS FROM STDIN and PRINTS TO STDOUT — not a bare function stub), "
                "\"sample_answer\" (a COMPLETE stdin->stdout program that prints the correct answer), and "
                "\"test_cases\" (an array of 5-6 objects {\"input\",\"expected_output\",\"is_sample\"}; make the first 1-2 "
                "is_sample:true and AT LEAST 3 is_sample:false hidden edge cases; running sample_answer on each input MUST "
                "print exactly expected_output). The worked example under \"Example:\" must show only input data and expected "
                "output, never the solution code. Do NOT include options or correct_answer."
            )
        else:
            format_example = f'''{{"question_text": "Your question here",
  "question_type": "{q_type}",
  "difficulty_level": "{difficulty}",
  "category": "{category}",
  "sample_answer": "A detailed sample answer here",
  "points": 10,
  "time_limit_minutes": 5}}'''
            type_instructions = "The question MUST include a sample_answer. Do NOT include options."

        prompt = f"""Generate exactly 1 NEW and DIFFERENT {q_type} question for a {job} interview.
Difficulty: {difficulty}
Category: {category}
{type_instructions}

The previous question was: "{old_question_text}"
Generate a COMPLETELY DIFFERENT question on the same topic/category.

Return ONLY a JSON array with exactly 1 object:
[{format_example}]"""

        questions = await self._call_gemini(prompt)
        if questions and len(questions) > 0:
            return questions[0]
        raise Exception("Failed to generate replacement question")

    async def generate_text(self, prompt: str, *, temperature: float = 0.6,
                            num_predict: int = 1200,
                            thinking_budget: Optional[int] = None) -> str:
        """
        Generate free-form prose (e.g. a cover letter) from a prompt.

        Unlike generate_questions(), this returns the raw model text without any
        JSON parsing. Same health-check + retry/backoff convention as the rest of
        the service; raises if Gemini is unavailable after MAX_RETRIES.
        """
        if not await self.check_health():
            raise Exception(
                "Gemini service not available: either the google-genai package is "
                "not installed or no credentials are configured (GEMINI_API_KEY / "
                "GOOGLE_CLOUD_PROJECT) — see the preceding log line for which"
            )

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                # Offload the blocking SDK call to a thread (see _call_gemini)
                # so it doesn't block the event loop / other requests.
                text = await asyncio.to_thread(
                    _generate_content,
                    prompt, temperature=temperature, top_p=0.9, max_output_tokens=num_predict,
                    thinking_budget=thinking_budget,
                )
                if text:
                    return text
                raise Exception("Gemini returned an empty response")
            except Exception as e:
                last_error = e
                logger.warning(f"generate_text attempt {attempt + 1} failed: {e}")
                if attempt == MAX_RETRIES - 1:
                    raise Exception(f"Failed to generate text after {MAX_RETRIES} attempts: {e}")
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        raise Exception(f"Failed to generate text: {last_error}")


# Global instance
gemini_service = GeminiService()
