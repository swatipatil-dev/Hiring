"""Tests for the Resume Matcher's profile parser.

The parser feeds the searchable resume pool (name / college / year of passing /
role), so the contract that matters most is: **never guess**. A missing field must
come back as None so HR is prompted to fill it in, rather than silently seeding the
pool with a wrong college or a date range mistaken for a phone number.
"""

import pytest

from src.services.resume_profile_parser import (
    parse_resume_profile,
    extract_full_name,
    extract_college,
    extract_year_of_passing,
    extract_highest_degree,
    extract_phone,
    extract_job_role,
)


INDIAN_FRESHER = """PRIYA SHARMA
priya.sharma@gmail.com | +91 98765 43210 | Bengaluru

OBJECTIVE
Seeking a Backend Developer role in a product company.

EDUCATION
Indian Institute of Technology Bombay
B.Tech in Computer Science, 2021
CGPA 8.7

EXPERIENCE
Software Engineer, Infosys (2021 - Present)
Built REST APIs in Python and Django.
"""

US_STYLE = """John A. Smith
john.smith@example.com
(415) 555-0132

EDUCATION
University of California, Berkeley
Bachelor of Science, Computer Science, May 2019

WORK EXPERIENCE
Senior Data Analyst at Acme Corp, 2019-2024
"""

CV_HEADER = """Curriculum Vitae

Rahul Verma
Pune Institute of Computer Technology
M.Tech Data Science  2023
Contact: 9876501234
"""

UNSTRUCTURED = "random notes, worked on stuff, no header at all"


class TestFullName:
    def test_all_caps_header_is_title_cased(self):
        assert extract_full_name(INDIAN_FRESHER) == "Priya Sharma"

    def test_middle_initial_is_kept(self):
        assert extract_full_name(US_STYLE) == "John A. Smith"

    def test_cv_banner_is_skipped(self):
        # "Curriculum Vitae" sits above the real name and must not be taken as one.
        assert extract_full_name(CV_HEADER) == "Rahul Verma"

    def test_no_name_returns_none(self):
        assert extract_full_name(UNSTRUCTURED) is None

    @pytest.mark.parametrize("line", [
        "EDUCATION",
        "john.smith@example.com",
        "Software Engineer",       # job title, not a name
        "Bengaluru 560001",        # contains digits
    ])
    def test_non_name_lines_rejected(self, line):
        assert extract_full_name(line) is None


class TestCollege:
    def test_multiword_institute(self):
        assert extract_college(INDIAN_FRESHER) == "Indian Institute of Technology Bombay"

    def test_university_of_pattern(self):
        assert "University of California" in extract_college(US_STYLE)

    def test_institute_without_education_header(self):
        assert extract_college(CV_HEADER) == "Pune Institute of Computer Technology"

    def test_absent_college_returns_none(self):
        assert extract_college(UNSTRUCTURED) is None


class TestYearOfPassing:
    def test_year_next_to_college(self):
        assert extract_year_of_passing(INDIAN_FRESHER, "Indian Institute of Technology Bombay") == 2021

    def test_graduation_year_not_employment_year(self):
        # 2024 appears in the work section; the education year must win.
        assert extract_year_of_passing(US_STYLE, "University of California") == 2019

    def test_absurd_future_year_rejected(self):
        assert extract_year_of_passing("Graduated from Anna University in 2099", "Anna University") is None

    def test_no_year_returns_none(self):
        assert extract_year_of_passing(UNSTRUCTURED, None) is None


class TestHighestDegree:
    def test_btech_detected(self):
        assert extract_highest_degree(INDIAN_FRESHER) == "B.Tech"

    def test_spelled_out_bachelor_of_science(self):
        assert extract_highest_degree(US_STYLE) == "B.Sc"

    def test_highest_wins_when_multiple_present(self):
        text = "B.Tech Computer Science 2019, M.Tech Data Science 2021"
        assert extract_highest_degree(text) == "M.Tech"

    def test_no_degree_returns_none(self):
        assert extract_highest_degree(UNSTRUCTURED) is None


class TestPhone:
    def test_spaced_indian_format_not_truncated(self):
        # The shared ATS regex stops at "+91 98765"; this must keep all digits.
        assert extract_phone(INDIAN_FRESHER) == "+91 98765 43210"

    def test_us_parenthesised_format(self):
        assert extract_phone(US_STYLE) == "(415) 555-0132"

    @pytest.mark.parametrize("text", ["2019-2024", "Worked 2019 – 2024 at Acme"])
    def test_year_range_is_not_a_phone(self, text):
        assert extract_phone(text) is None

    def test_no_phone_returns_none(self):
        assert extract_phone(UNSTRUCTURED) is None


class TestJobRole:
    def test_objective_line_strips_seeking_prefix(self):
        assert extract_job_role(INDIAN_FRESHER) == "Backend Developer"

    def test_title_before_company_separator(self):
        assert extract_job_role(US_STYLE) == "Senior Data Analyst"

    def test_worked_as_prefix_stripped(self):
        assert extract_job_role("Meera Iyer\nWorked as QA Tester at Zoho") == "QA Tester"

    def test_no_role_returns_none(self):
        assert extract_job_role(UNSTRUCTURED) is None


class TestParseResumeProfile:
    EXPECTED_KEYS = {
        "full_name", "email", "phone", "college",
        "year_of_passing", "job_role", "highest_degree",
    }

    def test_full_profile(self):
        p = parse_resume_profile(INDIAN_FRESHER)
        assert p["full_name"] == "Priya Sharma"
        assert p["email"] == "priya.sharma@gmail.com"
        assert p["phone"] == "+91 98765 43210"
        assert p["college"] == "Indian Institute of Technology Bombay"
        assert p["year_of_passing"] == 2021
        assert p["highest_degree"] == "B.Tech"
        assert p["job_role"] == "Backend Developer"

    @pytest.mark.parametrize("text", ["", "   ", "\n\n"])
    def test_empty_text_returns_all_none(self, text):
        p = parse_resume_profile(text)
        assert set(p) == self.EXPECTED_KEYS
        assert all(v is None for v in p.values())

    def test_unstructured_text_returns_all_none(self):
        # Better a blank HR fills in than a wrong value polluting search.
        p = parse_resume_profile(UNSTRUCTURED)
        assert all(v is None for v in p.values())

    def test_missing_email_does_not_blank_other_fields(self):
        p = parse_resume_profile(CV_HEADER)
        assert p["email"] is None
        assert p["full_name"] == "Rahul Verma"
        assert p["college"] == "Pune Institute of Computer Technology"
        assert p["year_of_passing"] == 2023

    def test_keys_are_always_present(self):
        for text in (INDIAN_FRESHER, US_STYLE, CV_HEADER, UNSTRUCTURED, ""):
            assert set(parse_resume_profile(text)) == self.EXPECTED_KEYS
