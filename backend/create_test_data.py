"""Create 5 jobs with tests and questions for proctoring testing."""
import uuid
import json
from sqlalchemy import create_engine, text

engine = create_engine('mysql+pymysql://root:1234@localhost:3306/recruitment_db')

HR_ID = '8975b280-b883-4898-bec5-82aa54205cd6'
INTERVIEWER_ID = '5ffece4c-8422-45a5-a5b0-67bfda655576'

departments = [
    ('dept-eng', 'Engineering'),
    ('dept-ds', 'Data Science'),
    ('dept-devops', 'DevOps'),
    ('dept-fe', 'Frontend'),
    ('dept-qa', 'Quality Assurance'),
]

jobs = [
    {
        'id': str(uuid.uuid4()), 'title': 'Senior Python Developer',
        'description': 'Build scalable backend services using Python, FastAPI, and microservices architecture.',
        'requirements': 'Python, FastAPI, SQLAlchemy, PostgreSQL, Docker, REST APIs, 3+ years',
        'department_id': 'dept-eng',
        'required_skills': 'Python,FastAPI,SQLAlchemy,Docker,REST APIs,PostgreSQL',
        'experience_years': 3, 'salary_min': 80000, 'salary_max': 120000,
        'location': 'Remote', 'employment_type': 'Full-time',
    },
    {
        'id': str(uuid.uuid4()), 'title': 'Machine Learning Engineer',
        'description': 'Design and deploy ML models for production. Work on NLP, CV, and recommendation systems.',
        'requirements': 'Python, TensorFlow, PyTorch, scikit-learn, NLP, Computer Vision, 2+ years',
        'department_id': 'dept-ds',
        'required_skills': 'Python,TensorFlow,PyTorch,scikit-learn,NLP,Computer Vision',
        'experience_years': 2, 'salary_min': 90000, 'salary_max': 140000,
        'location': 'Hybrid', 'employment_type': 'Full-time',
    },
    {
        'id': str(uuid.uuid4()), 'title': 'React Frontend Developer',
        'description': 'Build modern, responsive web apps using React, TypeScript, and modern CSS.',
        'requirements': 'React, TypeScript, CSS, REST APIs, Git, responsive design, 2+ years',
        'department_id': 'dept-fe',
        'required_skills': 'React,TypeScript,JavaScript,CSS,HTML,REST APIs',
        'experience_years': 2, 'salary_min': 70000, 'salary_max': 110000,
        'location': 'Remote', 'employment_type': 'Full-time',
    },
    {
        'id': str(uuid.uuid4()), 'title': 'DevOps Engineer',
        'description': 'Manage CI/CD pipelines, cloud infrastructure (AWS/GCP), and container orchestration.',
        'requirements': 'AWS, Docker, Kubernetes, Terraform, CI/CD, Linux, 3+ years',
        'department_id': 'dept-devops',
        'required_skills': 'AWS,Docker,Kubernetes,Terraform,CI/CD,Linux,Jenkins',
        'experience_years': 3, 'salary_min': 85000, 'salary_max': 130000,
        'location': 'On-site', 'employment_type': 'Full-time',
    },
    {
        'id': str(uuid.uuid4()), 'title': 'QA Automation Engineer',
        'description': 'Design and implement automated testing frameworks for web and API services.',
        'requirements': 'Selenium, Cypress, Python, API testing, CI/CD, 2+ years',
        'department_id': 'dept-qa',
        'required_skills': 'Selenium,Cypress,Python,API Testing,CI/CD,Jest,pytest',
        'experience_years': 2, 'salary_min': 65000, 'salary_max': 100000,
        'location': 'Remote', 'employment_type': 'Full-time',
    },
]

