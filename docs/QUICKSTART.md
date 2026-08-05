# 🚀 HIREFLOW - QUICK START GUIDE

## ⚡ 10-Minute Setup Guide

Get your AI-powered recruitment system running in minutes!

---

## 📋 Prerequisites

Make sure you have installed:
- **Python 3.8+**
- **PostgreSQL 12+**
- **Git**

---

## 🏃‍♂️ Quick Setup (5 Commands)

### 1. Clone and Navigate
```bash
git clone <your-repo-url>
cd hiring_system
```

### 2. Setup Database
```bash
# Create database
createdb recruitment_db

# Import schema (one command)
psql recruitment_db < database_schema.sql
```

### 3. Setup Python Environment
```bash
# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Mac/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
# Copy environment template
cp .env.example .env

# Edit .env file (minimum required)
# DATABASE_URL=postgresql://postgres:password@localhost:5432/recruitment_db
# SECRET_KEY=your-secret-key-here
```

### 5. Start Server
```bash
python -m uvicorn main:app --reload --port 8000
```

🎉 **Done!** Your API is running at http://localhost:8000

---

## 🧪 Quick Test (2 Minutes)

### Test Health Check
```bash
curl http://localhost:8000/health
```
Expected response:
```json
{"status": "healthy", "timestamp": "2024-03-05T12:00:00Z"}
```

### Create Admin User
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@test.com",
    "password": "admin123",
    "role": "admin",
    "first_name": "Admin",
    "last_name": "User"
  }'
```

### Login and Get Token
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"admin123"}'
```

---

## 📚 Explore API Documentation

### Interactive API Docs
Open in browser: **http://localhost:8000/docs**

### Key Endpoints to Try
1. **POST** `/api/v1/auth/register` - Create users
2. **POST** `/api/v1/auth/login` - Login
3. **POST** `/api/v1/hr/jobs` - Create job (HR role)
4. **GET** `/api/v1/candidate/jobs` - Browse jobs
5. **POST** `/api/v1/candidate/jobs/{id}/apply` - Apply with resume

---

## 🎯 First Workflow Demo (5 Minutes)

### Step 1: Create HR User
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "hr@test.com",
    "password": "hr123",
    "role": "hr",
    "first_name": "Sarah",
    "last_name": "Johnson"
  }'
```

### Step 2: Login as HR
```bash
# Get HR token
HR_TOKEN=$(curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"hr@test.com","password":"hr123"}' \
  | jq -r '.access_token')
```

### Step 3: Create Department
```bash
curl -X POST http://localhost:8000/api/v1/admin/departments \
  -H "Authorization: Bearer $HR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Engineering",
    "description": "Software development team"
  }'
```

### Step 4: Create Job Posting
```bash
curl -X POST http://localhost:8000/api/v1/hr/jobs \
  -H "Authorization: Bearer $HR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Python Developer",
    "description": "Looking for Python developer with FastAPI experience",
    "requirements": "3+ years Python, FastAPI, PostgreSQL",
    "department_id": "engineering-dept-id",
    "location": "Remote",
    "employment_type": "full-time",
    "experience_years": 3,
    "required_skills": ["Python", "FastAPI", "PostgreSQL"]
  }'
```

### Step 5: View Jobs (Candidate Perspective)
```bash
curl http://localhost:8000/api/v1/candidate/jobs
```

---

## 📄 Test Resume Upload

### Create Sample Resume
Create a simple PDF file named `test_resume.pdf` with text:
```
John Doe
Email: john.doe@email.com

Experience:
- 4 years Python development
- 2 years FastAPI experience
- 3 years PostgreSQL
- Built REST APIs and web applications

Skills:
Python, FastAPI, PostgreSQL, Docker, REST APIs, Git
```

### Apply for Job
```bash
# Create candidate user first
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "candidate@test.com",
    "password": "candidate123",
    "role": "candidate",
    "first_name": "John",
    "last_name": "Doe"
  }'

# Login as candidate
CANDIDATE_TOKEN=$(curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"candidate@test.com","password":"candidate123"}' \
  | jq -r '.access_token')

# Apply with resume
curl -X POST http://localhost:8000/api/v1/candidate/jobs/{job-id}/apply \
  -H "Authorization: Bearer $CANDIDATE_TOKEN" \
  -F "resume=@test_resume.pdf" \
  -F 'application={
    "first_name": "John",
    "last_name": "Doe",
    "email": "john.doe@email.com",
    "expected_salary": 90000
  }'
