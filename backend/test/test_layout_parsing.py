"""
Unit tests for layout-aware parsing, expanded section headers, category anti-misclassification,
and soft skills separation in enhanced_ats_service.py and candidate_profile_routes.py.
"""
import pytest
from src.services.enhanced_ats_service import (
    detect_sections,
    enhanced_ats_service,
    SPOKEN_LANGUAGES_SET,
    SOFT_SKILLS_SET,
)

def test_expanded_section_headers():
    text = """
MY TECHNICAL SKILLS
Python
FastAPI
PostgreSQL

PROFESSIONAL JOURNEY
2020 - 2024 Senior Software Engineer at TechCorp

ACADEMIC BACKGROUND
B.Tech in Computer Science from IIT Delhi

MAJOR PROJECTS
- E-Commerce Platform built with React and Node.js

ONLINE CERTIFICATIONS
AWS Certified Solutions Architect
    """
    sections = detect_sections(text)
    assert "skills" in sections
    assert "experience" in sections
    assert "education" in sections
    assert "projects" in sections
    assert "certifications" in sections


def test_soft_skills_not_in_technical_skills():
    text = """
    SKILLS
    Python, Java, Communication, Teamwork, Problem Solving, React, SQL, Leadership
    """
    skills = enhanced_ats_service.extract_skills(text)
    
    # Technical skills must be extracted
    assert "Python" in skills or "python" in skills
    assert "React" in skills or "react" in skills
    
    # Soft skills MUST NOT be mapped/included in technical skills list
    assert "Communication" not in skills and "communication" not in skills
    assert "Teamwork" not in skills and "teamwork" not in skills
    assert "Leadership" not in skills and "leadership" not in skills
    assert "Problem Solving" not in skills and "problem solving" not in skills


def test_certifications_anti_misclassification():
    text = """
    CERTIFICATIONS & LICENSES
    AWS Certified Solutions Architect
    English
    Python
    B.Tech
    Communication
    Certified Kubernetes Application Developer (CKAD)
    """
    certs = enhanced_ats_service.extract_certifications(text)
    
    # Real certs must survive
    assert any("AWS" in c for c in certs)
    assert any("CKAD" in c or "Kubernetes" in c for c in certs)
    
    # Non-certs must be filtered out
    assert not any("English" == c for c in certs)
    assert not any("Python" == c for c in certs)
    assert not any("B.Tech" == c for c in certs)
    assert not any("Communication" == c for c in certs)


def test_languages_extracted_separately():
    text = """
    LANGUAGES
    English, Hindi, Spanish
    """
    languages = enhanced_ats_service.extract_languages(text)
    assert "English" in languages or "english" in [l.lower() for l in languages]
    assert "Hindi" in languages or "hindi" in [l.lower() for l in languages]
