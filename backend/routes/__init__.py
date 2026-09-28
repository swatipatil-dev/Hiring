"""
📁 ROUTES PACKAGE INITIALIZATION
Imports all route modules for easy registration
"""

from .auth_routes import router as auth_router
from .candidate_profile_routes import router as candidate_profile_router
from .candidate_routes import router as candidate_router
from .hr_routes import router as hr_router
from .interviewer_routes import router as interviewer_router
from .enhanced_interviewer_routes import router as enhanced_interviewer_router
from .admin_routes import router as admin_router
from .ats_routes import router as ats_router
from .database_management_routes import router as database_management_router
from .advanced_features_routes import router as advanced_router
from .ats_explain_routes import router as ats_explain_router

__all__ = [
    "auth_router",
    "candidate_router",
    "candidate_profile_router",
    "hr_router",
    "interviewer_router",
    "enhanced_interviewer_router",
    "admin_router",
    "ats_router",
    "database_management_router",
    "advanced_router",
    "ats_explain_router",
]
