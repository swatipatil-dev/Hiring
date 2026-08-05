"""
🔧 PROMPT BUILDER - ENHANCED
Builds prompts for AI question generation
Enhanced with improvements from New folder (7)
"""

import json
from typing import Dict, Any, List
from datetime import datetime

from src.constants import (
    MCQ_ANGLES, ANGLE_TO_BLOOM, normalize_angles,
    CODE_MCQ_STYLES, detect_programming_languages,
)

# ── Coding-question language support ──────────────────────────────────────────
# HR picks a target language per test; the coding prompt below is written for that
# language. "sql" is handled separately (it has its own result-set shape). Anything
# else / None falls back to python (keeps legacy behaviour).
CODING_LANGUAGES = {"python", "java", "cpp", "javascript", "sql"}
_CODING_LANG_ALIASES = {"py": "python", "python3": "python", "js": "javascript",
                        "node": "javascript", "c++": "cpp", "cplusplus": "cpp"}


def normalize_coding_language(lang) -> str:
    """Map a user/HR language string to a judge key, or '' if unknown/empty."""
    l = (lang or "").strip().lower()
    l = _CODING_LANG_ALIASES.get(l, l)
    return l if l in CODING_LANGUAGES else ""


# Per-language reference snippets used to build the worked coding example. Real code
# (single braces) — these are serialised with json.dumps so the example is valid JSON.
_CODING_LANG_SNIPPETS = {
    "python": {
        "label": "Python 3",
        "note": "a runnable Python 3 program that reads from standard input (input()/sys.stdin) and prints to standard output",
        "template": "import sys\n\ndef main():\n    data = sys.stdin.read().split()\n    # TODO: parse input and print the answer\n\nmain()",
        "solution": "import sys\n\ndef main():\n    data = sys.stdin.read().split()\n    n = int(data[0])\n    nums = list(map(int, data[1:1+n]))\n    u = sorted(set(nums), reverse=True)\n    print(u[1] if len(u) > 1 else u[0])\n\nmain()",
    },
    "java": {
        "label": "Java 21",
        # CRITICAL: the judge compiles Main.java and runs `java Main`, so the class MUST be public and named Main.
        "note": "a runnable Java 21 program in a single public class named EXACTLY 'Main' (the judge compiles Main.java and runs `java Main`); read from System.in (e.g. Scanner/BufferedReader) and print to System.out",
        "template": "import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        Scanner sc = new Scanner(System.in);\n        // TODO: read input and print the answer\n    }\n}",
        "solution": "import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        Scanner sc = new Scanner(System.in);\n        int n = sc.nextInt();\n        TreeSet<Integer> set = new TreeSet<>();\n        for (int i = 0; i < n; i++) set.add(sc.nextInt());\n        Integer[] a = set.toArray(new Integer[0]);\n        System.out.println(a.length > 1 ? a[a.length - 2] : a[a.length - 1]);\n    }\n}",
    },
    "cpp": {
        "label": "C++ 17",
        "note": "a runnable C++17 program with int main() that reads from std::cin and prints to std::cout",
        "template": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    // TODO: read input and print the answer\n    return 0;\n}",
        "solution": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    int n; cin >> n;\n    set<int> s;\n    for (int i = 0; i < n; i++) { int x; cin >> x; s.insert(x); }\n    vector<int> a(s.begin(), s.end());\n    cout << (a.size() > 1 ? a[a.size() - 2] : a[a.size() - 1]) << endl;\n    return 0;\n}",
    },
    "javascript": {
        "label": "JavaScript (Node 20)",
        "note": "a runnable Node.js program that reads all of standard input (e.g. require('fs').readFileSync(0,'utf8')) and prints to standard output with console.log",
        "template": "const data = require('fs').readFileSync(0, 'utf8').split(/\\s+/).filter(Boolean);\n// TODO: parse input and print the answer",
        "solution": "const data = require('fs').readFileSync(0, 'utf8').split(/\\s+/).filter(Boolean);\nconst n = Number(data[0]);\nconst nums = data.slice(1, 1 + n).map(Number);\nconst u = [...new Set(nums)].sort((a, b) => b - a);\nconsole.log(u.length > 1 ? u[1] : u[0]);",
    },
}


