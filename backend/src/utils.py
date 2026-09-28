"""
🔧 UTILS AND HELPERS
Common utility functions and helpers
"""

import os
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from pathlib import Path
import re

from .constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

# Configure logging
logger = logging.getLogger(__name__)

def generate_uuid() -> str:
    """Generate a UUID string"""
    return str(uuid.uuid4())

def format_datetime(dt: datetime) -> str:
    """Format datetime for API responses"""
    return dt.isoformat()

def create_api_response(success: bool = True, message: str = "", data: Any = None, 
                     error_code: int = 200) -> Dict[str, Any]:
    """Create standardized API response"""
    response = {
        "success": success,
        "message": message,
        "data": data,
        "timestamp": datetime.now().isoformat()
    }
    
    if not success:
        response["error_code"] = error_code
    
    return response

def validate_email(email: str) -> bool:
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def sanitize_string(input_str: str) -> str:
    """Sanitize string for database storage"""
    if not input_str:
        return ""
    
    # Remove potentially harmful characters
    dangerous_chars = ["<", ">", "&", '"', "'", "\\", "/"]
    for char in dangerous_chars:
        input_str = input_str.replace(char, "")
    
    return input_str.strip()

def paginate_query(query, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE):
    """Add pagination to database query"""
    offset = (page - 1) * page_size
    return query.offset(offset).limit(page_size)

def log_api_request(endpoint: str, method: str = "GET", user_id: str = None, 
                 details: Dict[str, Any] = None):
    """Log API request for debugging"""
    log_message = f"{method} {endpoint}"
    if user_id:
        log_message += f" (User: {user_id})"
    if details:
        log_message += f" Details: {json.dumps(details)}"
    
    logger.info(log_message)

def handle_database_error(error: Exception, operation: str = "database operation") -> Dict[str, Any]:
    """Handle database errors consistently"""
    error_message = str(error)
    logger.error(f"Database {operation} failed: {error_message}")
    
    return create_api_response(
        success=False,
        message=f"Database {operation} failed",
        error_code=500
    )

def validate_file_upload(file: Any, max_size: int = 10 * 1024 * 1024) -> Dict[str, Any]:
    """Validate uploaded file"""
    if not file:
        return {"valid": False, "error": "No file provided"}
    
    if file.size > max_size:
        return {"valid": False, "error": f"File size exceeds {max_size} bytes"}
    
    # Check file extension
    allowed_extensions = ['.pdf', '.doc', '.docx']
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        return {"valid": False, "error": f"File type {file_ext} not allowed"}
    
    return {"valid": True}

def save_file_securely(file: Any, upload_dir: str = "uploads") -> str:
    """Save an uploaded file to the configured storage backend (local FS or GCS).

    Returns the storage *key* (e.g. "resumes/<uuid>.pdf") — the same relative-path
    form used before, so DB columns and served URLs are unchanged."""
    from src.services.storage import storage
    file_ext = Path(file.filename).suffix.lower()
    key = f"{upload_dir.strip('/')}/{generate_uuid()}{file_ext}"
    return storage.save_fileobj(file.file, key)

def extract_text_with_diagnostics(file_path: str) -> dict:
    """Like extract_text_from_file, but also reports what happened.

    Returns {text, confidence, method, pages, error}. `pages` lets the parse-quality check
    spot a scanned PDF (pages but almost no text), and `error` separates "the file could
    not be read" from "the file had no words in it". Both used to surface as an empty
    string and a silent ATS score of 0.
    """
    from src.services.storage import as_local_path
    out = {"text": "", "confidence": 0.0, "method": None, "pages": None, "error": None}
    try:
        file_ext = Path(file_path).suffix.lower()
        with as_local_path(file_path) as local_path:
            if file_ext == '.pdf':
                import pdfplumber
                with pdfplumber.open(local_path) as pdf:
                    out["pages"] = len(pdf.pages)
                    out["text"] = "".join((page.extract_text() or "") for page in pdf.pages)
                out["method"] = "pdfplumber"
                out["confidence"] = 0.9 if out["text"] else 0.0
            elif file_ext in ('.doc', '.docx'):
                import docx
                doc = docx.Document(local_path)
                out["text"] = "\n".join(p.text for p in doc.paragraphs)
                out["method"] = "python-docx"
                out["confidence"] = 0.9 if out["text"] else 0.0
            elif file_ext == '.txt':
                with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
                    out["text"] = f.read()
                out["method"] = "text"
                out["confidence"] = 0.8 if out["text"] else 0.0
            else:
                out["error"] = f"unsupported file type {file_ext or '(none)'}"
    except Exception as e:
        logger.error(f"Error extracting text from {file_path}: {e}")
        out["error"] = str(e)[:300]
        out["text"], out["confidence"] = "", 0.0
    return out


