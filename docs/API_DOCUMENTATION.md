# 🚀 HIREFLOW RECRUITMENT SYSTEM - COMPLETE API DOCUMENTATION

## 📋 Table of Contents
1. [System Overview](#system-overview)
2. [Authentication](#authentication)
3. [API Endpoints by Role](#api-endpoints-by-role)
4. [12-Phase Workflow](#12-phase-workflow)
5. [AI/ML ATS Scoring](#aiml-ats-scoring)
6. [Error Handling](#error-handling)
7. [Rate Limiting](#rate-limiting)
8. [Examples & Workflows](#examples--workflows)

---

## 🎯 System Overview

**HireFlow** is a comprehensive AI-powered recruitment system with:
- **4 User Roles**: Admin, HR, Interviewer, Candidate
- **12-Phase Workflow**: From job creation to hiring
- **AI/ML ATS Scoring**: 4-factor resume ranking algorithm
- **Real-time Processing**: Instant candidate ranking
- **Complete Audit Trail**: Every decision logged

### Base URL
```
Development: http://localhost:8000
Production: https://api.yourcompany.com
```

### API Version
```
/api/v1/
```

---

## 🔐 Authentication

### JWT Token-Based Authentication
All API endpoints (except public ones) require a valid JWT token in the Authorization header:

```bash
Authorization: Bearer <your-jwt-token>
```

### Token Expiration
- **Default**: 24 hours
- **Refresh**: Login again for new token

---

## 📚 API Endpoints by Role

## 🔓 Public Endpoints

### Health Check
```http
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-03-05T12:00:00Z"
}
```

### API Information
```http
GET /
```

**Response:**
```json
{
  "message": "HireFlow Recruitment System API",
  "version": "1.0.0",
  "docs": "/docs",
  "health": "/health"
}
```

---

## 👤 Authentication Endpoints

### Register User
```http
POST /api/v1/auth/register
```

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "securePassword123",
  "role": "candidate",  // admin, hr, interviewer, candidate
  "first_name": "John",
  "last_name": "Doe",
  "phone": "+1-555-0123"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "role": "candidate",
    "first_name": "John",
    "last_name": "Doe",
    "phone": "+1-555-0123",
    "is_active": true,
    "created_at": "2024-03-05T12:00:00Z"
  }
}
```

### Login
```http
POST /api/v1/auth/login
```

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "securePassword123"
}
```

**Response:** Same as register

### Logout
```http
POST /api/v1/auth/logout
Authorization: Bearer <token>
```

**Response:**
```json
{
  "message": "Successfully logged out"
}
```

---

## 🎓 Candidate Endpoints

### Browse Jobs
```http
GET /api/v1/candidate/jobs
```

**Query Parameters:**
- `skip` (int, default: 0): Pagination offset
- `limit` (int, default: 50): Max records per page
- `search` (string, optional): Search job titles

**Response:**
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440001",
    "title": "Senior Python Developer",
    "description": "We are looking for a Senior Python Developer...",
    "requirements": "5+ years of Python development experience...",
    "department": "Engineering",
    "location": "San Francisco, CA",
    "employment_type": "full-time",
    "salary_min": 120000,
    "salary_max": 180000,
    "experience_years": 5,
    "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
    "status": "active",
    "created_at": "2024-03-05T12:00:00Z"
  }
]
```

### Get Job Details
```http
GET /api/v1/candidate/jobs/{job_id}
```

**Response:** Same structure as browse jobs, single record

### Apply for Job (PHASE 2) ⭐
```http
POST /api/v1/candidate/jobs/{job_id}/apply
Content-Type: multipart/form-data
Authorization: Bearer <token>
```

**Form Data:**
- `resume` (file): PDF resume file
- `application` (JSON): Application details

**Application JSON:**
```json
{
  "first_name": "John",
  "last_name": "Doe",
  "email": "john.doe@email.com",
  "phone": "+1-555-0123",
  "current_salary": 100000,
  "expected_salary": 120000,
  "notice_period": "2 weeks",
  "linkedin_url": "https://linkedin.com/in/johndoe",
  "github_url": "https://github.com/johndoe",
  "portfolio_url": "https://johndoe.dev",
  "additional_info": "Passionate about building scalable applications"
}
```

**Response:**
```json
{
  "message": "Application submitted successfully",
  "candidate_id": "550e8400-e29b-41d4-a716-446655440002",
  "ats_score": 85.5,
  "application_stage": "applied"
}
```

### Get My Applications
```http
GET /api/v1/candidate/applications
Authorization: Bearer <token>
```

**Response:**
```json
{
  "applications": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440002",
      "job_title": "Senior Python Developer",
      "company": "Company Name",
      "stage": "screening",
      "ats_score": 85.5,
      "applied_at": "2024-03-05T12:00:00Z",
      "status": "screening"
    }
  ]
}
```

---

## 👔 HR Endpoints

### Create Job (PHASE 1) ⭐
```http
POST /api/v1/hr/jobs
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "title": "Senior Python Developer",
  "description": "We are looking for a Senior Python Developer to join our engineering team...",
  "requirements": "5+ years of Python development experience, Strong knowledge of FastAPI...",
  "department_id": "550e8400-e29b-41d4-a716-446655440003",
  "location": "San Francisco, CA",
  "employment_type": "full-time",
  "salary_min": 120000,
  "salary_max": 180000,
  "experience_years": 5,
  "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "Git"]
}
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440004",
  "title": "Senior Python Developer",
  "description": "We are looking for a Senior Python Developer...",
  "requirements": "5+ years of Python development experience...",
  "department": "Engineering",
  "location": "San Francisco, CA",
  "employment_type": "full-time",
  "salary_min": 120000,
  "salary_max": 180000,
  "experience_years": 5,
  "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
  "status": "active",
  "created_at": "2024-03-05T12:00:00Z"
}
```

### Get Job Candidates with ATS Ranking (PHASE 3) ⭐
```http
GET /api/v1/hr/jobs/{job_id}/candidates
Authorization: Bearer <token>
```

**Query Parameters:**
- `sort_by` (string, default: "ats_score"): ats_score, applied_at, name
- `order` (string, default: "desc"): desc, asc
- `min_score` (float, optional): Minimum ATS score filter
- `max_score` (float, optional): Maximum ATS score filter

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440004",
  "job_title": "Senior Python Developer",
  "total_candidates": 15,
  "candidates": [
    {
      "candidate_id": "550e8400-e29b-41d4-a716-446655440005",
      "name": "John Doe",
      "email": "john.doe@email.com",
      "phone": "+1-555-0123",
      "current_stage": "applied",
      "ats_score": 92.5,
      "skills_match_score": 95.0,
      "semantic_score": 88.0,
      "experience_score": 90.0,
      "keyword_score": 85.0,
      "matched_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
      "missing_skills": ["Kubernetes"],
      "extracted_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs"],
      "experience_years": 6,
      "applied_at": "2024-03-05T12:00:00Z",
      "resume_path": "resumes/john.doe_email_job_id_resume.pdf"
    }
  ]
}
```