def build_coding_example(lang: str) -> str:
    """A valid-JSON worked coding example (second-largest-unique) in `lang`.
    Uses single braces on purpose — it is inserted as an f-string value, so it
    renders verbatim (unlike the legacy {{-doubled}} examples)."""
    snip = _CODING_LANG_SNIPPETS.get(lang) or _CODING_LANG_SNIPPETS["python"]
    return (
        '{\n'
        '        "question_text": "Given a list of integers, output the second largest UNIQUE value.\\n\\nInput: The first line contains N. The second line contains N space-separated integers.\\nOutput: Print the second largest unique integer (or the largest if only one unique value exists).\\n\\nExample:\\nInput:\\n5\\n4 1 4 5 2\\nOutput:\\n4",\n'
        '        "question_type": "coding",\n'
        '        "difficulty_level": "Easy|Medium|Hard",\n'
        '        "category": "topic_name",\n'
        f'        "code_template": {json.dumps(snip["template"])},\n'
        f'        "sample_answer": {json.dumps(snip["solution"])},\n'
        '        "test_cases": [\n'
        '            {"input": "5\\n4 1 4 5 2", "expected_output": "4", "is_sample": true},\n'
        '            {"input": "3\\n7 7 7", "expected_output": "7", "is_sample": true},\n'
        '            {"input": "4\\n10 20 30 40", "expected_output": "30", "is_sample": false},\n'
        '            {"input": "6\\n-1 -2 -3 -1 -2 -5", "expected_output": "-2", "is_sample": false}\n'
        '        ],\n'
        '        "points": 20,\n'
        '        "time_limit_minutes": 15\n'
        '    }'
    )