```

### View ATS Results
```bash
curl -X GET "http://localhost:8000/api/v1/hr/jobs/{job-id}/candidates" \
  -H "Authorization: Bearer $HR_TOKEN"
```

---

## 🔧 Common Setup Issues & Solutions

### Database Connection Issues
```bash
# Check if PostgreSQL is running
pg_isready

# Test connection
psql postgresql://postgres:password@localhost:5432/recruitment_db -c "SELECT 1;"

# Common solutions:
# 1. Ensure PostgreSQL service is running
# 2. Check DATABASE_URL in .env file
# 3. Verify database exists: \l in psql
```

### Python Environment Issues
```bash
# Check Python version
python --version

# Virtual environment issues
# Delete and recreate:
rm -rf venv
python -m venv venv
source venv/bin/activate  # Mac/Linux
# or
venv\Scripts\activate  # Windows

# Reinstall dependencies
pip install -r requirements.txt
```

### Port Already in Use
```bash
# Check what's using port 8000
netstat -tulpn | grep :8000  # Linux
netstat -ano | findstr :8000  # Windows

# Kill process or use different port
python -m uvicorn main:app --reload --port 8001
```

### Permission Issues
```bash
# Create resumes directory
mkdir -p resumes
chmod 755 resumes  # Linux/Mac

# Or run with elevated permissions (not recommended for production)
sudo python -m uvicorn main:app --reload --port 8000
```

---

## 📊 Quick Verification Checklist

### ✅ System Health
- [ ] Server starts without errors
- [ ] Health endpoint returns 200
- [ ] Database connection successful
- [ ] API docs accessible at /docs

### ✅ Authentication
- [ ] User registration works
- [ ] Login returns JWT token
- [ ] Token validation works
- [ ] Role-based access enforced

### ✅ Core Features
- [ ] Job creation works
- [ ] Resume upload processes
- [ ] ATS scoring calculates
- [ ] Candidate ranking displays

### ✅ Database
- [ ] Tables created correctly
- [ ] Sample data inserted
- [ ] Relationships working
- [ ] Indexes created

---

## 🎯 Next Steps

### Explore Features
1. **Browse API Docs**: http://localhost:8000/docs
2. **Test All Endpoints**: Try different user roles
3. **Upload Real Resumes**: Test ATS scoring
4. **Create Complete Workflow**: Job → Apply → Screen → Interview

### Production Setup
1. **Environment Variables**: Update .env with production values
2. **Database**: Use production PostgreSQL instance
3. **Security**: Generate strong SECRET_KEY
4. **Deployment**: Use Gunicorn + Nginx

### Customization
1. **ATS Weights**: Adjust scoring algorithm
2. **Workflow Phases**: Modify hiring stages
3. **User Roles**: Add custom permissions
4. **Email Notifications**: Configure SMTP

---

## 📞 Need Help?

### Documentation
- **Full API Docs**: [API_DOCUMENTATION.md](./API_DOCUMENTATION.md)
- **Database Schema**: [database_schema.sql](./database_schema.sql)
- **Complete README**: [README.md](./README.md)

### Common Commands Reference
```bash
# Start server
python -m uvicorn main:app --reload --port 8000

# Database operations
createdb recruitment_db
psql recruitment_db < database_schema.sql

# Test endpoints
curl http://localhost:8000/health
curl http://localhost:8000/docs

# Python environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Troubleshooting Quick Tips
1. **Check logs**: Server console shows detailed errors
2. **Verify .env**: Ensure all required variables are set
3. **Database**: Test connection with psql first
4. **Dependencies**: Ensure all packages installed successfully

---

## 🎉 Success!

You now have a fully functional AI-powered recruitment system running!

### What You Can Do Now
- ✅ Create job postings
- ✅ Accept candidate applications
- ✅ Score resumes with AI
- ✅ Rank candidates automatically
- ✅ Manage interview process
- ✅ Track hiring workflow

### Key URLs
- **API Documentation**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health
- **ReDoc**: http://localhost:8000/redoc

**Happy Hiring! 🚀**

---

*For detailed documentation and advanced features, see the full README.md file.*
