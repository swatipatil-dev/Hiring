"""
Hybrid ATS Scoring Service
--------------------------
Step 1 — Rule-based engine (EnhancedATSScoringService) produces the quantitative score.
Step 2 — Gemini LLM enriches with qualitative analysis (summary, recommendation,
          inferred skills, interview focus areas, red flags).

If Gemini is unavailable the quantitative score is returned as-is with llm_available=False.
"""

import re
import json
import logging
from typing import List, Dict, Any

from pydantic import BaseModel
from src.services.enhanced_ats_service import (
    EnhancedATSScoringService,
    EnhancedATSScoreResult,
)
from src.services.gemini_service import gemini_service, GEMINI_MODEL

logger = logging.getLogger(__name__)


# ── Extended result model ─────────────────────────────────────────────────────

class HybridATSResult(BaseModel):
    """All quantitative fields from EnhancedATSScoreResult plus LLM qualitative analysis."""

    # Quantitative (from rule-based engine)
    ats_score: float
    skills_match_score: float
    semantic_score: float
    experience_score: float
    keyword_score: float
    education_score: float
    certification_score: float
    project_score: float
    matched_skills: List[str]
    missing_skills: List[str]
    cluster_matched_skills: List[Dict[str, str]]
    extracted_skills: List[str]
    extracted_experience: float
    education_level: str
    certifications: List[str]
    confidence_score: float
    scoring_breakdown: Dict[str, Any]
    recommendations: List[str]
    strengths: List[str]
    weaknesses: List[str]

    # Qualitative (from Gemini LLM)
    llm_summary: str = ""
    llm_recommendation: str = ""   # "Strong Hire" | "Phone Screen" | "Reject"
    inferred_skills: List[str] = []
    interview_focus: List[str] = []
    red_flags: List[str] = []
    llm_available: bool = False


# ── Service ───────────────────────────────────────────────────────────────────