### Move Candidates to Screening (PHASE 3) ⭐
```http
POST /api/v1/hr/jobs/{job_id}/move-to-screening
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "candidate_ids": [
    "550e8400-e29b-41d4-a716-446655440005",
    "550e8400-e29b-41d4-a716-446655440006"
  ],
  "reject_others": false
}
```

**Response:**
```json
{
  "message": "Candidates moved to screening successfully",
  "moved_to_screening": 2,
  "auto_rejected": 0,
  "job_id": "550e8400-e29b-41d4-a716-446655440004"
}
```

### Schedule Interview (PHASE 7) ⭐
```http
POST /api/v1/hr/interviews
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "candidate_id": "550e8400-e29b-41d4-a716-446655440005",
  "interviewer_id": "550e8400-e29b-41d4-a716-446655440007",
  "interview_type": "technical",
  "scheduled_at": "2024-03-10T14:00:00Z",
  "duration_minutes": 60,
  "meeting_link": "https://zoom.us/j/123456789"
}
```

**Response:**
```json
{
  "message": "Interview scheduled successfully",
  "interview_id": "550e8400-e29b-41d4-a716-446655440008",
  "candidate_name": "John Doe",
  "interviewer_name": "David Wilson",
  "scheduled_at": "2024-03-10T14:00:00Z"
}
```