def extract_text_from_file(file_path: str) -> tuple[str, float]:
    """Extract text from a PDF/DOCX/TXT file referenced by a storage key or path."""
    from src.services.storage import as_local_path
    try:
        file_ext = Path(file_path).suffix.lower()

        # Materialise a real local path (no-op for local storage / temp files;
        # downloads from GCS when needed) for libraries that need a filesystem path.
        with as_local_path(file_path) as local_path:
            if file_ext == '.pdf':
                import pdfplumber
                with pdfplumber.open(local_path) as pdf:
                    text = ""
                    for page in pdf.pages:
                        text += page.extract_text() or ""
                    return text, 0.9 if text else 0.0

            elif file_ext in ['.doc', '.docx']:
                try:
                    import docx
                    doc = docx.Document(local_path)
                    text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
                    return text, 0.9 if text else 0.0
                except ImportError:
                    logger.warning("python-docx not installed")
                    return "", 0.0

            elif file_ext == '.txt':
                with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                return text, 0.8 if text else 0.0

        return "", 0.0

    except Exception as e:
        logger.error(f"Error extracting text from {file_path}: {e}")
        return "", 0.0

def get_ats_interpretation(ats_score: float) -> Dict[str, Any]:
    """Get interpretation of ATS score"""
    if ats_score >= 90:
        return {"level": "Excellent", "color": "green", "description": "Highly recommended"}
    elif ats_score >= 80:
        return {"level": "Good", "color": "blue", "description": "Recommended"}
    elif ats_score >= 70:
        return {"level": "Average", "color": "yellow", "description": "Consider"}
    elif ats_score >= 60:
        return {"level": "Below Average", "color": "orange", "description": "Low priority"}
    else:
        return {"level": "Poor", "color": "red", "description": "Not recommended"}

def get_job_statistics(db_or_job_id, job_id: str = None) -> Dict[str, Any]:
    """Get statistics for a job posting. Accepts (db, job_id) or (job_id,) signature."""
    # Handle both calling conventions
    if job_id is None:
        job_id = db_or_job_id  # called as get_job_statistics(job_id)
        db = None
    else:
        db = db_or_job_id     # called as get_job_statistics(db, job_id)

    application_count = 0
    average_ats_score = None
    if db is not None:
        try:
            from sqlalchemy import func
            from src.models.models import Candidate
            result = db.query(func.count(Candidate.id), func.avg(Candidate.ats_score)).filter(
                Candidate.job_id == job_id
            ).first()
            application_count = result[0] or 0
            average_ats_score = float(result[1]) if result[1] else None
        except Exception:
            pass

    return {
        "application_count": application_count,
        "average_ats_score": average_ats_score,
        "total_applications": application_count,
        "ats_screened": 0,
        "interviews_scheduled": 0,
        "offers_made": 0,
        "hired": 0
    }

def validate_pagination_params(page: int = 1, page_size=None, max_size: int = None) -> tuple:
    """Validate pagination parameters. Returns (page, page_size) tuple."""
    if page_size is None:
        page_size = DEFAULT_PAGE_SIZE
    if max_size is not None and page_size > max_size:
        page_size = max_size
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = DEFAULT_PAGE_SIZE
    return page, page_size

def validate_sort_params(sort_by: str, valid_fields: List[str], default: str = None) -> str:
    """Validate sort parameters"""
    if sort_by not in valid_fields:
        return default or valid_fields[0]  # Default sort field
    return sort_by

def validate_order_params(order: str) -> str:
    """Validate order parameters"""
    if order.lower() not in ['asc', 'desc']:
        return 'desc'  # Default order
    return order.lower()

def create_error_response(message: str, error_code: int = 400, details: Any = None) -> Dict[str, Any]:
    """Create error response"""
    return create_api_response(
        success=False,
        message=message,
        data=details,
        error_code=error_code
    )

def create_success_response(message: str, data: Any = None) -> Dict[str, Any]:
    """Create success response"""
    return create_api_response(
        success=True,
        message=message,
        data=data
    )

def calculate_ats_distribution(scores: List[float]) -> Dict[str, Any]:
    """Calculate ATS score distribution"""
    if not scores:
        return {"distribution": {}, "average": 0, "median": 0}
    
    scores.sort()
    n = len(scores)
    
    distribution = {
        "90-100": len([s for s in scores if s >= 90]),
        "80-89": len([s for s in scores if 80 <= s < 90]),
        "70-79": len([s for s in scores if 70 <= s < 80]),
        "60-69": len([s for s in scores if 60 <= s < 70]),
        "Below 60": len([s for s in scores if s < 60])
    }
    
    average = sum(scores) / n
    median = scores[n // 2] if n % 2 == 1 else (scores[n // 2 - 1] + scores[n // 2]) / 2
    
    return {
        "distribution": distribution,
        "average": round(average, 2),
        "median": round(median, 2),
        "total": n
    }
