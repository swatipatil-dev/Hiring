"""
🧪 CREATE TEST INTERVIEW
Creates a test interview for testing AI question generation
"""

import sys
from pathlib import Path

# Add current directory to path
sys.path.append(str(Path(__file__).parent))

from src.config.database_config_mysql import get_db, init_db
from src.models.models import Interview
from datetime import datetime, timedelta
import uuid
import json

def create_test_interview():
    """Create a test interview with sample data"""
    print("🧪 Creating Test Interview for AI Question Generation")
    print("=" * 60)
    
    try:
        # Initialize database
        if not init_db():
            print("❌ Database initialization failed!")
            return False
        
        # Create database session
        with get_db() as db:
            # First, create a test candidate if not exists
            from models import Candidate
            candidate = db.query(Candidate).filter(Candidate.email == "test_candidate@example.com").first()
            if not candidate:
                candidate = Candidate(
                    id=str(uuid.uuid4()),
                    job_id=str(uuid.uuid4()),  # Will be updated later
                    first_name="Test",
                    last_name="Candidate",
                    email="test_candidate@example.com",
                    phone="123-456-7890",
                    current_stage="interview",
                    status="active",
                    applied_at=datetime.now(),
                    updated_at=datetime.now()
                )
                db.add(candidate)
                db.commit()
                print(f"✅ Created test candidate: {candidate.email}")
            
            # Create test interviewer profile if not exists
            from models import InterviewerProfile, User
            interviewer_user = db.query(User).filter(User.email == "test_interviewer@example.com").first()
            if not interviewer_user:
                interviewer_user = User(
                    id=str(uuid.uuid4()),
                    email="test_interviewer@example.com",
                    password_hash="!",   # unusable: no bcrypt hash matches it, so nobody can sign in as this
                    role="interviewer",
                    first_name="Test",
                    last_name="Interviewer",
                    phone="123-456-7890",
                    is_active=True,
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                db.add(interviewer_user)
                db.commit()
                
                interviewer_profile = InterviewerProfile(
                    id=str(uuid.uuid4()),
                    user_id=interviewer_user.id,
                    department_id=None,
                    employee_id="INT001",
                    expertise_areas=["Python", "Django", "PostgreSQL"],
                    years_experience=5,
                    created_at=datetime.now()
                )
                db.add(interviewer_profile)
                db.commit()
                print(f"✅ Created test interviewer: {interviewer_user.email}")
            else:
                interviewer_profile = db.query(InterviewerProfile).filter(InterviewerProfile.user_id == interviewer_user.id).first()
            
            # Create test job posting if not exists
            from models import JobPosting
            job = db.query(JobPosting).filter(JobPosting.title == "Software Engineer AI Test").first()
            if not job:
                job = JobPosting(
                    id=str(uuid.uuid4()),
                    title="Software Engineer AI Test",
                    description="Test job for AI question generation",
                    requirements="Python, Django, PostgreSQL, REST APIs",
                    department_id=None,
                    location="Remote",
                    employment_type="Full-time",
                    salary_min=80000.0,
                    salary_max=120000.0,
                    experience_years=5,
                    required_skills=["python", "django", "postgresql", "rest api"],
                    status="active",
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                db.add(job)
                db.commit()
                print(f"✅ Created test job: {job.title}")
            
            # Update candidate's job_id
            candidate.job_id = job.id
            db.commit()
            
            # Check if test interview already exists
            existing = db.query(Interview).filter(Interview.interview_type == "ai_test").first()
            if existing:
                print(f"✅ Test interview already exists (ID: {existing.id})")
                return existing.id
            
            # Create test interview
            interview = Interview(
                id=str(uuid.uuid4()),
                candidate_id=candidate.id,
                interviewer_id=interviewer_profile.id,
                job_id=job.id,
                interview_type="ai_test",
                scheduled_at=datetime.now() + timedelta(days=1),
                duration_minutes=60,
                meeting_link="https://zoom.us/j/test-interview",
                status="scheduled",
                interviewer_notes="Test interview for AI question generation",
                created_at=datetime.now()
            )
            
            db.add(interview)
            db.commit()
            
            print(f"✅ Created test interview with ID: {interview.id}")
            print(f"� Candidate: {candidate.first_name} {candidate.last_name}")
            print(f"� Interviewer: {interviewer_user.first_name} {interviewer_user.last_name}")
            print(f"💼 Job: {job.title}")
            print(f"� Scheduled: {interview.scheduled_at}")
            print(f"⏱️  Duration: {interview.duration_minutes} minutes")
            
            return interview.id
            
    except Exception as e:
        print(f"❌ Error creating test interview: {e}")
        return False

def create_multiple_test_interviews():
    """Create multiple test interviews for different roles"""
    print("🧪 Creating Multiple Test Interviews")
    print("=" * 60)
    
    interviews_data = [
        {
            "title": "Frontend Developer AI Test Interview",
            "job_title": "Frontend Developer",
            "description": "Frontend developer position with React and TypeScript focus",
            "requirements": "React, TypeScript, JavaScript, HTML, CSS, Redux",
            "skills": "react, typescript, javascript, html, css, redux"
        },
        {
            "title": "Backend Developer AI Test Interview", 
            "job_title": "Backend Developer",
            "description": "Backend developer position with Node.js and database focus",
            "requirements": "Node.js, Express, MongoDB, SQL, REST APIs",
            "skills": "nodejs, express, mongodb, sql, rest api"
        },
        {
            "title": "DevOps Engineer AI Test Interview",
            "job_title": "DevOps Engineer", 
            "description": "DevOps engineer position with cloud and containerization focus",
            "requirements": "Docker, Kubernetes, AWS, CI/CD, Linux",
            "skills": "docker, kubernetes, aws, cicd, linux"
        },
        {
            "title": "Data Scientist AI Test Interview",
            "job_title": "Data Scientist",
            "description": "Data scientist position with machine learning focus",
            "requirements": "Python, Machine Learning, Statistics, SQL, Data Visualization",
            "skills": "python, machine learning, statistics, sql, data visualization"
        }
    ]
    
    try:
        with get_db() as db:
            created_count = 0
            
            for interview_data in interviews_data:
                # Check if already exists
                existing = db.query(Interview).filter(Interview.title == interview_data["title"]).first()
                if existing:
                    print(f"⚠️  {interview_data['title']} already exists (ID: {existing.id})")
                    continue
                
                # Create interview
                interview = Interview(
                    id=str(uuid.uuid4()),
                    title=interview_data["title"],
                    description=interview_data["description"],
                    job_title=interview_data["job_title"],
                    job_description=interview_data["description"],
                    requirements=interview_data["requirements"],
                    required_skills=interview_data["skills"],
                    department_id=None,
                    location="Remote",
                    employment_type="Full-time",
                    salary_min=70000.0,
                    salary_max=130000.0,
                    experience_years=4,
                    status="active",
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                
                db.add(interview)
                created_count += 1
                print(f"✅ Created: {interview_data['title']}")
            
            db.commit()
            print(f"\n📊 Created {created_count} new test interviews")
            
            # Show total interviews
            total = db.query(Interview).filter(Interview.title.like("%AI Test Interview%")).count()
            print(f"📋 Total AI test interviews: {total}")
            
            return True
            
    except Exception as e:
        print(f"❌ Error creating test interviews: {e}")
        return False

def show_interviews():
    """Show all interviews available for testing"""
    print("📋 Available Test Interviews")
    print("=" * 60)
    
    try:
        with get_db() as db:
            interviews = db.query(Interview).filter(Interview.interview_type == "ai_test").all()
            
            if not interviews:
                print("❌ No test interviews found")
                return
            
            for i, interview in enumerate(interviews, 1):
                print(f"{i}. Interview ID: {interview.id}")
                print(f"   👤 Candidate ID: {interview.candidate_id}")
                print(f"   � Interviewer ID: {interview.interviewer_id}")
                print(f"   💼 Job ID: {interview.job_id}")
                print(f"   � Scheduled: {interview.scheduled_at}")
                print(f"   ⏱️  Duration: {interview.duration_minutes} minutes")
                print(f"   � Meeting Link: {interview.meeting_link}")
                print()
            
            print(f"📊 Total: {len(interviews)} test interviews available")
            
    except Exception as e:
        print(f"❌ Error fetching interviews: {e}")

def main():
    """Main function"""
    print("🧪 TEST INTERVIEW CREATION SCRIPT")
    print("=" * 60)
    print("📡 Database: MySQL recruitment_db")
    print("🤖 Purpose: Create test interviews for AI question generation")
    print("=" * 60)
    
    # Create single test interview
    interview_id = create_test_interview()
    
    if interview_id:
        print(f"\n✅ Single test interview created: {interview_id}")
    
    # Create multiple test interviews
    print("\n" + "=" * 60)
    multiple_success = create_multiple_test_interviews()
    
    if multiple_success:
        print("\n✅ Multiple test interviews created successfully!")
    
    # Show all interviews
    print("\n" + "=" * 60)
    show_interviews()
    
    print("\n🎯 Next Steps:")
    print("1. Start the ATS Manager server")
    print("2. Use the enhanced interviewer endpoints")
    print("3. Test AI question generation")
    print("4. Example: POST /api/v1/interviewer/interviews/{interview_id}/generate-questions")
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
