# 🚀 HIREFLOW - COMPLETE WORKFLOW & STRUCTURED CODE GUIDE

## 📋 Overview

This document outlines the complete workflow and structured code organization for the HireFlow AI-powered recruitment system.

---

## 🏗️ PROJECT STRUCTURE

```
hiring_system/
├── 📁 src/                          # Core application code
│   ├── 📁 config/                    # Configuration files
│   │   └── database_config_mysql.py  # Database configuration
│   ├── 📁 services/                  # Business logic services
│   │   ├── enhanced_ats_service.py   # AI/ML ATS scoring
│   │   └── ollama_service.py        # AI service integration
│   ├── 📁 models/                    # Data models
│   │   ├── models.py                 # Database models
│   │   ├── ats_models.py           # ATS-specific models
│   │   ├── question_models.py       # Question models
│   │   └── question_schemas.py      # Question schemas
│   ├── auth.py                       # Authentication system
│   ├── config.py                     # App configuration
│   ├── constants.py                  # Application constants
│   ├── exceptions.py                 # Custom exceptions
│   ├── logging_config.py            # Logging configuration
│   └── utils.py                      # Utility functions
├── 📁 routes/                        # API route handlers
│   ├── auth_routes.py               # Authentication routes
│   ├── admin_routes.py              # Admin routes
│   ├── hr_routes.py                 # HR routes
│   ├── candidate_routes.py          # Candidate routes
│   ├── interviewer_routes.py        # Interviewer routes
│   ├── ats_routes.py                # ATS routes
│   └── database_management_routes.py # DB management
├── 📁 docs/                          # Documentation
│   ├── API_DOCUMENTATION.md         # API docs
│   ├── AI_RECRUITMENT_WORKFLOW.md   # AI workflow
│   ├── CODE_QUALITY_IMPROVEMENTS.md # Code quality
│   ├── ENHANCED_DATABASE_GUIDE.md   # Database guide
│   ├── JOB_CAREER_PAGE_FLOW.md     # Job flow
│   ├── MICROSERVICE_ARCHITECTURE.md # Architecture
│   ├── MYSQL_SETUP_COMPLETE.md      # MySQL setup
│   └── QUICKSTART.md               # Quick start
├── 📁 tests/                         # Test files
├── 📁 scripts/                       # Utility scripts
├── 📁 logs/                          # Application logs
├── main.py                           # FastAPI application entry
├── ats_manager.py                    # ATS manager
├── generator_router.py               # Question generator
├── mcq_api.py                       # MCQ API
├── prompt_builder.py                 # AI prompt builder
├── create_test_interview.py          # Test interview creator
├── requirements.txt                  # Dependencies
├── .env.example                      # Environment example
└── README.md                         # Main documentation
```

---

## 🔄 COMPLETE HIRING WORKFLOW

### Phase 0: System Setup
**Actor**: Admin
**Actions**:
- Initialize database and create tables
- Create departments and job categories
- Set up user accounts (HR, Interviewers, Admin)
- Configure system settings

**API Endpoints**:
```
POST /api/v1/admin/departments     # Create department
POST /api/v1/admin/users/hr         # Create HR user
POST /api/v1/admin/users/interviewer # Create interviewer
GET  /api/v1/admin/system/status    # System status
```

### Phase 1: Job Creation
**Actor**: HR
**Actions**:
- Create job posting with requirements
- Define required skills and experience
- Set salary range and job details
- Publish job to career portal

**API Endpoints**:
```
POST /api/v1/hr/jobs               # Create job posting
PUT  /api/v1/hr/jobs/{id}          # Update job
GET  /api/v1/hr/jobs               # List all jobs
POST /api/v1/hr/jobs/{id}/publish  # Publish job
```

### Phase 2: Candidate Application
**Actor**: Candidate
**Actions**:
- Browse available jobs
- Submit application with resume
- Provide personal information
- Receive confirmation

**API Endpoints**:
```
GET  /api/v1/candidate/jobs        # Browse jobs
GET  /api/v1/candidate/jobs/{id}   # Job details
POST /api/v1/candidate/jobs/{id}/apply # Apply with resume
GET  /api/v1/candidate/applications # My applications
```

### Phase 3: ATS Screening
**Actor**: HR
**Actions**:
- System automatically scores all applications
- Review ranked candidates by ATS score
- Filter by skills, experience, keywords
- Select top candidates for screening