questions_data = {
    'Senior Python Developer': [
        {'text': 'What is the output of: list(range(0, 10, 3))?', 'opts': ['[0, 3, 6, 9]', '[0, 3, 6]', '[3, 6, 9]', '[0, 1, 2, 3]'], 'ans': 'A', 'diff': 'Easy', 'cat': 'Python Basics'},
        {'text': 'Which Python decorator is used to define a class method?', 'opts': ['@staticmethod', '@classmethod', '@property', '@abstractmethod'], 'ans': 'B', 'diff': 'Easy', 'cat': 'Python OOP'},
        {'text': 'What does the GIL in CPython prevent?', 'opts': ['Multiple processes from running', 'Multiple threads from executing Python bytecode simultaneously', 'Garbage collection', 'Import of C extensions'], 'ans': 'B', 'diff': 'Medium', 'cat': 'Python Internals'},
        {'text': 'In FastAPI, what is the purpose of Depends()?', 'opts': ['To create middleware', 'To define dependency injection for route handlers', 'To handle WebSocket connections', 'To configure CORS'], 'ans': 'B', 'diff': 'Medium', 'cat': 'FastAPI'},
        {'text': 'Which SQLAlchemy method creates all ORM tables?', 'opts': ['engine.create_all()', 'Base.metadata.create_all(engine)', 'session.create_tables()', 'db.init_tables()'], 'ans': 'B', 'diff': 'Medium', 'cat': 'SQLAlchemy'},
    ],
    'Machine Learning Engineer': [
        {'text': 'What is the vanishing gradient problem?', 'opts': ['Gradients become very large', 'Gradients become very small, preventing weight updates', 'Model overfits', 'Loss reaches zero'], 'ans': 'B', 'diff': 'Medium', 'cat': 'Deep Learning'},
        {'text': 'Which technique handles class imbalance?', 'opts': ['Feature scaling', 'SMOTE (Synthetic Minority Oversampling)', 'PCA', 'Batch normalization'], 'ans': 'B', 'diff': 'Medium', 'cat': 'ML Techniques'},
        {'text': 'What does attention mechanism do in transformers?', 'opts': ['Reduces training time', 'Focuses on relevant parts of input sequence', 'Prevents overfitting', 'Normalizes outputs'], 'ans': 'B', 'diff': 'Hard', 'cat': 'NLP'},
        {'text': 'Which loss function for multi-class classification?', 'opts': ['Binary Cross-Entropy', 'Mean Squared Error', 'Categorical Cross-Entropy', 'Hinge Loss'], 'ans': 'C', 'diff': 'Easy', 'cat': 'ML Fundamentals'},
        {'text': 'What is a confusion matrix for?', 'opts': ['Visualize data distribution', 'Evaluate classification (TP, FP, TN, FN)', 'Reduce dimensionality', 'Tune hyperparameters'], 'ans': 'B', 'diff': 'Easy', 'cat': 'ML Evaluation'},
    ],
    'React Frontend Developer': [
        {'text': 'Which hook performs side effects in React?', 'opts': ['useState', 'useEffect', 'useReducer', 'useContext'], 'ans': 'B', 'diff': 'Easy', 'cat': 'React Hooks'},
        {'text': 'What is the purpose of React.memo()?', 'opts': ['Manage state', 'Prevent unnecessary re-renders', 'Handle side effects', 'Create context'], 'ans': 'B', 'diff': 'Medium', 'cat': 'React Performance'},
        {'text': 'Difference between interface and type in TypeScript?', 'opts': ['Identical', 'Interfaces can be extended and support declaration merging', 'Types are faster', 'Interfaces only for classes'], 'ans': 'B', 'diff': 'Medium', 'cat': 'TypeScript'},
        {'text': 'What does display: flex do?', 'opts': ['Makes element invisible', 'Enables flexbox layout', 'Creates grid layout', 'Floats element'], 'ans': 'B', 'diff': 'Easy', 'cat': 'CSS'},
        {'text': 'What is the virtual DOM?', 'opts': ['Browser API', 'Lightweight copy of real DOM for efficient updates', 'CSS engine', 'Server-side rendering'], 'ans': 'B', 'diff': 'Easy', 'cat': 'React Core'},
    ],
    'DevOps Engineer': [
        {'text': 'What is a Kubernetes Pod?', 'opts': ['Persistent storage', 'Smallest deployable unit with one or more containers', 'Network router', 'Auto-scaler'], 'ans': 'B', 'diff': 'Medium', 'cat': 'Kubernetes'},
        {'text': 'Which Terraform command creates an execution plan?', 'opts': ['terraform apply', 'terraform plan', 'terraform init', 'terraform validate'], 'ans': 'B', 'diff': 'Easy', 'cat': 'Terraform'},
        {'text': 'Difference between Docker CMD and ENTRYPOINT?', 'opts': ['Identical', 'CMD provides overridable defaults; ENTRYPOINT defines main executable', 'CMD at build, ENTRYPOINT at runtime', 'ENTRYPOINT deprecated'], 'ans': 'B', 'diff': 'Medium', 'cat': 'Docker'},
        {'text': 'What does CI/CD stand for?', 'opts': ['Code Integration/Deployment', 'Continuous Integration/Continuous Delivery', 'Container Integration/Delivery', 'Cloud Infrastructure/Development'], 'ans': 'B', 'diff': 'Easy', 'cat': 'CI/CD'},
        {'text': 'Which AWS service runs serverless functions?', 'opts': ['EC2', 'Lambda', 'ECS', 'S3'], 'ans': 'B', 'diff': 'Easy', 'cat': 'AWS'},
    ],
    'QA Automation Engineer': [
        {'text': 'What is the Page Object Model (POM)?', 'opts': ['Database structure', 'Web pages represented as classes with elements/methods', 'CSS framework', 'Testing methodology'], 'ans': 'B', 'diff': 'Medium', 'cat': 'Test Patterns'},
        {'text': 'Which Selenium method waits for clickable element?', 'opts': ['find_element()', 'WebDriverWait with element_to_be_clickable()', 'implicitly_wait()', 'sleep()'], 'ans': 'B', 'diff': 'Medium', 'cat': 'Selenium'},
        {'text': 'Difference between unit and integration tests?', 'opts': ['Same thing', 'Unit tests isolated components; integration tests component interactions', 'Unit auto, integration manual', 'Integration faster'], 'ans': 'B', 'diff': 'Easy', 'cat': 'Testing Fundamentals'},
        {'text': 'What does @pytest.fixture do?', 'opts': ['Skip test', 'Define reusable setup/teardown injected into tests', 'Run parallel', 'Generate reports'], 'ans': 'B', 'diff': 'Medium', 'cat': 'pytest'},
        {'text': 'What is API testing focused on?', 'opts': ['UI appearance', 'Business logic, data, and API responses', 'Database schema', 'Network latency'], 'ans': 'B', 'diff': 'Easy', 'cat': 'API Testing'},
    ],
}