### Create Offer (PHASE 10) ⭐
```http
POST /api/v1/hr/offers
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "candidate_id": "550e8400-e29b-41d4-a716-446655440005",
  "offer_type": "full_time",
  "salary": 140000,
  "bonus": 10000,
  "start_date": "2024-04-01",
  "expiry_date": "2024-03-20",
  "terms": "This offer is contingent upon background check..."
}
```

**Response:**
```json
{
  "message": "Offer created and sent successfully",
  "offer_id": "550e8400-e29b-41d4-a716-446655440009",
  "candidate_name": "John Doe",
  "salary": 140000,
  "expiry_date": "2024-03-20T00:00:00Z"
}
```

---

## 🎯 Interviewer Endpoints

### Get My Interviews
```http
GET /api/v1/interviewer/interviews
Authorization: Bearer <token>
```

**Response:**
```json
{
  "interviews": [
    {
      "interview_id": "550e8400-e29b-41d4-a716-446655440008",
      "candidate_name": "John Doe",
      "job_title": "Senior Python Developer",
      "interview_type": "technical",
      "scheduled_at": "2024-03-10T14:00:00Z",
      "duration_minutes": 60,
      "meeting_link": "https://zoom.us/j/123456789",
      "status": "scheduled"
    }
  ]
}
```

### Submit Interview Feedback (PHASE 8) ⭐
```http
POST /api/v1/interviewer/interviews/{interview_id}/feedback
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "technical_score": 85,
  "communication_score": 90,
  "problem_solving_score": 80,
  "cultural_fit_score": 88,
  "strengths": [
    "Strong Python skills",
    "Good problem solving approach",
    "Clear communication"
  ],
  "weaknesses": [
    "Limited experience with cloud deployment",
    "Could improve system design knowledge"
  ],
  "recommendations": "Consider for senior role with some mentorship",
  "hire_recommendation": "hire",
  "additional_notes": "Candidate shows great potential"
}
```

**Response:**
```json
{
  "message": "Interview feedback submitted successfully",
  "interview_id": "550e8400-e29b-41d4-a716-446655440008",
  "overall_score": 84.5,
  "hire_recommendation": "hire"
}
```

---

## 🛡️ Admin Endpoints

### Create HR User
```http
POST /api/v1/admin/users/hr
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "email": "hr@company.com",
  "password": "securePassword123",
  "role": "hr",
  "first_name": "Sarah",
  "last_name": "Johnson",
  "phone": "+1-555-0124"
}
```

**Form Data:**
- `department_id` (string): Department UUID
- `employee_id` (string): Employee ID

**Response:**
```json
{
  "message": "HR user created successfully",
  "user_id": "550e8400-e29b-41d4-a716-446655440010"
}
```

### Create Department
```http
POST /api/v1/admin/departments
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "name": "Engineering",
  "description": "Software development and technology teams"
}
```

**Response:**
```json
{
  "message": "Department created successfully",
  "department_id": "550e8400-e29b-41d4-a716-446655440003"
}
```

### Get System Analytics
```http
GET /api/v1/admin/analytics/overview
Authorization: Bearer <token>
```

**Response:**
```json
{
  "total_users": 25,
  "total_jobs": 8,
  "active_jobs": 5,
  "total_candidates": 156,
  "candidates_by_stage": [
    {"stage": "applied", "count": 45},
    {"stage": "screening", "count": 23},
    {"stage": "interview_scheduled", "count": 12},
    {"stage": "offer_sent", "count": 8},
    {"stage": "hired", "count": 3}
  ],
  "recent_applications": [
    {
      "name": "John Doe",
      "job_title": "Senior Python Developer",
      "stage": "screening",
      "ats_score": 92.5,
      "applied_at": "2024-03-05T12:00:00Z"
    }
  ]
}
```

---

## 🔄 12-Phase Workflow