**AI/ML Process**:
```
ATS Score = (Skills_Match × 35%) + (Semantic_Score × 35%) + 
            (Experience_Score × 20%) + (Keyword_Score × 10%)
```

**API Endpoints**:
```
GET  /api/v1/hr/jobs/{id}/candidates # ATS ranked candidates
POST /api/v1/hr/jobs/{id}/move-to-screening # Move to screening
GET  /api/v1/ats/score/{candidate_id} # Get ATS score details
```

### Phase 4: Initial Screening Call
**Actor**: HR
**Actions**:
- Conduct phone screening
- Verify basic qualifications
- Assess cultural fit
- Schedule HR interview

**API Endpoints**:
```
POST /api/v1/hr/interviews          # Schedule screening
PUT  /api/v1/hr/interviews/{id}     # Update interview
GET  /api/v1/hr/interviews          # List interviews
```

### Phase 5: HR Interview
**Actor**: HR
**Actions**:
- Conduct detailed HR interview
- Assess soft skills and communication
- Discuss salary expectations
- Move to technical assessment

**API Endpoints**:
```
POST /api/v1/hr/interviews/{id}/feedback # Submit feedback
PUT  /api/v1/hr/candidates/{id}/stage   # Update stage
```

### Phase 6: Technical Assessment
**Actor**: HR/Candidate
**Actions**:
- Assign technical tests/coding challenges
- AI generates questions based on job requirements
- Candidate completes assessments
- System auto-scores technical tests

**AI Question Generation**:
```
- Analyzes job description and requirements
- Generates role-specific technical questions
- Creates coding challenges for developers
- Adjusts difficulty based on experience level
```

**API Endpoints**:
```
POST /api/v1/interviews/generate-questions # Generate AI questions
GET  /api/v1/mcq/assessment/{id}          # Get MCQ assessment
POST /api/v1/mcq/submit                   # Submit assessment
```

### Phase 7: Technical Interview
**Actor**: Interviewer
**Actions**:
- Conduct technical interview
- Evaluate coding skills and problem-solving
- Assess technical knowledge
- Submit detailed feedback

**API Endpoints**:
```
GET  /api/v1/interviewer/interviews     # My interviews
POST /api/v1/interviewer/interviews/{id}/feedback # Submit feedback
GET  /api/v1/interviewer/candidates/{id} # Candidate details
```

### Phase 8: Interviewer Feedback Review
**Actor**: HR
**Actions**:
- Review all interviewer feedback
- Compare technical assessments
- Evaluate overall candidate performance
- Make decision on offer

**API Endpoints**:
```
GET  /api/v1/hr/candidates/{id}/feedback # All feedback
POST /api/v1/hr/candidates/{id}/evaluate # Evaluate candidate
```

### Phase 9: Offer Creation
**Actor**: HR
**Actions**:
- Generate offer letter
- Set compensation package
- Define start date and terms
- Send offer to candidate

**API Endpoints**:
```
POST /api/v1/hr/offers               # Create offer
GET  /api/v1/hr/offers               # List offers
PUT  /api/v1/hr/offers/{id}          # Update offer
POST /api/v1/hr/offers/{id}/send     # Send offer
```

### Phase 10: Offer Response
**Actor**: Candidate
**Actions**:
- Review offer details
- Accept or reject offer
- Negotiate if needed
- Confirm decision

**API Endpoints**:
```
GET  /api/v1/candidate/offers       # My offers
POST /api/v1/candidate/offers/{id}/respond # Respond to offer
```

### Phase 11: Hiring Complete
**Actor**: HR
**Actions**:
- Finalize hiring process
- Update candidate status to hired
- Initiate onboarding process
- Close job posting if needed

**API Endpoints**:
```
PUT  /api/v1/hr/candidates/{id}/hire # Mark as hired
POST /api/v1/hr/onboarding/{id}      # Start onboarding
PUT  /api/v1/hr/jobs/{id}/close      # Close job
```

---

## 🤖 AI/ML INTEGRATION POINTS

### 1. Resume Parsing & ATS Scoring
- **File**: `src/services/enhanced_ats_service.py`
- **Features**: OCR support, skill extraction, semantic matching
- **Algorithms**: TF-IDF, cosine similarity, NLP processing

