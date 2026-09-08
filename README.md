# 🚀 HIREFLOW - AI-POWERED RECRUITMENT SYSTEM

## 📋 Overview

**HireFlow** is a comprehensive, production-ready recruitment system featuring AI/ML-powered resume scoring, complete workflow automation, and role-based access control. Built with FastAPI and PostgreSQL, it provides an end-to-end solution from job creation to hiring.

### ✨ Key Features

- 🤖 **AI/ML ATS Scoring** - 4-factor resume ranking algorithm
- 📊 **Real-time Candidate Ranking** - Instant ATS score calculation
- 🔄 **12-Phase Workflow** - Complete hiring automation
- 👥 **4 Role-Based Access** - Admin, HR, Interviewer, Candidate
- 📄 **Resume Parsing** - PDF text extraction & skill detection
- 🎯 **Semantic Matching** - Embedding-based similarity scoring
- 🔐 **Enterprise Security** - JWT auth, role permissions, audit trail
- 📈 **Analytics Dashboard** - Comprehensive hiring metrics

---

## 🏗️ System Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │   FastAPI       │    │   PostgreSQL    │
│   (React/Vue)   │◄──►│   Backend       │◄──►│   Database      │
│                 │    │                 │    │                 │
│ • Career Portal │    │ • 50+ API End  │    │ • 14 Tables     │
│ • HR Dashboard  │    │ • AI/ML Scoring │    │ • Relationships │
│ • Admin Panel   │    │ • Auth System   │    │ • Indexes       │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

---

## 🚀 Quick Start

> **New to this project?** Start with **[`docs/END_TO_END_RUNBOOK.md`](docs/END_TO_END_RUNBOOK.md)**.
> It walks from an empty laptop to code running in production — local setup, the branch flow,
> GitHub secrets, the cPanel server, and deploying — with every command labelled by which
> terminal to type it in.

### Prerequisites
- Python 3.11
- MySQL 8
- Node.js 20 (for frontend)

### Installation

#### 1. Clone Repository
```bash
git clone <repository-url>
cd hiring_system
```

#### 2. Setup Database
```bash
# Create database
createdb recruitment_db

# Import schema
psql recruitment_db < database_schema.sql
```

#### 3. Setup Python Environment
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

#### 4. Configure Environment
```bash
# Create .env file
cat > .env << EOF
DATABASE_URL=postgresql://postgres:password@localhost:5432/recruitment_db
SECRET_KEY=your-super-secret-key-change-in-production
OPENAI_API_KEY=sk-your-openai-key
EOF
```

#### 5. Run Backend Server
```bash
python -m uvicorn main:app --reload --port 8000
```

#### 6. Access API
- **API Documentation**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

---

## 📊 Database Schema

### Core Tables (14 Total)

| Table | Purpose | Key Features |
|-------|---------|--------------|
| `users` | Authentication | JWT auth, role-based access |
| `job_postings` | Job Listings | JD embeddings, skill requirements |
| `candidates` | Applications | Resume text, ATS scores, stage tracking |
| `ats_results` | Scoring Breakdown | 4-factor scoring details |
| `interviews` | Interview Records | Scheduling, feedback integration |
| `interview_feedback` | Interviewer Input | Multi-dimensional scoring |
| `offers` | Offer Management | Status tracking, terms |
| `candidate_stage_history` | Audit Trail | Immutable stage changes |

### Database Relationships
```
users (1:1) hr_profiles
users (1:1) interviewer_profiles
departments (1:N) job_postings
job_postings (1:N) candidates
candidates (1:1) ats_results
candidates (1:N) interviews
interviews (1:1) interview_feedback
candidates (1:N) offers
```

---

## 🤖 AI/ML ATS Scoring System

### 4-Factor Scoring Algorithm

```
ATS_Score = (Skills_Match × 35%) + (Semantic_Score × 35%) + 
            (Experience_Score × 20%) + (Keyword_Score × 10%)
```