### Phase Overview
| Phase | Description | API Endpoint | Status |
|-------|-------------|--------------|--------|
| 0 | Admin System Setup | `POST /api/v1/admin/users/hr` | ✅ |
| 1 | HR Creates Job | `POST /api/v1/hr/jobs` | ✅ |
| 2 | Candidate Applies | `POST /api/v1/candidate/jobs/{id}/apply` | ✅ |
| 3 | HR ATS Screening | `GET /api/v1/hr/jobs/{id}/candidates` | ✅ |
| 4 | Screening Call | Custom endpoint | 🔄 |
| 5 | HR Interview | Custom endpoint | 🔄 |
| 6 | Assessment Rounds | Custom endpoint | 🔄 |
| 7 | Schedule Technical Interview | `POST /api/v1/hr/interviews` | ✅ |
| 8 | Interviewer Feedback | `POST /api/v1/interviewer/interviews/{id}/feedback` | ✅ |
| 9 | HR Reviews Feedback | Custom endpoint | 🔄 |
| 10 | Create & Send Offer | `POST /api/v1/hr/offers` | ✅ |
| 11 | Candidate Accepts/Rejects | Custom endpoint | 🔄 |
| 12 | Hiring Complete | Custom endpoint | 🔄 |

### Workflow Diagram
```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → Phase 8 → Phase 9 → Phase 10 → Phase 11 → Phase 12
   ↓         ↓         ↓         ↓         ↓         ↓         ↓         ↓         ↓         ↓         ↓         ↓         ↓
Admin     HR       Candidate   HR        HR        HR        HR        HR       Interviewer  HR        HR       Candidate   HR
Setup    Creates   Applies    Screens   Calls    Interviews  Assesses  Schedules  Gives     Reviews   Sends     Responds   Completes
         Job       Resume     Ranking   Candidates            Interviews Interview Feedback   Feedback  Offer     to Offer   Hiring
```

---

## 🤖 AI/ML ATS Scoring

### 7-Factor Scoring Algorithm

| Factor | Weight | Description |
|---------|--------|-------------|
| Education | 35% | Degree match (40) + college tier (30) + CGPA (30) |
| Skills Match | 25% | Required (70) + preferred (30), **each scaled by where the resume evidences it** |
| Experience | 20% | Relevant years (50) + internships (25) + GitHub/OSS/hackathons (25) |
| Certifications | 10% | Count and relevance of professional certifications |
| Projects | 8% | Relevance of projects to the JD |
| Semantic | 1% | Meaning-level similarity of resume to JD |
| Keyword | 1% | JD keyword coverage, wherever in the resume it appears |

Source of truth: `DEFAULT_ATS_WEIGHTS` in `backend/src/services/enhanced_ats_service.py`.

**Skills are evidence-weighted.** A required skill used inside a job or a project
counts in full; the same skill sitting in a comma-separated list is discounted
(`SKILL_EVIDENCE_WEIGHTS`). A resume whose sections cannot be detected falls back
to a flat, position-blind match, so unusual formatting is never itself a penalty.

### Example ATS Result
```json
{
  "ats_score": 88.5,
  "skills_match_score": 95.0,
  "semantic_score": 85.2,
  "experience_score": 90.0,
  "keyword_score": 80.0,
  "matched_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
  "missing_skills": ["Kubernetes"],
  "extracted_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs"],
  "extracted_experience": 6
}
```

### Score Interpretation
- **90-100**: Excellent match - Strongly recommend
- **80-89**: Good match - Recommend
- **70-79**: Fair match - Consider
- **60-69**: Weak match - Borderline
- **Below 60**: Poor match - Reject

---

## ⚠️ Error Handling

### Standard Error Response Format
```json
{
  "detail": "Error message description",
  "error_code": "VALIDATION_ERROR",
  "timestamp": "2024-03-05T12:00:00Z"
}
```

### Common HTTP Status Codes
| Status | Description | Example |
|--------|-------------|---------|
| 200 | Success | Request completed successfully |
| 201 | Created | Resource created successfully |
| 400 | Bad Request | Invalid input data |
| 401 | Unauthorized | Invalid or missing token |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource not found |
| 409 | Conflict | Duplicate resource |
| 422 | Validation Error | Invalid data format |
| 500 | Internal Error | Server error |

