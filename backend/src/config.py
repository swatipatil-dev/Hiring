"""
🔧 CONFIGURATION MANAGEMENT
Application configuration settings and environment management
"""

import os
from typing import Dict, Any, Optional

class Config:
    """Application configuration class"""
    
    def __init__(self):
        self.load_from_env()
    
    def load_from_env(self):
        """Load configuration from environment variables"""
        # NOTE: Gemini/LLM settings deliberately do NOT live here.
        # src/services/gemini_service.py reads GEMINI_API_KEY / GOOGLE_CLOUD_PROJECT /
        # GEMINI_MODEL directly and is the single source of truth. A second copy in
        # this class had no callers and normalised the values differently (no
        # .strip() on the API key), which is a trap for whoever wires it up next.
        self.database_url = os.getenv("DATABASE_URL", "mysql+pymysql://root:1234@localhost:3306/recruitment_db")
        self.secret_key = os.getenv("SECRET_KEY", "your-secret-key-here")
        self.debug_mode = os.getenv("DEBUG", "False").lower() == "true"
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
    
    def get_database_config(self) -> Dict[str, Any]:
        """Get database configuration"""
        return {
            "url": self.database_url,
            "echo": self.debug_mode,
            "pool_size": 10,
            "max_overflow": 20,
            "pool_recycle": 3600
        }
    
    def get_api_config(self) -> Dict[str, Any]:
        """Get API configuration"""
        return {
            "prefix": "/api/v1",
            "version": "2.0.0",
            "title": "HireFlow Recruitment API",
            "description": "Complete AI-powered recruitment system",
            "docs_url": "/docs",
            "cors_origins": ["*"] if not self.debug_mode else ["http://localhost:3000"],
            "page_size": 20,
            "page_limit": 100
        }

# Global config instance
config = Config()