### Factor Breakdown

#### 1. Skills Match Score (35%)
- Extracts skills from resume using keyword matching
- Compares with job requirements
- Calculates percentage overlap

#### 2. Semantic Score (35%)
- Uses TF-IDF vectorization
- Computes cosine similarity
- Measures contextual relevance

#### 3. Experience Score (20%)
- Extracts years of experience from resume
- Compares with job requirements
- Rewards relevant experience

#### 4. Keyword Score (10%)
- Identifies important keywords in JD
- Checks presence in resume
- Filters common words

### Example Output
```json
{
  "ats_score": 88.5,
  "skills_match_score": 95.0,
  "semantic_score": 85.2,
  "experience_score": 90.0,
  "keyword_score": 80.0,
  "matched_skills": ["Python", "FastAPI", "PostgreSQL"],
  "missing_skills": ["Kubernetes"]
}
```

---

## 🔄 12-Phase Hiring Workflow

### Complete Workflow Overview

| Phase | Description | Primary User | Key Action |
|-------|-------------|--------------|------------|
| **0** | System Setup | Admin | Create users, departments |
| **1** | Job Creation | HR | Post job with requirements |
| **2** | Application | Candidate | Submit resume + details |
| **3** | ATS Screening | HR | Review ranked candidates |
| **4** | Screening Call | HR | Initial phone screening |
| **5** | HR Interview | HR | Detailed interview |
| **6** | Assessments | HR | Technical/coding tests |
| **7** | Technical Interview | HR | Schedule with interviewers |
| **8** | Interviewer Feedback | Interviewer | Submit detailed feedback |
| **9** | Review Feedback | HR | Evaluate all feedback |
| **10** | Offer Creation | HR | Generate and send offer |
| **11** | Offer Response | Candidate | Accept/reject offer |
| **12** | Hiring Complete | HR | Onboarding preparation |

### Workflow Automation Features

- **Automatic Stage Progression**: System moves candidates through phases
- **Audit Trail**: Every change logged with timestamp and user
- **Notifications**: Automated alerts for stage changes
- **Deadline Tracking**: Offer expiry and follow-up reminders

---

## 📚 API Documentation

### Authentication
All endpoints (except public) require JWT token:

```bash
Authorization: Bearer <your-jwt-token>
```

### Key Endpoints

#### Authentication
```http
POST /api/v1/auth/register    # Register new user
POST /api/v1/auth/login       # User login
POST /api/v1/auth/logout      # User logout
```

#### Candidate
```http
GET  /api/v1/candidate/jobs                    # Browse jobs
GET  /api/v1/candidate/jobs/{id}               # Job details
POST /api/v1/candidate/jobs/{id}/apply         # Apply with resume
GET  /api/v1/candidate/applications           # My applications
```

#### HR
```http
POST /api/v1/hr/jobs                          # Create job (Phase 1)
GET  /api/v1/hr/jobs/{id}/candidates          # ATS ranking (Phase 3)
POST /api/v1/hr/jobs/{id}/move-to-screening   # Move to screening
POST /api/v1/hr/interviews                    # Schedule interview
POST /api/v1/hr/offers                         # Create offer
```

#### Interviewer
```http
GET  /api/v1/interviewer/interviews          # My interviews
POST /api/v1/interviewer/interviews/{id}/feedback  # Submit feedback
```

#### Admin
```http
POST /api/v1/admin/users/hr                   # Create HR user
POST /api/v1/admin/departments                # Create department
GET  /api/v1/admin/analytics/overview         # System metrics
```

### Complete API Reference
- **Interactive Docs**: http://localhost:8000/docs
- **Detailed Documentation**: [API_DOCUMENTATION.md](./API_DOCUMENTATION.md)

---

## 🎯 Use Cases & Examples

### HR ATS Screening Workflow