### Specific Error Examples

#### Validation Error (422)
```json
{
  "detail": [
    {
      "loc": ["body", "email"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

#### Authentication Error (401)
```json
{
  "detail": "Could not validate credentials",
  "error_code": "AUTHENTICATION_FAILED"
}
```

#### Authorization Error (403)
```json
{
  "detail": "Insufficient permissions",
  "error_code": "AUTHORIZATION_FAILED"
}
```

---

## 🚦 Rate Limiting

### Rate Limits by Endpoint
| Endpoint Type | Limit | Window |
|---------------|-------|--------|
| Authentication | 5 requests | 1 minute |
| File Upload | 10 requests | 1 minute |
| Search/Query | 100 requests | 1 minute |
| Standard API | 1000 requests | 1 hour |

### Rate Limit Headers
```http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1646780400
```

### Rate Limit Exceeded Response
```json
{
  "detail": "Rate limit exceeded",
  "error_code": "RATE_LIMIT_EXCEEDED",
  "retry_after": 60
}
```

---

## 📝 Examples & Workflows

### Complete Candidate Application Workflow

#### Step 1: Candidate Registers
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "john.doe@email.com",
    "password": "securePassword123",
    "role": "candidate",
    "first_name": "John",
    "last_name": "Doe"
  }'
```

#### Step 2: Candidate Logs In
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "john.doe@email.com",
    "password": "securePassword123"
  }'
```

#### Step 3: Browse Jobs
```bash
curl -X GET "http://localhost:8000/api/v1/candidate/jobs?search=python" \
  -H "Authorization: Bearer <token>"
```

#### Step 4: Apply for Job
```bash
curl -X POST http://localhost:8000/api/v1/candidate/jobs/{job_id}/apply \
  -H "Authorization: Bearer <token>" \
  -F "resume=@resume.pdf" \
  -F 'application={
    "first_name": "John",
    "last_name": "Doe",
    "email": "john.doe@email.com",
    "expected_salary": 120000
  }'
```

### Complete HR Workflow

#### Step 1: HR Creates Job
```bash
curl -X POST http://localhost:8000/api/v1/hr/jobs \
  -H "Authorization: Bearer <hr_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Senior Python Developer",
    "description": "We are looking for a Senior Python Developer...",
    "requirements": "5+ years of Python experience...",
    "department_id": "dept-uuid",
    "location": "San Francisco, CA",
    "employment_type": "full-time",
    "salary_min": 120000,
    "salary_max": 180000,
    "experience_years": 5,
    "required_skills": ["Python", "FastAPI", "PostgreSQL"]
  }'
```

#### Step 2: View ATS Ranked Candidates
```bash
curl -X GET "http://localhost:8000/api/v1/hr/jobs/{job_id}/candidates?sort_by=ats_score&order=desc" \
  -H "Authorization: Bearer <hr_token>"
```

#### Step 3: Move Top Candidates to Screening
```bash
curl -X POST http://localhost:8000/api/v1/hr/jobs/{job_id}/move-to-screening \
  -H "Authorization: Bearer <hr_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "candidate_ids": ["candidate-uuid-1", "candidate-uuid-2"],
    "reject_others": false
  }'
```

#### Step 4: Schedule Interview
```bash
curl -X POST http://localhost:8000/api/v1/hr/interviews \
  -H "Authorization: Bearer <hr_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "candidate_id": "candidate-uuid",
    "interviewer_id": "interviewer-uuid",
    "interview_type": "technical",
    "scheduled_at": "2024-03-10T14:00:00Z",
    "duration_minutes": 60
  }'
```

### Interviewer Feedback Workflow

#### Step 1: Get Assigned Interviews
```bash
curl -X GET http://localhost:8000/api/v1/interviewer/interviews \
  -H "Authorization: Bearer <interviewer_token>"