with engine.connect() as conn:
    # Create departments
    for dept_id, dept_name in departments:
        conn.execute(text(
            "INSERT IGNORE INTO departments (id, name, created_at) VALUES (:id, :name, NOW())"
        ), {'id': dept_id, 'name': dept_name})
    print("Departments created")

    # Create jobs
    for job in jobs:
        conn.execute(text(
            "INSERT INTO job_postings (id, title, description, requirements, department_id, "
            "created_by_id, hr_id, interviewer_id, location, employment_type, "
            "salary_min, salary_max, experience_years, required_skills, status, created_at, updated_at) "
            "VALUES (:id, :title, :desc, :req, :dept, :hr, :hr, :int, :loc, :emp, "
            ":smin, :smax, :exp, :skills, 'active', NOW(), NOW())"
        ), {
            'id': job['id'], 'title': job['title'], 'desc': job['description'],
            'req': job['requirements'], 'dept': job['department_id'],
            'hr': HR_ID, 'int': INTERVIEWER_ID,
            'loc': job['location'], 'emp': job['employment_type'],
            'smin': job['salary_min'], 'smax': job['salary_max'],
            'exp': job['experience_years'], 'skills': job['required_skills'],
        })
        print(f"Created job: {job['title']}")

    # Create interviews (tests) with questions
    for i, job in enumerate(jobs):
        interview_id = i + 100
        title = job['title']

        conn.execute(text(
            "INSERT INTO interviews (id, title, job_position, job_title, job_id, "
            "interview_type, difficulty_level, duration_minutes, status, "
            "created_by, assigned_hr_id, created_at, updated_at) "
            "VALUES (:id, :title, :pos, :jtitle, :jid, 'online_test', 'Medium', 30, "
            "'READY', :hr, :hr, NOW(), NOW())"
        ), {
            'id': interview_id, 'title': f'{title} Test',
            'pos': title, 'jtitle': title, 'jid': job['id'],
            'hr': HR_ID,
        })
        print(f"  Created test #{interview_id}: {title} Test")

        qs = questions_data[title]
        for qi, q in enumerate(qs):
            conn.execute(text(
                "INSERT INTO interview_questions (id, interview_id, question_text, question_type, "
                "options, correct_answer, difficulty_level, category, points, "
                "time_limit_minutes, created_at) "
                "VALUES (:id, :iid, :text, 'multiple_choice', :opts, :ans, :diff, :cat, "
                "10, 5, NOW())"
            ), {
                'id': str(uuid.uuid4()), 'iid': interview_id,
                'text': q['text'], 'opts': json.dumps(q['opts']),
                'ans': q['ans'], 'diff': q['diff'], 'cat': q['cat'],
            })
        print(f"    Added {len(qs)} questions")

    conn.commit()
    print("\nDone! 5 jobs + 5 tests with 25 questions created.")
    print("\nJob IDs:")
    for job in jobs:
        print(f"  {job['title']}: {job['id']}")
    print(f"\nTest IDs: 100, 101, 102, 103, 104")
    print(f"\nLogin as candidate (swati@gmail.com) to apply and test.")