#### 1. Create Job Posting
```bash
curl -X POST http://localhost:8000/api/v1/hr/jobs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Senior Python Developer",
    "description": "Looking for experienced Python developer...",
    "requirements": "5+ years Python, FastAPI, PostgreSQL",
    "department_id": "dept-uuid",
    "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"]
  }'
```

#### 2. View ATS Ranked Candidates
```bash
curl -X GET "http://localhost:8000/api/v1/hr/jobs/{job_id}/candidates?sort_by=ats_score&order=desc" \
  -H "Authorization: Bearer <token>"
```

#### 3. Move Top Candidates to Screening
```bash
curl -X POST http://localhost:8000/api/v1/hr/jobs/{job_id}/move-to-screening \
  -H "Authorization: Bearer <token>" \
  -d '{
    "candidate_ids": ["candidate-uuid-1", "candidate-uuid-2"],
    "reject_others": false
  }'
```

### Candidate Application Workflow

#### 1. Register and Login
```bash
# Register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "john.doe@email.com",
    "password": "securePassword123",
    "role": "candidate",
    "first_name": "John",
    "last_name": "Doe"
  }'

# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"john.doe@email.com","password":"securePassword123"}'
```

#### 2. Apply for Job
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

---

## 🔧 Configuration

### Environment Variables
```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/recruitment_db

# Security
SECRET_KEY=your-super-secret-key-change-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# AI/ML (Optional)
OPENAI_API_KEY=sk-your-openai-key

# File Upload
UPLOAD_DIR=./resumes
MAX_FILE_SIZE=10485760  # 10MB
```

### ATS Scoring Weights (Customizable)
```python
ATS_WEIGHTS = {
    "skills_match": 0.35,    # 35%
    "semantic": 0.35,         # 35%
    "experience": 0.20,      # 20%
    "keywords": 0.10         # 10%
}
```

---

## 📊 Analytics & Reporting

### Built-in Views
- `v_candidate_pipeline` - Candidates by stage
- `v_ats_ranking` - Ranked candidates with scores
- `v_interview_feedback_summary` - Interview analytics
- `v_hiring_metrics` - Monthly hiring statistics

### Sample Queries
```sql
-- Top performing candidates
SELECT * FROM v_ats_ranking WHERE ats_score > 80;

-- Pipeline conversion rates
SELECT job_title, current_stage, candidate_count 
FROM v_candidate_pipeline;

-- Interview feedback trends
SELECT interview_type, AVG(overall_score) as avg_score
FROM v_interview_feedback_summary
GROUP BY interview_type;
```

---

## 🧪 Testing

### Unit Tests
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=.

# Run specific test file
pytest test/test_ats_scoring.py
```

### Integration Tests
```bash
# Test API endpoints
pytest test/test_api_endpoints.py

# Test workflow
pytest test/test_workflow.py
```

### Manual Testing
```bash
# Health check
curl http://localhost:8000/health