```

#### Step 2: Submit Feedback
```bash
curl -X POST http://localhost:8000/api/v1/interviewer/interviews/{interview_id}/feedback \
  -H "Authorization: Bearer <interviewer_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "technical_score": 85,
    "communication_score": 90,
    "problem_solving_score": 80,
    "cultural_fit_score": 88,
    "strengths": ["Strong Python skills", "Good communication"],
    "weaknesses": ["Limited cloud experience"],
    "recommendations": "Hire with mentorship",
    "hire_recommendation": "hire"
  }'
```

---

## 🛠️ Development & Testing

### Local Development Setup
```bash
# Clone repository
git clone <repo-url>
cd hiring_system

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Setup database
createdb recruitment_db
psql recruitment_db < database_schema.sql

# Run development server
python -m uvicorn main:app --reload --port 8000

# Access API docs
open http://localhost:8000/docs
```

### Testing with cURL
```bash
# Health check
curl http://localhost:8000/health

# Register admin user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@test.com",
    "password": "admin123",
    "role": "admin",
    "first_name": "Admin",
    "last_name": "User"
  }'

# Login and get token
TOKEN=$(curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"admin123"}' \
  | jq -r '.access_token')

# Use token for authenticated requests
curl -X GET http://localhost:8000/api/v1/admin/analytics/overview \
  -H "Authorization: Bearer $TOKEN"
```

### Python Client Example
```python
import requests
import json

# Base URL
BASE_URL = "http://localhost:8000"

# Login
login_data = {
    "email": "hr@test.com",
    "password": "hr123"
}
response = requests.post(f"{BASE_URL}/api/v1/auth/login", json=login_data)
token = response.json()["access_token"]

# Headers
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Create job
job_data = {
    "title": "Python Developer",
    "description": "Looking for Python developer...",
    "requirements": "3+ years Python experience...",
    "department_id": "dept-uuid",
    "location": "Remote",
    "employment_type": "full-time",
    "experience_years": 3,
    "required_skills": ["Python", "FastAPI"]
}

response = requests.post(f"{BASE_URL}/api/v1/hr/jobs", json=job_data, headers=headers)
print(f"Job created: {response.json()}")
```

---

## 📊 Monitoring & Analytics

### System Health Endpoints
- `/health` - Basic health check
- `/metrics` - Prometheus metrics (if enabled)

### Analytics Views
- `v_candidate_pipeline` - Candidate pipeline by stage
- `v_ats_ranking` - ATS-ranked candidates
- `v_interview_feedback_summary` - Interview feedback analytics
- `v_hiring_metrics` - Monthly hiring metrics

### Database Queries
```sql
-- Top performing candidates
SELECT * FROM v_ats_ranking WHERE ats_score > 80;

-- Pipeline conversion rates
SELECT * FROM v_candidate_pipeline;

-- Recent interview feedback
SELECT * FROM v_interview_feedback_summary 
WHERE scheduled_at >= CURRENT_DATE - INTERVAL '7 days';
```

---

## 🔒 Security Considerations

### Authentication
- JWT tokens with 24-hour expiration
- Bcrypt password hashing
- Role-based access control

### Data Protection
- Input validation on all endpoints
- SQL injection prevention via ORM
- File upload restrictions (PDF only)
- Rate limiting on sensitive endpoints

### Best Practices
- HTTPS in production
- Environment variables for secrets
- Regular security audits
- Database connection pooling

---

## 📞 Support & Contact

### API Documentation
- Interactive docs: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Common Issues
1. **Database connection failed**: Check DATABASE_URL
2. **401 Unauthorized**: Verify token is valid
3. **422 Validation Error**: Check request format
4. **File upload failed**: Ensure PDF format

### Troubleshooting
```bash
# Check logs
python -m uvicorn main:app --reload --log-level debug

# Database connection test
psql $DATABASE_URL -c "SELECT 1;"

# API test
curl -I http://localhost:8000/health
```

---

## 📈 Performance Metrics

### Response Times
- Authentication: <200ms
- Database queries: <100ms
- File uploads: <2s
- ATS scoring: <500ms

### Scalability
- Database connection pooling: 20 connections
- Async request handling
- Horizontal scaling ready

---

**API Version: 1.0.0**  
**Last Updated: March 5, 2024**  
**Documentation Version: Complete**

For questions or support, contact the development team or check the interactive API documentation at `/docs`.
