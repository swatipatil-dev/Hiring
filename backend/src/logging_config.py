"""
📝 ENHANCED LOGGING CONFIGURATION
Structured logging with proper error tracking and performance monitoring
"""

import logging
import sys
from typing import Any, Dict, Optional
from datetime import datetime
from pathlib import Path
import json
import traceback

from constants import LOG_LEVELS, LOG_FORMAT


class StructuredLogger:
    """Enhanced logger with structured output and error tracking"""
    
    def __init__(self, name: str, level: str = "INFO"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper()))
        
        # Create formatters
        self.formatter = logging.Formatter(LOG_FORMAT)
        self.json_formatter = JsonFormatter()
        
        # Setup handlers
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Setup console and file handlers"""
        # Console handler with standard format
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(self.formatter)
        self.logger.addHandler(console_handler)
        
        # File handler with JSON format
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        file_handler = logging.FileHandler(
            log_dir / f"app_{datetime.now().strftime('%Y%m%d')}.log"
        )
        file_handler.setFormatter(self.json_formatter)
        self.logger.addHandler(file_handler)
    
    def log_request(
        self,
        method: str,
        endpoint: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        request_id: Optional[str] = None
    ):
        """Log API request with structured data"""
        self.logger.info(
            "API Request",
            extra={
                "event_type": "api_request",
                "method": method,
                "endpoint": endpoint,
                "user_id": user_id,
                "ip_address": ip_address,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    def log_response(
        self,
        method: str,
        endpoint: str,
        status_code: int,
        duration_ms: float,
        request_id: Optional[str] = None
    ):
        """Log API response with performance metrics"""
        self.logger.info(
            "API Response",
            extra={
                "event_type": "api_response",
                "method": method,
                "endpoint": endpoint,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    def log_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ):
        """Log error with full context and traceback"""
        self.logger.error(
            f"Error: {str(error)}",
            extra={
                "event_type": "error",
                "error_type": type(error).__name__,
                "error_message": str(error),
                "traceback": traceback.format_exc(),
                "context": context or {},
                "user_id": user_id,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    def log_business_event(
        self,
        event_name: str,
        details: Dict[str, Any],
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ):
        """Log business events with structured data"""
        self.logger.info(
            f"Business Event: {event_name}",
            extra={
                "event_type": "business_event",
                "event_name": event_name,
                "details": details,
                "user_id": user_id,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    def log_performance(
        self,
        operation: str,
        duration_ms: float,
        details: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None
    ):
        """Log performance metrics"""
        self.logger.info(
            f"Performance: {operation}",
            extra={
                "event_type": "performance",
                "operation": operation,
                "duration_ms": duration_ms,
                "details": details or {},
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )


class JsonFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging"""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields if they exist
        if hasattr(record, '__dict__'):
            for key, value in record.__dict__.items():
                if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 
                              'pathname', 'filename', 'module', 'lineno', 
                              'funcName', 'created', 'msecs', 'relativeCreated', 
                              'thread', 'threadName', 'processName', 'process']:
                    log_data[key] = value
        
        return json.dumps(log_data, default=str)


class PerformanceLogger:
    """Context manager for performance logging"""
    
    def __init__(self, logger: StructuredLogger, operation: str, details: Optional[Dict[str, Any]] = None):
        self.logger = logger
        self.operation = operation
        self.details = details or {}
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.utcnow()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration_ms = (datetime.utcnow() - self.start_time).total_seconds() * 1000
            try:
                self.logger.info(
                    f"Performance: {self.operation}",
                    extra={
                        "event_type": "performance",
                        "operation": self.operation,
                        "duration_ms": duration_ms,
                        "details": self.details,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                )
            except Exception:
                self.logger.logger.info(f"Performance: {self.operation} - {duration_ms}ms")


# ==================== LOGGER INSTANCES ====================

# Create logger instances for different components
api_logger = StructuredLogger("api")
business_logger = StructuredLogger("business")
performance_logger = StructuredLogger("performance")
error_logger = StructuredLogger("errors")


# ==================== DECORATORS ====================

def log_api_calls(func):
    """Decorator to log API calls automatically"""
    def wrapper(*args, **kwargs):
        start_time = datetime.utcnow()
        try:
            result = func(*args, **kwargs)
            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            # Log successful call
            api_logger.log_response(
                method="POST",  # This should be extracted from context
                endpoint=func.__name__,
                status_code=200,
                duration_ms=duration_ms
            )
            return result
        except Exception as e:
            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            # Log error
            error_logger.log_error(
                error=e,
                context={"function": func.__name__, "args": str(args), "kwargs": str(kwargs)}
            )
            
            # Log failed call
            api_logger.log_response(
                method="POST",
                endpoint=func.__name__,
                status_code=500,
                duration_ms=duration_ms
            )
            raise
    
    return wrapper


def log_performance(operation: str, details: Optional[Dict[str, Any]] = None):
    """Decorator to log function performance"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            with PerformanceLogger(performance_logger, operation, details):
                return func(*args, **kwargs)
        return wrapper
    return decorator