class LLMATSService:
    """Hybrid ATS scoring: rule-based engine + Gemini qualitative layer."""

    def __init__(
        self,
        model: str = GEMINI_MODEL,
    ):
        self.model = model
        self._engine = EnhancedATSScoringService()

    # ── Gemini helpers ────────────────────────────────────────────────────────

    async def _check_llm(self) -> bool:
        """Return True if the Gemini API is configured and reachable."""
        return await gemini_service.check_health()

    def _build_prompt(
        self,
        resume_text: str,
        job_description: str,
        score: EnhancedATSScoreResult,
        required_experience: int,
    ) -> str:
        resume_snippet = resume_text[:2500].strip()
        jd_snippet = job_description[:1000].strip()

        cluster_lines = ""
        if score.cluster_matched_skills:
            lines = [
                f"  - Required '{m['required']}' → candidate has '{m['found']}'"
                f" (same {m['cluster'].replace('_', ' ')} category)"
                for m in score.cluster_matched_skills
            ]
            cluster_lines = "Similar-but-not-exact skills:\n" + "\n".join(lines) + "\n"

        return f"""You are an expert recruiter. Analyze this candidate and return ONLY a valid JSON object. No markdown, no explanation, no text outside the JSON.

SCORE BREAKDOWN:
- ATS Score: {score.ats_score}/100
- Skills Match: {score.skills_match_score}/100
- Semantic: {score.semantic_score}/100
- Experience: {score.experience_score}/100 (candidate: {score.extracted_experience} yrs, required: {required_experience} yrs)
- Education: {score.education_score}/100 ({score.education_level})
- Certifications: {score.certification_score}/100

MATCHED SKILLS: {', '.join(score.matched_skills) if score.matched_skills else 'None'}
MISSING SKILLS: {', '.join(score.missing_skills) if score.missing_skills else 'None'}
{cluster_lines}
RESUME:
{resume_snippet}

JOB DESCRIPTION:
{jd_snippet}

INSTRUCTIONS:
- "recommendation" MUST be exactly one of these three strings: "Strong Hire", "Phone Screen", "Reject"
  - Strong Hire: ATS >= 65 AND candidate clearly meets core requirements
  - Phone Screen: ATS 40-64 OR candidate meets some requirements but has gaps
  - Reject: ATS < 40 OR candidate is clearly unqualified or in a completely different field
- If candidate is overqualified, use "Phone Screen" not "Reject"

Return ONLY this JSON:
{{
  "summary": "2-3 sentence honest assessment of this candidate for this role",
  "recommendation": "Strong Hire",
  "inferred_skills": ["skills evident from descriptions not listed explicitly, max 5"],
  "interview_focus": ["3-4 specific topics worth probing in interview"],
  "red_flags": ["genuine concerns only, empty array if none"]
}}"""

    # Valid recommendation values — anything else gets mapped to the closest one
    _VALID_RECS = {"Strong Hire", "Phone Screen", "Reject"}
    _REC_MAP = {
        "hire": "Strong Hire", "yes": "Strong Hire", "recommended": "Strong Hire",
        "consider": "Phone Screen", "maybe": "Phone Screen", "review": "Phone Screen",
        "borderline": "Phone Screen", "conditional": "Phone Screen",
        "no": "Reject", "not recommended": "Reject", "do not hire": "Reject",
        "unsuitable": "Reject", "pass": "Reject",
    }

    @classmethod
    def _normalise_recommendation(cls, raw: str, ats_score: float) -> str:
        """Map LLM recommendation to exactly one of three valid values."""
        if raw in cls._VALID_RECS:
            return raw
        lower = raw.lower().strip()
        for key, mapped in cls._REC_MAP.items():
            if key in lower:
                return mapped
        # Final fallback: decide by score
        if ats_score >= 65:
            return "Strong Hire"
        elif ats_score >= 40:
            return "Phone Screen"
        return "Reject"

    @staticmethod
    def _parse_llm_response(text: str) -> Dict[str, Any]:
        """Extract JSON from LLM output, handles markdown fences and stray text."""
        text = re.sub(r"```(?:json)?", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    # ── Main entry point ──────────────────────────────────────────────────────

    async def hybrid_score_candidate(
        self,
        resume_text: str,
        job_description: str,
        required_skills: List[str],
        required_experience: int,
        required_education: str = "bachelor",
    ) -> HybridATSResult:
        """
        Score a candidate using the hybrid engine.
        Always returns a valid result — LLM fields are empty if Ollama is unavailable.
        """

        # ── Step 1: Quantitative scoring (always runs) ────────────────────────
        score: EnhancedATSScoreResult = self._engine.enhanced_score_candidate(
            resume_text=resume_text,
            job_description=job_description,
            required_skills=required_skills,
            required_experience=required_experience,
            required_education=required_education,
        )

        result = HybridATSResult(**score.model_dump(), llm_available=False)

        # ── Step 2: LLM qualitative enrichment (optional) ─────────────────────
        if not await self._check_llm():
            logger.warning(
                "Gemini API not available — returning rule-based score only."
            )
            return result

        try:
            prompt = self._build_prompt(resume_text, job_description, score, required_experience)

            # low temp = consistent, factual. thinking_budget=0 stops gemini-2.5-flash
            # from spending the token budget on internal thinking and truncating the
            # JSON (which silently drops us to the rule-based score).
            raw = await gemini_service.generate_text(
                prompt, temperature=0.3, num_predict=1200, thinking_budget=0
            )

            llm_data = self._parse_llm_response(raw)
            if llm_data:
                result.llm_summary = llm_data.get("summary", "")
                raw_rec = llm_data.get("recommendation", "")
                result.llm_recommendation = self._normalise_recommendation(raw_rec, score.ats_score)
                result.inferred_skills = llm_data.get("inferred_skills", [])[:5]
                result.interview_focus = llm_data.get("interview_focus", [])[:4]
                result.red_flags = llm_data.get("red_flags", [])
                result.llm_available = True
            else:
                logger.warning("LLM returned unparseable response — using rule-based result only.")

        except Exception as e:
            logger.warning(f"LLM enrichment failed: {e} — using rule-based result only.")

        return result
