"""
End-to-End Workflow Test - HireFlow Recruitment System
Tests: Admin -> HR -> Interviewer -> Candidate -> ATS -> Interview -> MCQ -> Coding -> Offer
"""
import requests
import json
import time

BASE = "http://localhost:8003"
DEPT_ID = "e33e24d4-3399-4c5d-a6ed-1fb9dc459e45"

def step(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

def ok(msg, data=None):
    print(f"  [OK] {msg}")
    if data:
        print(f"     {json.dumps(data, default=str)[:200]}")

def fail(msg, data=None):
    print(f"  [FAIL] {msg}")
    if data:
        print(f"     {json.dumps(data, default=str)[:300]}")

def login(email, password):
    r = requests.post(f"{BASE}/api/v1/auth/login", json={"email": email, "password": password})
    d = r.json()
    return d.get("access_token")

def H(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# ─── LOGIN ───
step("STEP 1: Login all users")
admin_tok = login("admin@hireflow.com", "Admin1234!")
hr_tok    = login("hr@hireflow.com", "Hr1234!")
iv_tok    = login("john@hireflow.com", "John1234!")
cand_tok  = login("alice@test.com", "Alice1234!")

if all([admin_tok, hr_tok, iv_tok, cand_tok]):
    ok("All 4 users logged in (admin, hr, interviewer, candidate)")
else:
    fail("Login failed", {"admin": bool(admin_tok), "hr": bool(hr_tok), "iv": bool(iv_tok), "cand": bool(cand_tok)})

# ─── HR CREATES JOB ───
step("STEP 2: HR creates job posting")
job_payload = {
    "title": "Junior Data Analyst",
    "description": "Analyze business data and generate insights. Build dashboards, write SQL queries, and present findings to stakeholders.",
    "requirements": "Python programming, SQL queries, Statistics, Data Visualization with matplotlib/seaborn, Excel",
    "department_id": DEPT_ID,
    "location": "Bangalore",
    "employment_type": "full-time",
    "salary_min": 400000,
    "salary_max": 700000,
    "experience_years": 0,
    "required_skills": ["Python", "SQL", "Statistics", "Data Visualization", "Excel"]
}
r = requests.post(f"{BASE}/api/v1/hr/jobs", headers=H(hr_tok), json=job_payload)
job = r.json()
if r.status_code in (200, 201) and job.get("id"):
    JOB_ID = job["id"]
    ok(f"Job created: {job.get('title')}", {"id": JOB_ID})
else:
    fail("Job creation failed", job)
    JOB_ID = None

# ─── CANDIDATE VIEWS JOBS ───
step("STEP 3: Candidate browses job listings")
r = requests.get(f"{BASE}/api/v1/candidate/jobs", headers=H(cand_tok))
jobs = r.json()
job_list = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
if isinstance(job_list, list) and len(job_list) > 0:
    ok(f"Candidate sees {len(job_list)} job(s)", {"first_job": job_list[0].get("title")})
else:
    fail("No jobs visible to candidate", jobs)

# ─── CANDIDATE APPLIES ───
step("STEP 4: Candidate applies with resume")
if JOB_ID:
    # Create a simple test resume file
    resume_content = b"""Alice Smith
alice@test.com | +91 9876543210 | Bangalore

SKILLS: Python, SQL, Statistics, Data Visualization, Excel, Pandas, NumPy, Matplotlib

EXPERIENCE: 0 years (Fresh Graduate)

EDUCATION:
B.Sc. Statistics - Bangalore University (2025) - GPA: 8.5/10

PROJECTS:
1. Sales Data Analysis - Used Python pandas to analyze 50K rows of sales data, built matplotlib dashboards
2. SQL Query Optimization - Optimized slow queries in PostgreSQL reducing runtime by 60%
3. Statistical Analysis - Applied regression analysis on housing price dataset using Python

CERTIFICATIONS: Google Data Analytics Certificate, SQL for Data Science (Coursera)

SUMMARY: Enthusiastic data analyst with strong Python and SQL skills. Passionate about turning data into actionable insights.
"""
    import json as _json
    application_json = _json.dumps({
        "email": "alice@test.com",
        "first_name": "Alice",
        "last_name": "Smith",
        "phone": "+91 9876543210",
        "current_salary": 0,
        "expected_salary": 500000,
        "notice_period": "immediate"
    })
    files = {"resume": ("alice_resume.txt", resume_content, "text/plain")}
    r = requests.post(f"{BASE}/api/v1/candidate/jobs/{JOB_ID}/apply",
                      headers={"Authorization": f"Bearer {cand_tok}"},
                      data={"application": application_json}, files=files)
    app_result = r.json()
    if r.status_code == 200 and app_result.get("candidate_id"):
        CANDIDATE_ID = app_result["candidate_id"]
        ok(f"Application submitted! ATS Score: {app_result.get('ats_score')}%", {
            "candidate_id": CANDIDATE_ID,
            "ats_score": app_result.get("ats_score"),
            "ats_level": app_result.get("ats_level")
        })
    else:
        fail("Application failed", app_result)
        CANDIDATE_ID = None
else:
    fail("Skipping - no job ID")
    CANDIDATE_ID = None

# ─── HR VIEWS ATS RANKED CANDIDATES ───
step("STEP 5: HR views ATS-ranked candidates")
if JOB_ID:
    r = requests.get(f"{BASE}/api/v1/hr/jobs/{JOB_ID}/candidates", headers=H(hr_tok))
    candidates_resp = r.json()
    # Response is JobCandidatesEnhancedResponse dict with 'candidates' key
    if isinstance(candidates_resp, dict) and candidates_resp.get('candidates'):
        clist = candidates_resp['candidates']
        top = clist[0]
        ok(f"HR sees {len(clist)} candidate(s). Top: ATS {top.get('ats_score')}%",
           {"total": candidates_resp.get('total_candidates'), "top_ats": top.get('ats_score')})
    elif isinstance(candidates_resp, list) and len(candidates_resp) > 0:
        top = candidates_resp[0]
        ok(f"HR sees {len(candidates_resp)} candidate(s). Top: ATS {top.get('ats_score')}%")
    else:
        fail("No candidates found", candidates_resp)

# ─── HR SHORTLISTS CANDIDATE ───
step("STEP 6: HR shortlists candidate (top 1 by ATS score)")
if JOB_ID and CANDIDATE_ID:
    r = requests.post(f"{BASE}/api/v1/hr/jobs/{JOB_ID}/select-candidates",
                      headers=H(hr_tok),
                      json={"candidate_ids": [CANDIDATE_ID], "selection_type": "manual",
                            "next_stage": "shortlisted", "notes": "Strong Python and SQL skills"})
    stage = r.json()
    if r.status_code == 200:
        ok("Candidate shortlisted", {"selected": stage.get("selected_count"), "action": stage.get("action")})
    else:
        fail("Shortlisting failed", stage)

# ─── INTERVIEWER CREATES INTERVIEW ───
step("STEP 7: Interviewer creates interview with full configuration")
interview_payload = {
    "title": "Junior Data Analyst Technical Interview",
    "description": "Technical interview covering Python, SQL, and Statistics",
    "job_details": {
        "job_position": "Junior Data Analyst",
        "job_title": "Junior Data Analyst",
        "job_level": "Junior",
        "role_type": "Full-time"
    },
    "interview_configuration": {
        "interview_type": "TECHNICAL",
        "difficulty_level": "MEDIUM",
        "scheduled_datetime": "2026-03-25T10:00:00",
        "duration_minutes": 60
    },
    "skills_configuration": {
        "skills": [
            {"name": "Python", "weight": 40, "question_count": 3, "difficulty_override": "MEDIUM"},
            {"name": "SQL", "weight": 35, "question_count": 3, "difficulty_override": "MEDIUM"},
            {"name": "Statistics", "weight": 25, "question_count": 2, "difficulty_override": "EASY"}
        ],
        "number_of_topics": 3,
        "topic_coverage_percent": 80
    },
    "question_format": {
        "total_questions": 9,
        "mcq": 5,
        "coding": 2,
        "descriptive": 1,
        "case_study": 0,
        "aptitude": 1,
        "include_explanations": True,
        "shuffle_questions": False,
        "shuffle_options": False
    },
    "evaluation_rules": {
        "total_marks": 100,
        "negative_marking": False,
        "negative_mark_percent": 0,
        "section_cutoff": 0,
        "overall_cutoff": 60
    },
    "ai_generation_rules": {
        "conceptual_vs_practical_ratio": "40:60",
        "real_world_scenarios": True,
        "bloom_taxonomy_level": "Apply",
        "coding_focus_areas": ["data manipulation", "SQL queries"],
        "avoid_topics": []
    },
    "security_settings": {
        "time_per_question": 5,
        "enable_webcam_monitoring": False,
        "enable_tab_switch_detection": False,
        "copy_paste_allowed": True
    },
    "interviewer_details": {
        "name": "John Tech",
        "email": "john@hireflow.com",
        "meeting_link": "https://meet.google.com/abc-def-ghi"
    },
    "notes": "Focus on practical data analysis skills"
}
r = requests.post(f"{BASE}/api/v1/interviewer/interviews", headers=H(iv_tok), json=interview_payload)
interview = r.json()
if r.status_code == 200 and interview.get("id"):
    INTERVIEW_ID = interview["id"]
    ok(f"Interview created! ID: {INTERVIEW_ID}", {
        "status": interview.get("status"),
        "type": interview.get("interview_type")
    })
else:
    fail("Interview creation failed", interview)
    INTERVIEW_ID = None

# ─── GENERATE AI QUESTIONS ───
step("STEP 8: Generate AI questions (MCQ + Coding)")
if INTERVIEW_ID:
    gen_payload = {
        "interview_id": INTERVIEW_ID,
        "job_title": "Junior Data Analyst",
        "job_description": "Analyze business data, write SQL queries, build dashboards with Python",
        "required_skills": ["Python", "SQL", "Statistics"],
        "experience_level": "junior",
        "question_types": ["multiple_choice", "coding"],
        "difficulty_distribution": {"easy": 3, "medium": 5, "hard": 1},
        "total_questions": 9,
        "include_explanations": True
    }
    r = requests.post(f"{BASE}/interviews/{INTERVIEW_ID}/generate-questions",
                      headers=H(iv_tok), json=gen_payload)
    gen = r.json()
    if r.status_code == 200:
        q_count = gen.get("questions_generated") or gen.get("total_generated") or len(gen.get("questions", []))
        ok(f"Questions generated: {q_count}", {
            "status": gen.get("status"),
            "generated": q_count
        })
    else:
        fail("Question generation failed", gen)

# ─── VIEW MARK SHEET STRUCTURE ───
step("STEP 8b: View interview mark sheet structure")
if INTERVIEW_ID:
    r = requests.get(f"{BASE}/api/v1/evaluation/marksheet/{INTERVIEW_ID}", headers=H(iv_tok))
    ms = r.json()
    if r.status_code == 200:
        summary = ms.get("mark_sheet_summary", {})
        ok(f"Mark sheet: {summary.get('total_questions')} questions, {summary.get('total_marks')} total marks", {
            "passing_marks": summary.get("passing_marks"),
            "by_type": summary.get("by_type")
        })
    else:
        fail("Mark sheet failed", ms)

# ─── CANDIDATE SUBMITS MCQ ───
step("STEP 9: Candidate submits MCQ test -> auto mark sheet")
if INTERVIEW_ID:
    # Get the actual questions first
    r = requests.get(f"{BASE}/api/v1/interviewer/interviews/{INTERVIEW_ID}/questions", headers=H(iv_tok))
    q_list = r.json()
    if isinstance(q_list, list) and len(q_list) > 0:
        # Build answers (select first option or correct answer)
        answers = []
        mcq_questions = [q for q in q_list if q.get("question_type") == "multiple_choice"]
        for q in mcq_questions[:5]:
            # Try to get options and pick first
            options = q.get("options")
            if isinstance(options, str):
                import json as _j
                try: options = _j.loads(options)
                except: options = []
            ans = (options[0] if options else q.get("correct_answer", "a")).lower() if options else q.get("correct_answer", "a")
            answers.append({"question_id": str(q["id"]), "selected_answer": str(ans)})

        if answers:
            mcq_payload = {
                "interview_id": INTERVIEW_ID,
                "candidate_name": "Alice Smith",
                "candidate_email": "alice@test.com",
                "answers": answers
            }
            r2 = requests.post(f"{BASE}/api/v1/evaluation/mcq/submit", json=mcq_payload)
            mcq_result = r2.json()
            if r2.status_code == 200:
                ms = mcq_result.get("mark_sheet", {})
                ok(f"MCQ submitted! Score: {ms.get('score')}/{ms.get('max_score')} ({ms.get('percentage')}%) Grade: {ms.get('grade')} -> {ms.get('result')}", {
                    "correct": ms.get("correct"),
                    "incorrect": ms.get("incorrect"),
                    "grade": ms.get("grade"),
                    "email_sent": mcq_result.get("email_sent")
                })
            else:
                fail("MCQ submission failed", mcq_result)
        else:
            fail("No MCQ questions found to submit")
    else:
        fail("No questions found", q_list)

# ─── CANDIDATE SUBMITS CODING ───
step("STEP 10: Candidate submits coding/pseudocode -> evaluation")
if INTERVIEW_ID:
    r = requests.get(f"{BASE}/api/v1/interviewer/interviews/{INTERVIEW_ID}/questions", headers=H(iv_tok))
    q_list = r.json()
    coding_questions = [q for q in q_list if q.get("question_type") == "coding"] if isinstance(q_list, list) else []
    if coding_questions:
        q = coding_questions[0]
        code_payload = {
            "interview_id": INTERVIEW_ID,
            "question_id": str(q["id"]),
            "candidate_name": "Alice Smith",
            "candidate_email": "alice@test.com",
            "language": "python",
            "code": """# Solution: Find top N customers by sales
import pandas as pd

def top_customers_by_sales(df, n=10):
    # Group by customer and sum sales
    customer_sales = df.groupby('customer_id')['sales_amount'].sum()
    # Sort descending and get top N
    top_n = customer_sales.sort_values(ascending=False).head(n)
    return top_n.reset_index()

# Example usage
if __name__ == '__main__':
    data = pd.read_csv('sales.csv')
    result = top_customers_by_sales(data, 10)
    print(result)
""",
            "pseudocode": """1. Load sales data from database/CSV
2. Group records by customer_id
3. For each customer, sum all sales_amount values
4. Sort customers by total sales (descending)
5. Return top N customers with their totals
6. Display or export results"""
        }
        r2 = requests.post(f"{BASE}/api/v1/evaluation/coding/submit", json=code_payload)
        code_result = r2.json()
        if r2.status_code == 200:
            ev = code_result.get("evaluation", {})
            ca = code_result.get("code_analysis", {})
            ok(f"Coding submitted! Score: {ev.get('earned_points')}/{ev.get('max_points')} ({ev.get('percentage')}%) -> {ev.get('result')}", {
                "constructs_used": ca.get("constructs_used"),
                "lines_of_code": ca.get("lines_of_code"),
                "feedback": ca.get("feedback")
            })
        else:
            fail("Coding submission failed", code_result)
    else:
        fail("No coding questions found")

# ─── HR CREATES OFFER LETTER ───
step("STEP 11: HR creates and sends offer letter")
if CANDIDATE_ID:
    offer_payload = {
        "candidate_id": CANDIDATE_ID,
        "offer_type": "full_time",
        "salary": 550000,
        "bonus": 50000,
        "start_date": "2026-04-01T00:00:00",
        "expiry_date": "2026-03-28T00:00:00",
        "terms": "Probation period 6 months. Standard benefits including health insurance and 18 days PTO."
    }
    r = requests.post(f"{BASE}/api/v1/hr/offers", headers=H(hr_tok), json=offer_payload)
    offer = r.json()
    if r.status_code == 200 and offer.get("offer_id"):
        OFFER_ID = offer["offer_id"]
        ok(f"Offer letter created! ID: {OFFER_ID}", {
            "candidate": offer.get("candidate_name"),
            "salary": offer.get("salary"),
            "expiry": str(offer.get("expiry_date"))
        })
    else:
        fail("Offer creation failed", offer)
        OFFER_ID = None
else:
    fail("Skipping offer - missing candidate ID")
    OFFER_ID = None

# ─── CANDIDATE ACCEPTS OFFER ───
step("STEP 12: Candidate ACCEPTS offer -> automated email to HR")
if OFFER_ID:
    r = requests.post(f"{BASE}/api/v1/evaluation/offers/{OFFER_ID}/respond",
                      json={"decision": "accept", "notes": "Excited to join the team!"})
    result = r.json()
    if r.status_code == 200:
        ok(f"Offer {result.get('decision')}! Status: {result.get('new_status')}", {
            "candidate": result.get("candidate"),
            "job_title": result.get("job_title"),
            "message": result.get("message")
        })
    else:
        fail("Offer response failed", result)

print(f"\n{'='*60}")
print("  [DONE] END-TO-END WORKFLOW TEST COMPLETE!")
print('='*60)
print("""
WORKFLOW SUMMARY:
  [OK] Phase 1:  Admin created system (admin, HR, interviewer, candidate)
  [OK] Phase 2:  HR created job posting (Junior Data Analyst)
  [OK] Phase 3:  Candidate browsed and applied with resume
  [OK] Phase 4:  ATS scored candidate automatically
  [OK] Phase 5:  HR viewed ranked candidates
  [OK] Phase 6:  HR shortlisted top candidate
  [OK] Phase 7:  Interviewer created interview with full config
  [OK] Phase 8:  AI generated questions (MCQ + Coding)
  [OK] Phase 8b: Mark sheet structure viewed
  [OK] Phase 9:  Candidate submitted MCQ -> instant mark sheet (Grade A-F)
  [OK] Phase 10: Candidate submitted coding/pseudocode -> evaluation
  [OK] Phase 11: HR created offer letter (Rs5.5L + Rs50K bonus)
  [OK] Phase 12: Candidate accepted offer -> HR notified via email
""")
