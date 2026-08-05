"""
🚨 CUSTOM EXCEPTIONS
Custom exception classes for better error handling and debugging
"""

from typing import Optional, Dict, Any
from fastapi import HTTPException


class BaseCustomException(Exception):
    """Base custom exception class"""
    
    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        status_code: int = 500
    ):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.status_code = status_code
        super().__init__(self.message)


class ValidationError(BaseCustomException):
    """Raised when validation fails"""
    
    def __init__(self, message: str, field: Optional[str] = None, value: Optional[Any] = None):
        details = {}
        if field:
            details['field'] = field
        if value is not None:
            details['value'] = str(value)
        super().__init__(
            message=message,
            error_code="VALIDATION_ERROR",
            details=details,
            status_code=400
        )


class FileProcessingError(BaseCustomException):
    """Raised when file processing fails"""
    
    def __init__(
        self,
        message: str,
        filename: Optional[str] = None,
        file_type: Optional[str] = None,
        file_size: Optional[int] = None
    ):
        details = {}
        if filename:
            details['filename'] = filename
        if file_type:
            details['file_type'] = file_type
        if file_size is not None:
            details['file_size'] = file_size
        super().__init__(
            message=message,
            error_code="FILE_PROCESSING_ERROR",
            details=details,
            status_code=400
        )


class TextExtractionError(BaseCustomException):
    """Raised when text extraction fails"""
    
    def __init__(
        self,
        message: str,
        file_path: Optional[str] = None,
        extraction_method: Optional[str] = None,
        confidence: Optional[float] = None
    ):
        details = {}
        if file_path:
            details['file_path'] = file_path
        if extraction_method:
            details['extraction_method'] = extraction_method
        if confidence is not None:
            details['confidence'] = confidence
        super().__init__(
            message=message,
            error_code="TEXT_EXTRACTION_ERROR",
            details=details,
            status_code=400
        )


class ATSScoringError(BaseCustomException):
    """Raised when ATS scoring fails"""
    
    def __init__(
        self,
        message: str,
        resume_text_length: Optional[int] = None,
        job_description_length: Optional[int] = None,
        scoring_factors: Optional[Dict[str, float]] = None
    ):
        details = {}
        if resume_text_length is not None:
            details['resume_text_length'] = resume_text_length
        if job_description_length is not None:
            details['job_description_length'] = job_description_length
        if scoring_factors:
            details['scoring_factors'] = scoring_factors
        super().__init__(
            message=message,
            error_code="ATS_SCORING_ERROR",
            details=details,
            status_code=500
        )


class DatabaseError(BaseCustomException):
    """Raised when database operations fail"""
    
    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        table: Optional[str] = None,
        query_params: Optional[Dict[str, Any]] = None
    ):
        details = {}
        if operation:
            details['operation'] = operation
        if table:
            details['table'] = table
        if query_params:
            details['query_params'] = query_params
        super().__init__(
            message=message,
            error_code="DATABASE_ERROR",
            details=details,
            status_code=500
        )


class AuthenticationError(BaseCustomException):
    """Raised when authentication fails"""
    
    def __init__(self, message: str, user_id: Optional[str] = None, role: Optional[str] = None):
        details = {}
        if user_id:
            details['user_id'] = user_id
        if role:
            details['role'] = role
        super().__init__(
            message=message,
            error_code="AUTHENTICATION_ERROR",
            details=details,
            status_code=401
        )


class AuthorizationError(BaseCustomException):
    """Raised when authorization fails"""
    
    def __init__(
        self,
        message: str,
        user_id: Optional[str] = None,
        required_role: Optional[str] = None,
        resource: Optional[str] = None
    ):
        details = {}
        if user_id:
            details['user_id'] = user_id
        if required_role:
            details['required_role'] = required_role
        if resource:
            details['resource'] = resource
        super().__init__(
            message=message,
            error_code="AUTHORIZATION_ERROR",
            details=details,
            status_code=403
        )


class NotFoundError(BaseCustomException):
    """Raised when a resource is not found"""
    
    def __init__(self, message: str, resource_type: Optional[str] = None, resource_id: Optional[str] = None):
        details = {}
        if resource_type:
            details['resource_type'] = resource_type
        if resource_id:
            details['resource_id'] = resource_id
        super().__init__(
            message=message,
            error_code="NOT_FOUND_ERROR",
            details=details,
            status_code=404
        )


class ConflictError(BaseCustomException):
    """Raised when there's a conflict with existing data"""
    
    def __init__(
        self,
        message: str,
        conflicting_field: Optional[str] = None,
        conflicting_value: Optional[Any] = None
    ):
        details = {}
        if conflicting_field:
            details['conflicting_field'] = conflicting_field
        if conflicting_value is not None:
            details['conflicting_value'] = str(conflicting_value)
        super().__init__(
            message=message,
            error_code="CONFLICT_ERROR",
            details=details,
            status_code=409
        )


class RateLimitError(BaseCustomException):
    """Raised when rate limit is exceeded"""
    
    def __init__(
        self,
        message: str,
        limit: Optional[int] = None,
        window: Optional[int] = None,
        retry_after: Optional[int] = None
    ):
        details = {}
        if limit is not None:
            details['limit'] = limit
        if window is not None:
            details['window'] = window
        if retry_after is not None:
            details['retry_after'] = retry_after
        super().__init__(
            message=message,
            error_code="RATE_LIMIT_ERROR",
            details=details,
            status_code=429
        )


class ConfigurationError(BaseCustomException):
    """Raised when there's a configuration error"""
    
    def __init__(
        self,
        message: str,
        config_key: Optional[str] = None,
        config_value: Optional[Any] = None
    ):
        details = {}
        if config_key:
            details['config_key'] = config_key
        if config_value is not None:
            details['config_value'] = str(config_value)
        super().__init__(
            message=message,
            error_code="CONFIGURATION_ERROR",
            details=details,
            status_code=500
        )


# ==================== HTTP EXCEPTION CONVERTERS ====================

def convert_to_http_exception(exc: BaseCustomException) -> HTTPException:
    """Convert custom exception to HTTP exception"""
    return HTTPException(
        status_code=exc.status_code,
        detail={
            'error_code': exc.error_code,
            'message': exc.message,
            'details': exc.details
        }
    )


def handle_exception(exc: Exception) -> HTTPException:
    """Handle generic exception and convert to HTTP exception"""
    if isinstance(exc, BaseCustomException):
        return convert_to_http_exception(exc)
    
    # Handle generic exceptions
    return HTTPException(
        status_code=500,
        detail={
            'error_code': 'INTERNAL_SERVER_ERROR',
            'message': 'An unexpected error occurred',
            'details': {'original_error': str(exc)}
        }
    )