### 2. Question Generation
- **File**: `prompt_builder.py`, `generator_router.py`
- **Features**: AI-powered question generation based on JD
- **Integration**: Ollama service for LLM capabilities

### 3. Interview Assessment
- **File**: `mcq_api.py`, `question_models.py`
- **Features**: Auto-generated technical assessments
- **Scoring**: Automated evaluation and feedback

---

## 🔧 CONFIGURATION MANAGEMENT

### Environment Variables
```bash
# Database
DATABASE_URL=mysql+pymysql://root:1234@localhost:3306/recruitment_db

# Security
SECRET_KEY=your-super-secret-key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# AI Services
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_API_KEY=sk-your-openai-key

# File Upload
UPLOAD_DIR=./uploads
MAX_FILE_SIZE=10485760  # 10MB
```

### Database Configuration
- **File**: `src/config/database_config_mysql.py`
- **Features**: Connection pooling, caching, error handling
- **Models**: SQLAlchemy ORM with 17 tables

---

## 📊 MONITORING & LOGGING

### Logging Structure
- **File**: `src/logging_config.py`
- **Levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL
- **Features**: Structured logging, performance tracking
- **Output**: File logs in `logs/` directory

### Health Monitoring
- **Endpoint**: `/health`
- **Metrics**: Database status, service health, system performance
- **Alerts**: Error tracking and notification system

---

## 🧪 TESTING STRATEGY

### Test Categories
1. **Unit Tests**: Individual function testing
2. **Integration Tests**: API endpoint testing
3. **Workflow Tests**: Complete hiring process
4. **AI/ML Tests**: ATS scoring accuracy

### Test Files Location
- **Directory**: `tests/`
- **Structure**: Mirror main application structure
- **Coverage**: Target 90%+ code coverage

---

## 🚀 DEPLOYMENT WORKFLOW

### Development Setup
```bash
# Setup virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run development server
python -m uvicorn main:app --reload --port 8000
```

### Production Deployment
```bash
# Using Gunicorn
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 main:app

# Using Docker
docker build -t hireflow .
docker run -p 8000:8000 --env-file .env hireflow
```

---

## 📈 PERFORMANCE OPTIMIZATION

### Database Optimization
- Connection pooling (20 base, 40 overflow)
- Strategic indexes on frequently queried columns
- Query optimization with efficient ORM usage
- JSON columns for flexible metadata storage

### Application Performance
- Async/await patterns for I/O operations
- Caching for frequently accessed data
- Background tasks for heavy processing
- Response compression and rate limiting

---

## 🔒 SECURITY FEATURES

### Authentication & Authorization
- JWT-based token authentication
- Role-based access control (4 roles)
- Password hashing with bcrypt
- Token expiration and refresh

### Data Protection
- Input validation with Pydantic models
- SQL injection prevention with SQLAlchemy ORM
- File upload security (PDF-only restriction)
- Rate limiting to prevent abuse

### Audit Trail
- Immutable history of all stage changes
- User attribution for every action
- Complete timestamp tracking
- Comprehensive audit logs

---

## 📞 SUPPORT & MAINTENANCE

### Documentation
- **API Reference**: `/docs` (Swagger UI)
- **ReDoc**: `/redoc`
- **Complete Guide**: `docs/` directory

### Common Issues
1. **Database Connection**: Verify MySQL is running and accessible
2. **Authentication**: Check token format and expiration
3. **File Upload**: Ensure directory exists with proper permissions
4. **AI Services**: Verify Ollama/OpenAI service availability

### Getting Help
- Check logs in `logs/` directory
- Review API documentation at `/docs`
- Health check at `/health` endpoint
- System info at `/api/v1/info`

---

## 🎉 SUMMARY

The HireFlow system provides a complete, production-ready recruitment solution with:

✅ **AI/ML-Powered ATS Scoring** - Advanced resume analysis
✅ **Complete 12-Phase Workflow** - End-to-end hiring process  
✅ **Role-Based Access Control** - 4 distinct user roles
✅ **Modern Architecture** - Microservice-based design
✅ **Comprehensive Testing** - 90%+ test coverage target
✅ **Production Ready** - Security, monitoring, deployment guides

**Version**: 2.0.0 (Structured Architecture)  
**Status**: Production Ready 🚀

For the most up-to-date information, visit the interactive API documentation at `http://localhost:8000/docs`.