# Test authentication
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"admin123"}'
```

---

## 🚀 Deployment

Production is **`career.innomaxskills.com`**, running on cPanel / AlmaLinux 9: MySQL 8,
five systemd units, and Apache as the reverse proxy. A merge to `main` deploys it
automatically once CI passes — nobody clicks anything.

| Doc | What it covers |
|---|---|
| **[`docs/END_TO_END_RUNBOOK.md`](docs/END_TO_END_RUNBOOK.md)** | **Start here.** Local → git → GitHub → cPanel, every command labelled by terminal |
| [`docs/CPANEL_DEPLOYMENT.md`](docs/CPANEL_DEPLOYMENT.md) | Installing on a brand new server |
| [`docs/DEPLOY_UPDATE.md`](docs/DEPLOY_UPDATE.md) | Deploying by hand, when the pipeline is unavailable |
| [`docs/CICD_SETUP.md`](docs/CICD_SETUP.md) | Full detail on the pipeline |
| [`docs/DEPLOYMENT_NOTES.md`](docs/DEPLOYMENT_NOTES.md) | Read **before** changing deployment behaviour |

> An earlier version of this section described a PostgreSQL / Gunicorn / Docker / Nginx
> setup. That deployment no longer exists; the docs above describe the real one.

---

## 🔒 Security Features

### Authentication & Authorization
- **JWT Tokens**: Secure token-based authentication
- **Role-Based Access**: 4 distinct roles with permissions
- **Password Hashing**: Bcrypt encryption
- **Token Expiration**: 24-hour default expiry

### Data Protection
- **Input Validation**: Pydantic models for all inputs
- **SQL Injection Prevention**: SQLAlchemy ORM
- **File Upload Security**: PDF-only restriction
- **Rate Limiting**: Prevent abuse

### Audit Trail
- **Immutable History**: All stage changes logged
- **User Attribution**: Every action tracked to user
- **Timestamp Records**: Complete audit timeline

---

## 📈 Performance Optimization

### Database Optimization
- **Connection Pooling**: 20 base connections, 40 overflow
- **Strategic Indexes**: 15+ performance indexes
- **Query Optimization**: Efficient ORM queries
- **JSON Columns**: Flexible metadata storage

### Application Performance
- **Async Processing**: FastAPI async/await
- **Caching**: Redis for frequently accessed data
- **Background Tasks**: Celery for heavy processing
- **Compression**: Gzip for API responses

### Monitoring
- **Health Endpoints**: System health checks
- **Metrics**: Prometheus integration
- **Logging**: Structured logging with levels
- **Error Tracking**: Comprehensive error handling

---

## 🤝 Contributing

### Development Setup
```bash
# Clone repository
git clone <repository-url>
cd hiring_system

# Setup development environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Setup pre-commit hooks
pre-commit install

# Run development server
python -m uvicorn main:app --reload
```

### Code Style
- **Python**: Black formatting, flake8 linting
- **Type Hints**: Full type annotation coverage
- **Documentation**: Comprehensive docstrings
- **Testing**: 90%+ test coverage required

### Pull Request Process
1. Fork repository
2. Create feature branch
3. Write tests for new functionality
4. Ensure all tests pass
5. Submit pull request with description

---

## 📞 Support

### Documentation
- **API Reference**: [API_DOCUMENTATION.md](./API_DOCUMENTATION.md)
- **Database Schema**: [database_schema.sql](./database_schema.sql)
- **Interactive Docs**: http://localhost:8000/docs

### Common Issues

#### Database Connection
```bash
# Check connection
psql $DATABASE_URL -c "SELECT 1;"

# Common solutions
- Verify PostgreSQL is running
- Check DATABASE_URL format
- Ensure database exists
```

#### Authentication Issues
```bash
# Verify token format
echo $TOKEN | cut -d. -f2 | base64 -d

# Common solutions
- Check token expiration
- Verify secret key
- Ensure correct headers
```

#### File Upload Issues
```bash
# Check upload directory
ls -la resumes/

# Common solutions
- Ensure directory exists
- Check permissions
- Verify file size limits
```

### Getting Help
- **Issues**: GitHub Issues for bug reports
- **Discussions**: GitHub Discussions for questions
- **Email**: support@yourcompany.com

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🎉 Acknowledgments

- **FastAPI**: Modern Python web framework
- **SQLAlchemy**: Powerful ORM for database operations
- **PostgreSQL**: Robust relational database
- **scikit-learn**: Machine learning library
- **pdfplumber**: PDF text extraction

---

## 📊 Project Statistics

- **Total Code Lines**: 6000+
- **API Endpoints**: 50+
- **Database Tables**: 14
- **Test Coverage**: 90%+
- **Documentation**: 2000+ lines
- **Supported Roles**: 4
- **Workflow Phases**: 12

---

**Version**: 1.0.0  
**Last Updated**: March 5, 2024  
**Status**: Production Ready 🚀

For the most up-to-date information, visit our interactive API documentation at `http://localhost:8000/docs`.
