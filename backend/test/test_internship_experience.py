"""
Unit tests for the 0 to 8 month internship exclusion rule and work experience calculation.
"""
import pytest
from src.services.enhanced_ats_service import enhanced_ats_service

def test_internship_under_8_months_excluded():
    entries = [
        {
            "job_title": "Software Engineer Intern",
            "company": "TechCorp",
            "employment_type": "Internship",
            "duration_months": 6,
        }
    ]
    exp_years = enhanced_ats_service.calculate_work_experience_years_from_entries(entries)
    # A 6-month internship MUST be excluded from work experience
    assert exp_years == 0.0


def test_full_time_job_and_short_internship():
    entries = [
        {
            "job_title": "Senior Software Engineer",
            "company": "Innomax",
            "employment_type": "Full-time",
            "duration_months": 36,  # 3 years
        },
        {
            "job_title": "Developer Intern",
            "company": "Acme Inc",
            "employment_type": "Internship",
            "duration_months": 5,   # 5 months internship - EXCLUDED
        }
    ]
    exp_years = enhanced_ats_service.calculate_work_experience_years_from_entries(entries)
    # Only the 36-month full time job should count (3.0 years)
    assert exp_years == 3.0


def test_long_internship_over_8_months_included():
    entries = [
        {
            "job_title": "Graduate Intern",
            "company": "Global Tech",
            "employment_type": "Internship",
            "duration_months": 12,  # 1 year internship - INCLUDED (> 8 months)
        }
    ]
    exp_years = enhanced_ats_service.calculate_work_experience_years_from_entries(entries)
    assert exp_years == 1.0