class PromptBuilder:
    """Builds structured prompts for AI question generation"""
    
    def __init__(self):
        self.base_prompt = """
You are an expert technical interviewer and question generator. Generate high-quality interview questions based on the provided context.

Requirements:
1. Generate exactly {num_questions} questions
2. Mix of question types: {question_types}
3. Difficulty level: {difficulty_level}
4. Focus on skills: {skills}
5. Each question should be clear, concise, and relevant

Format your response as a JSON array with the following structure:
[
    {{
        "question_text": "The question text",
        "question_type": "multiple_choice|short_answer|coding|behavioral|technical|situational",
        "difficulty_level": "Easy|Medium|Hard",
        "options": "JSON array for multiple choice only",
        "correct_answer": "Correct answer for MCQ",
        "sample_answer": "Sample answer for open-ended questions",
        "explanation": "Brief explanation of the answer",
        "time_limit_minutes": 5,
        "points": 10
    }}
]
"""
    
    def build_interview_prompt(self, config: Dict[str, Any]) -> str:
        """Build a comprehensive interview prompt based on configuration"""
        
        # Extract configuration
        num_questions = config.get('num_questions', 10)
        difficulty = config.get('difficulty', 'Medium')
        skills = config.get('skills', [])
        question_types = config.get('question_types', ['technical'])
        
        # Build context-specific prompt
        context_prompt = f"""
## Interview Configuration
- Number of Questions: {num_questions}
- Difficulty Level: {difficulty}
- Question Types: {', '.join(question_types)}
- Focus Skills: {', '.join(skills) if skills else 'General Technical Skills'}

## Guidelines
- Generate questions appropriate for {difficulty} difficulty level
- Include a mix of {', '.join(question_types)} question types
- Focus on testing practical knowledge and problem-solving skills
- Ensure questions are clear, concise, and relevant
- For coding questions, include algorithmic thinking and problem-solving
- For behavioral questions, focus on teamwork and communication
- For technical questions, test core concepts and best practices

## Output Format
Return a JSON array with exactly {num_questions} questions following the structure above.
Each question should be complete and ready to use in an interview setting.
"""
        
        # Combine base prompt with context
        full_prompt = self.base_prompt.format(
            num_questions=num_questions,
            question_types=', '.join(question_types),
            difficulty_level=difficulty,
            skills=', '.join(skills) if skills else 'General'
        ) + context_prompt
        
        return full_prompt.strip()
    
    def build_generation_prompt(self, job_position: str, interview_type: str,
                               difficulty_level: str, question_count: int = 10,
                               question_types: List[str] = None,
                               categories: List[str] = None,
                               skills_detail: List[Dict] = None,
                               coding_focus_areas: List[str] = None,
                               avoid_topics: List[str] = None,
                               real_world_scenarios: bool = False,
                               mcq_angles: List[str] = None,
                               coding_language: str = None) -> str:
        """
        Build a simplified prompt for generating interview questions.

        coding_language: judge key (python/java/cpp/javascript/sql) that coding
        questions must be written in. '' / None / unknown -> python (legacy).
        """
        code_lang = normalize_coding_language(coding_language)

        base_prompt = """You are an expert technical interviewer. Generate high-quality interview questions."""

        context = f"""
## Job: {job_position}
## Type: {interview_type}
## Difficulty: {difficulty_level}
## Focus Areas: {', '.join(categories) if categories else 'General'}
"""
        # Add skill-wise distribution if available
        if skills_detail:
            skills_str = '\n'.join([
                f"- {s['name']}: {s.get('question_count', 2)} questions, difficulty {s.get('difficulty_override', difficulty_level)}, weight {s.get('weight', 1)}"
                for s in skills_detail if s.get('name', '').strip()
            ])
            if skills_str:
                context += f"\n## Skill-wise Distribution:\n{skills_str}\n"

        if coding_focus_areas:
            context += f"\n## Coding Focus Areas: {', '.join(coding_focus_areas)}\n"

        type_str = ', '.join(question_types) if question_types else 'technical'
        is_mcq_only = question_types and len(question_types) == 1 and question_types[0] == 'multiple_choice'
        is_coding_only = question_types and len(question_types) == 1 and question_types[0] == 'coding'
        is_case_study_only = question_types and len(question_types) == 1 and question_types[0] == 'case_study'
        is_aptitude_only = question_types and len(question_types) == 1 and question_types[0] == 'aptitude'
        is_communication_only = question_types and len(question_types) == 1 and question_types[0] == 'communication'
        is_mixed = question_types and len(question_types) > 1

        requirements = f"""Generate exactly {question_count} questions of type: {type_str}.
Keep questions concise and relevant."""

        if avoid_topics:
            requirements += f"\nDo NOT include questions about: {', '.join(avoid_topics)}."

        if real_world_scenarios:
            requirements += "\nInclude real-world practical scenarios in questions where applicable."

        if is_mcq_only:
            requirements += """
IMPORTANT: ALL questions MUST be multiple_choice type with exactly 4 options (A, B, C, D) and a correct_answer.
Do NOT generate coding, short_answer, or any other question type."""
        elif is_coding_only and code_lang == 'sql':
            requirements += """
IMPORTANT: ALL questions MUST be coding type asking the candidate to write a SQL QUERY (SQLite). Each MUST include:
- "question_text": describe the table(s) — name, columns and types — and show a small SAMPLE of the rows as a plain table, then state exactly what the query must return and show the EXPECTED OUTPUT for that sample. Do NOT put any SQL query in the example; the example must never reveal the solution.
- "code_template": "-- Write your SQL query below\\n".
- "sample_answer": the correct SQL query (a single SELECT). It is the reference solution and is graded by comparing RESULT SETS, so column order/formatting need not match a string.
- "test_cases": each "input" is a SETUP SCRIPT of valid SQLite statements that create and populate the table(s), e.g. "CREATE TABLE employees (id INTEGER, name TEXT, salary REAL);\\nINSERT INTO employees VALUES (1,'John',9000),(2,'Jane',8000);". "expected_output" is the result rows, one row per line with columns comma-separated. First 1-2 cases is_sample:true and AT LEAST 3 cases is_sample:false (hidden, with DIFFERENT seed data). The setup SCRIPT (input) is hidden from the candidate — never restate it in question_text.
Do NOT include "options" or "correct_answer". Do NOT generate multiple_choice or non-SQL questions."""
        elif is_coding_only:
            _snip = _CODING_LANG_SNIPPETS.get(code_lang or 'python', _CODING_LANG_SNIPPETS['python'])
            _lang_label = _snip['label']
            requirements += f"""
IMPORTANT: ALL questions MUST be coding type, written in {_lang_label} for STDIN/STDOUT automated judging (HackerRank/Codeforces/GUVI CodeKata style). EVERY question's "code_template" AND "sample_answer" MUST be written in {_lang_label} — do NOT use any other programming language. Each question MUST include:
- "question_text": a clear problem statement that SPECIFIES the exact INPUT format (read from standard input) and OUTPUT format (printed to standard output), then ONE worked example under a line that reads exactly "Example:" (the platform shows the sample input/output as separate labelled blocks, so put the demo only under "Example:" and keep the rest as plain prose). Do NOT name the programming language in the problem statement.
- "code_template": {_snip['note']} — a runnable STARTER, NOT a bare function signature.
- "sample_answer": a COMPLETE reference solution in {_lang_label} that reads from stdin and prints the correct answer to stdout.
- "test_cases": an array of 5-6 objects {{"input": "<exact text given on stdin>", "expected_output": "<exact text the program must print>", "is_sample": true/false}}. Make the first 1-2 cases "is_sample": true and AT LEAST 3 cases "is_sample": false (hidden, covering edge cases). EVERY case MUST be correct for "sample_answer" (running sample_answer on "input" prints exactly "expected_output", trailing whitespace ignored).
Do NOT include "options" or "correct_answer". Do NOT generate multiple_choice, short_answer, or SQL questions.
The worked example in "question_text" must show only the INPUT DATA and the EXPECTED OUTPUT — NEVER the solution code. Do not leak the answer in the problem statement.
OUTPUT MUST BE DETERMINISTIC: the program is graded by exact text match, so the output order must be fully specified. If the answer prints a collection, print it in a defined order (e.g. sorted, or in input order) and say so in the problem statement.
Each coding question should test a distinct concept (arrays, strings, math, sorting, hashing, recursion)."""
        elif is_case_study_only:
            requirements += """
IMPORTANT: ALL questions MUST be case_study type. Each MUST include:
- "question_text": a detailed real-world scenario or business case for the candidate to analyze
- "sample_answer": a comprehensive analysis and recommended approach
Do NOT include "options" or "correct_answer" fields. Each case study should present a unique scenario."""
        elif is_aptitude_only:
            requirements += """
IMPORTANT: ALL questions MUST be aptitude type (logical reasoning, quantitative, analytical).
Each MUST include "options" (4 choices) and "correct_answer" (letter A/B/C/D).
Questions should test logical thinking, mathematical reasoning, or pattern recognition."""
        elif is_communication_only:
            requirements += """
IMPORTANT: ALL questions MUST be communication type testing verbal and written communication skills.
Each MUST include a "sample_answer" with a model response. Do NOT include "options" or "correct_answer".
Questions should test: email drafting, conflict resolution, client communication, presentation skills, active listening, feedback delivery, or professional writing."""
        elif is_mixed:
            type_list = ', '.join(question_types)
            requirements += f"""
IMPORTANT: Generate a mix of these types: {type_list}.
- For multiple_choice: include "options" (4 actual option texts) and "correct_answer" (letter A/B/C/D).
- For coding: write for STDIN/STDOUT judging. Include "code_template" (reads stdin, prints stdout), "sample_answer" (a complete stdin->stdout program), and "test_cases" (array of {"input","expected_output","is_sample"} where running sample_answer on input prints expected_output; first 1-2 are is_sample:true, rest hidden). Do NOT include "options". The example in question_text must NOT reveal the solution code. For SQL/database coding questions instead: "sample_answer" is the correct SQL query, each test case "input" is a SQLite setup script (CREATE TABLE + INSERT) seeding the data, "expected_output" is the result rows (one per line, comma-separated columns), and the example shows the sample data + expected output but NEVER the query.
- For short_answer: include "sample_answer". Do NOT include "options".
- For case_study: include a detailed scenario in "question_text" and "sample_answer". Do NOT include "options".
- For aptitude: include "options" (4 choices) and "correct_answer" (letter A/B/C/D). Test logical/analytical reasoning.
- For communication: include "sample_answer" with a model response. Test email drafting, conflict resolution, or professional writing. Do NOT include "options"."""

        # Reinforce the chosen coding language for mixed generations (coding-only already
        # states it in its own block above).
        if is_mixed and code_lang and code_lang != 'sql' and 'coding' in (question_types or []):
            _lbl = _CODING_LANG_SNIPPETS.get(code_lang, _CODING_LANG_SNIPPETS['python'])['label']
            requirements += (f"\nFor any coding questions, write BOTH \"code_template\" and "
                             f"\"sample_answer\" in {_lbl} ONLY (stdin/stdout) — not any other language, and not SQL.")

        # MCQ angles: when angles are selected and MCQs are being generated, spread the
        # multiple_choice questions across the chosen cognitive angles and tag each one.
        valid_angles = normalize_angles(mcq_angles)
        mcq_in_types = (not question_types) or ('multiple_choice' in question_types)
        if valid_angles and mcq_in_types:
            angle_lines = '\n'.join(
                f'  - "{slug}" ({MCQ_ANGLES[slug]["label"]}, bloom {ANGLE_TO_BLOOM[slug]}): {MCQ_ANGLES[slug]["desc"]}'
                for slug in valid_angles
            )
            requirements += f"""
IMPORTANT (MCQ ANGLES): Distribute the multiple_choice questions as evenly as possible across these angles:
{angle_lines}
For EACH multiple_choice question, set "category" to the exact angle slug it covers (e.g. "code_debugging")
and set "bloom_taxonomy_level" to that angle's bloom level shown above. Cover every listed angle at least once
before repeating any. Snippets used in output_prediction/code_debugging/edge_cases angles must be short and self-contained."""

        # Programming-language MCQs: when the focus skills include a programming
        # language AND no explicit angles were chosen, force the multiple_choice
        # questions to be CODE-REASONING questions (code tracing, output
        # prediction, dry run, program-flow analysis) built around a short
        # snippet — instead of plain definition/theory recall.
        detected_langs = detect_programming_languages(
            [s.get('name', '') for s in skills_detail] if skills_detail else []
        )
        if detected_langs and mcq_in_types and not valid_angles:
            primary_lang = detected_langs[0]
            langs_str = ', '.join(detected_langs)
            style_lines = '\n'.join(
                f'  - "{slug}" ({CODE_MCQ_STYLES[slug]["label"]}): {CODE_MCQ_STYLES[slug]["desc"]}'
                for slug in CODE_MCQ_STYLES
            )
            requirements += f"""
IMPORTANT (CODE-BASED MCQs): The focus skills include programming language(s): {langs_str}.
Therefore EVERY multiple_choice question MUST be a CODE-REASONING question built around a SHORT, self-contained {primary_lang} code snippet embedded inside "question_text" (use \\n for newlines; keep each snippet under 15 lines and syntactically valid {primary_lang}). Do NOT ask plain definition / theory / "which statement is true" questions.
Distribute the questions as evenly as possible across these four styles, and set each question's "category" to the exact style slug it uses:
{style_lines}
For EACH question provide exactly 4 options where one is correct and the other three are realistic distractors (e.g. an off-by-one result, a wrong-branch result, a common misconception). Put the letter (A/B/C/D) in "correct_answer". Cover every style at least once before repeating any."""

        mcq_example = """{{
        "question_text": "Clear question text",
        "question_type": "multiple_choice",
        "difficulty_level": "Easy|Medium|Hard",
        "category": "topic_name",
        "options": ["Option A text", "Option B text", "Option C text", "Option D text"],
        "correct_answer": "A",
        "points": 10,
        "time_limit_minutes": 5
    }}"""

        coding_example = """{{
        "question_text": "Given a list of integers, output the second largest UNIQUE value.\\n\\nInput: The first line contains N. The second line contains N space-separated integers.\\nOutput: Print the second largest unique integer (or the largest if only one unique value exists).\\n\\nExample:\\nInput:\\n5\\n4 1 4 5 2\\nOutput:\\n4",
        "question_type": "coding",
        "difficulty_level": "Easy|Medium|Hard",
        "category": "topic_name",
        "code_template": "import sys\\n\\ndef main():\\n    data = sys.stdin.read().split()\\n    # TODO: parse input and print the answer\\n\\nmain()",
        "sample_answer": "import sys\\n\\ndef main():\\n    data = sys.stdin.read().split()\\n    n = int(data[0])\\n    nums = list(map(int, data[1:1+n]))\\n    u = sorted(set(nums), reverse=True)\\n    print(u[1] if len(u) > 1 else u[0])\\n\\nmain()",
        "test_cases": [
            {{"input": "5\\n4 1 4 5 2", "expected_output": "4", "is_sample": true}},
            {{"input": "3\\n7 7 7", "expected_output": "7", "is_sample": true}},
            {{"input": "4\\n10 20 30 40", "expected_output": "30", "is_sample": false}},
            {{"input": "6\\n-1 -2 -3 -1 -2 -5", "expected_output": "-2", "is_sample": false}}
        ],
        "points": 20,
        "time_limit_minutes": 15
    }}"""

        sql_coding_example = """{{
        "question_text": "Table 'employees' has columns id (INT), name (TEXT), salary (REAL).\\n\\nSample data:\\nid | name | salary\\n1  | John | 9000\\n2  | Jane | 8000\\n3  | Bob  | 3000\\n\\nReturn the names of all employees whose salary is greater than the average salary of all employees.\\n\\nExpected output for the sample data:\\nJohn\\nJane",
        "question_type": "coding",
        "difficulty_level": "Medium",
        "category": "sql",
        "code_template": "-- Write your SQL query below\\n",
        "sample_answer": "SELECT name FROM employees WHERE salary > (SELECT AVG(salary) FROM employees);",
        "test_cases": [
            {{"input": "CREATE TABLE employees (id INTEGER, name TEXT, salary REAL);\\nINSERT INTO employees VALUES (1,'John',9000),(2,'Jane',8000),(3,'Bob',3000);", "expected_output": "John\\nJane", "is_sample": true}},
            {{"input": "CREATE TABLE employees (id INTEGER, name TEXT, salary REAL);\\nINSERT INTO employees VALUES (1,'Al',5000),(2,'Bea',5000);", "expected_output": "", "is_sample": false}},
            {{"input": "CREATE TABLE employees (id INTEGER, name TEXT, salary REAL);\\nINSERT INTO employees VALUES (1,'Sam',100),(2,'Tom',200),(3,'Uma',9000);", "expected_output": "Uma", "is_sample": false}}
        ],
        "points": 20,
        "time_limit_minutes": 15
    }}"""

        short_answer_example = """{{
        "question_text": "Explain the difference between REST and GraphQL APIs.",
        "question_type": "short_answer",
        "difficulty_level": "Easy|Medium|Hard",
        "category": "topic_name",
        "sample_answer": "REST uses fixed endpoints with HTTP methods while GraphQL uses a single endpoint with a query language for flexible data fetching.",
        "points": 10,
        "time_limit_minutes": 5
    }}"""

        case_study_example = """{{
        "question_text": "A mid-size e-commerce company is experiencing 10x traffic spikes during flash sales, causing their monolithic application to crash. Their current stack uses a single PostgreSQL database and a Node.js backend. Analyze the situation and propose a scalable architecture.",
        "question_type": "case_study",
        "difficulty_level": "Easy|Medium|Hard",
        "category": "topic_name",
        "sample_answer": "The recommended approach involves: 1) Breaking the monolith into microservices for independent scaling, 2) Adding a CDN and caching layer (Redis) for static assets and hot data, 3) Implementing a message queue (RabbitMQ/Kafka) to handle order processing asynchronously, 4) Using read replicas for the database and considering horizontal sharding for the product catalog.",
        "points": 15,
        "time_limit_minutes": 10
    }}"""

        aptitude_example = """{{
        "question_text": "If a project requires 5 developers working 8 hours/day to complete in 10 days, how many developers are needed to complete it in 5 days assuming the same 8-hour workday?",
        "question_type": "aptitude",
        "difficulty_level": "Easy|Medium|Hard",
        "category": "logical_reasoning",
        "options": ["8 developers", "10 developers", "12 developers", "15 developers"],
        "correct_answer": "B",
        "points": 5,
        "time_limit_minutes": 3
    }}"""

        communication_example = """{{
        "question_text": "You receive an email from a frustrated client who claims your team missed a deadline. The client is threatening to cancel the contract. Draft a professional response that acknowledges their concern, explains the situation, and proposes a resolution.",
        "question_type": "communication",
        "difficulty_level": "Easy|Medium|Hard",
        "category": "written_communication",
        "sample_answer": "Dear [Client], Thank you for bringing this to our attention. I understand your frustration regarding the missed deadline, and I sincerely apologize for the inconvenience. After reviewing the situation, [explain reason]. To resolve this, I propose [solution with timeline]. I value our partnership and want to ensure we meet your expectations going forward. Please let me know a convenient time to discuss this further.",
        "points": 10,
        "time_limit_minutes": 8
    }}"""

        # Language-aware coding example: SQL keeps its result-set shape, everything
        # else is a stdin/stdout program in the chosen language (python if unset).
        lang_coding_example = sql_coding_example if code_lang == 'sql' else build_coding_example(code_lang or 'python')

        # Select examples based on mode
        if is_mcq_only:
            example_block = mcq_example
        elif is_coding_only:
            example_block = lang_coding_example
        elif is_case_study_only:
            example_block = case_study_example
        elif is_aptitude_only:
            example_block = aptitude_example
        elif is_communication_only:
            example_block = communication_example
        else:
            parts = []
            if 'multiple_choice' in (question_types or []):
                parts.append(mcq_example)
            if 'coding' in (question_types or []):
                parts.append(lang_coding_example)
            if 'short_answer' in (question_types or []):
                parts.append(short_answer_example)
            if 'case_study' in (question_types or []):
                parts.append(case_study_example)
            if 'aptitude' in (question_types or []):
                parts.append(aptitude_example)
            if 'communication' in (question_types or []):
                parts.append(communication_example)
            example_block = ',\n    '.join(parts) if parts else mcq_example

        output_format = f"""Return JSON array:
[
    {example_block}
]"""
        
        prompt = f"{base_prompt}\n\n{context}\n\n{requirements}\n\n{output_format}"
        return prompt.strip()
    
    def _get_base_system_prompt(self) -> str:
        """Get the base system prompt for question generation"""
        return """You are an expert technical interviewer and question generator. 
Generate high-quality, relevant interview questions based on the provided job requirements and interview configuration.

Your questions should:
- Be appropriate for the specified difficulty level
- Test relevant skills and knowledge areas
- Be clear, concise, and professional
- Follow best practices for technical interviews
- Include appropriate difficulty progression
- Be suitable for the specified interview type

Always return your response in valid JSON format."""
    
    def build_custom_prompt(self, custom_config: Dict[str, Any]) -> str:
        """Build a custom prompt based on specific configuration"""
        
        system_prompt = self._get_base_system_prompt()
        
        custom_context = f"""
## Custom Interview Configuration
{json.dumps(custom_config, indent=2)}
"""
        
        instructions = """
Based on the above configuration, generate appropriate interview questions.
Ensure all questions match the specified requirements and are returned in valid JSON format.
"""
        
        return f"{system_prompt}\n\n{custom_context}\n\n{instructions}"

    def build_enhanced_interview_prompt(self, config: Dict[str, Any]) -> str:
        """Build enhanced prompt for detailed question generation"""
        
        system_prompt = """
You are an expert technical interviewer and question generator. Generate high-quality, detailed interview questions based on the provided context.

Requirements:
1. Generate exactly {question_count} questions
2. Question types: {question_types}
3. Difficulty distribution: {difficulty_distribution}
4. Categories: {categories}
5. Interview type: {interview_type}

Each question should include:
- Clear question text
- Appropriate difficulty level
- Category classification
- Bloom's taxonomy level
- Time limit and points
- Options for multiple choice questions
- Sample answers for open-ended questions
- Code templates for coding questions

Format your response as a JSON array with the following structure:
[
    {{
        "question_text": "The complete question text",
        "question_type": "multiple_choice|short_answer|coding|behavioral|technical|situational",
        "difficulty_level": "Easy|Medium|Hard",
        "category": "specific category name",
        "bloom_taxonomy_level": "Remember|Understand|Apply|Analyze|Evaluate|Create",
        "time_limit_minutes": 5,
        "points": 10,
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "correct_answer": "Correct option",
        "sample_answer": "Sample answer for reference",
        "code_template": "# Code template if applicable"
    }}
]
"""
        
        # Format the prompt with configuration
        formatted_prompt = system_prompt.format(
            question_count=config.get('question_count', 5),
            question_types=', '.join(config.get('question_types', ['multiple_choice'])),
            difficulty_distribution=config.get('difficulty_distribution', {}),
            categories=', '.join(config.get('categories', ['General'])),
            interview_type=config.get('interview_type', 'technical')
        )
        
        instructions = """
Based on the above configuration, generate appropriate interview questions.
Ensure all questions match the specified requirements and are returned in valid JSON format.
Make questions realistic and relevant to actual technical interviews.
"""
        
        return f"{formatted_prompt}\n\n{instructions}"

    def build_cover_letter_prompt(self, config: Dict[str, Any]) -> str:
        """
        Build a prompt that produces a tailored cover letter as plain prose.

        Expected config keys:
          candidate_name, headline, about, skills (list),
          work_experience (list of {title, company} dicts),
          job_title, company_name, job_description, tone
        """
        tone_map = {
            "professional": "professional and confident",
            "enthusiastic": "warm and enthusiastic",
            "concise": "concise and direct",
        }
        tone = tone_map.get((config.get("tone") or "professional"), "professional and confident")

        name = config.get("candidate_name") or "the candidate"
        headline = config.get("headline") or ""
        about = (config.get("about") or "")[:800]
        skills = ", ".join((config.get("skills") or [])[:12]) or "not specified"

        exp_lines = []
        for w in (config.get("work_experience") or [])[:4]:
            title = (w.get("title") or w.get("job_title") or "").strip()
            company = (w.get("company") or w.get("company_name") or "").strip()
            if title or company:
                exp_lines.append(f"- {title} at {company}".strip())
        experience = "\n".join(exp_lines) or "Not provided"

        job_title = config.get("job_title") or "the role"
        company_name = config.get("company_name") or "the company"
        job_description = (config.get("job_description") or "")[:1500]

        return f"""You are an expert career writer. Write a tailored cover letter for a job applicant.

APPLICANT
Name: {name}
Headline: {headline}
Summary: {about}
Key skills: {skills}
Recent experience:
{experience}

TARGET ROLE
Position: {job_title}
Company: {company_name}
Job description:
{job_description}

INSTRUCTIONS
- Write a {tone} cover letter of roughly 180-280 words.
- Write in the first person, addressed to the hiring team.
- Connect the applicant's skills and experience to the specific role and company.
- Be specific and genuine; do NOT invent facts, employers, or achievements not implied above.
- Return ONLY the cover letter body as plain text prose. No markdown, no headings, no bullet points, no placeholders like [Your Name], no preamble or explanation.
"""

# Global instance
prompt_builder = PromptBuilder()
