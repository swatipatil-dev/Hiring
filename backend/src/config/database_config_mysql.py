"""
🗄️ ENHANCED DATABASE CONFIGURATION - MYSQL
Improved MySQL database configuration with advanced connection management,
pooling, caching, and better error handling for recruitment_db
"""

import os
import pymysql

# Tolerant loader: the judge worker runs as a different user that cannot read the
# 0600 .env, and systemd has already injected its contents. See src/env_boot.py.
from src.env_boot import load_env

load_env()
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool, QueuePool
from sqlalchemy.exc import SQLAlchemyError, DisconnectionError
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import json
import threading
import time

# Configure logging
logger = logging.getLogger(__name__)

# Connection URL: prefer DATABASE_URL (e.g. Cloud SQL / any managed MySQL) and fall back
# to a local MySQL default. Locally you can override just the password via DB_PASSWORD.
# Cloud SQL example (unix socket): mysql+pymysql://USER:PASS@/recruitment_db?unix_socket=/cloudsql/PROJECT:REGION:INSTANCE
import urllib.parse

_db_password = os.getenv("DB_PASSWORD", "1234")
_quoted_db_password = urllib.parse.quote_plus(_db_password)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"mysql+pymysql://root:{_quoted_db_password}@localhost:3306/recruitment_db",
)


# Bootstrapping (CREATE DATABASE + CREATE USER) only makes sense on a self-managed local
# MySQL. On any managed host — Cloud SQL, or a cPanel account where the database is
# created through the control panel and root-level DDL/GRANT is not permitted — the
# database already exists, so set DB_BOOTSTRAP=0 there.
_DB_BOOTSTRAP = os.getenv("DB_BOOTSTRAP", "1").lower() in ("1", "true", "yes")


def _quote_ident(name: str) -> str:
    """Backtick-quote a MySQL identifier, doubling any embedded backtick.

    Schema names come from DATABASE_URL and cannot be passed as bind parameters
    in DDL, so they are interpolated — quoting keeps that safe and also allows
    names that need it (cPanel's `acct_hireflow` is fine, but a hyphen or a
    reserved word would not be).
    """
    return "`" + str(name).replace("`", "``") + "`"

class DatabaseManager:
    """Enhanced MySQL database manager with connection pooling and caching"""
    
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self.connection_pool = None
        self._connection_cache = {}
        self._lock = threading.Lock()
        self._last_health_check = None
        self._connection_count = 0
        self.initialize_engine()
    
    def initialize_engine(self):
        """Initialize MySQL database engine with enhanced settings"""
        try:
            self.engine = create_engine(
                DATABASE_URL,
                pool_pre_ping=True,
                pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "3600")),
                echo=False,  # Set to True for SQL logging
                pool_size=int(os.getenv("DB_POOL_SIZE", "20")),  # smaller on serverless (e.g. 5)
                max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "30")),  # smaller on serverless (e.g. 2)
                poolclass=QueuePool,  # Better connection management
                connect_args={
                    "charset": "utf8mb4",
                    "autocommit": False,  # Better transaction management
                    "connect_timeout": 60,
                    "read_timeout": 60,
                    "write_timeout": 60,
                    "sql_mode": "STRICT_TRANS_TABLES",
                    "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"
                }
            )
            
            # Add connection event listeners
            @event.listens_for(self.engine, "connect")
            def receive_connect(dbapi_connection, connection_record):
                self._connection_count += 1
                logger.debug(f"New connection established. Total connections: {self._connection_count}")
            
            @event.listens_for(self.engine, "checkout")
            def receive_checkout(dbapi_connection, connection_record, connection_proxy):
                logger.debug("Connection checked out from pool")
            
            @event.listens_for(self.engine, "checkin")
            def receive_checkin(dbapi_connection, connection_record):
                logger.debug("Connection checked in to pool")
            
            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=True,
                bind=self.engine,
                expire_on_commit=True
            )
            
            logger.info("Enhanced MySQL database engine initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize enhanced MySQL engine: {e}")
            raise
    
    def get_db_session(self) -> Session:
        """Get database session with connection management"""
        if self.SessionLocal is None:
            raise RuntimeError("Database not initialized")
        
        session = self.SessionLocal()
        try:
            # Add session cleanup on close
            @event.listens_for(session, "after_commit")
            def after_commit(session):
                logger.debug("Transaction committed successfully")
            
            @event.listens_for(session, "after_rollback")
            def after_rollback(session):
                logger.debug("Transaction rolled back")
                
            return session
        except Exception as e:
            logger.error(f"Failed to create database session: {e}")
            raise
    
    def check_connection(self) -> Dict[str, Any]:
        """Enhanced database connection health check"""
        try:
            with self.engine.connect() as connection:
                # Get basic server info
                result = connection.execute(text("SELECT VERSION()"))
                version = result.fetchone()
                
                # Get connection info
                info_result = connection.execute(text("SELECT DATABASE(), USER()"))
                db_info = info_result.fetchone()
                
                # Get pool status (simplified)
                try:
                    pool_size = self.engine.pool.size() if callable(getattr(self.engine.pool, 'size', None)) else getattr(self.engine.pool, '_pool_size', 20)
                    pool_status = {
                        "size": pool_size,
                        "checked_in": getattr(self.engine.pool, 'checkedin', lambda: 0)() if callable(getattr(self.engine.pool, 'checkedin', None)) else getattr(self.engine.pool, 'checkedin', 0),
                        "checked_out": getattr(self.engine.pool, 'checkedout', lambda: 0)() if callable(getattr(self.engine.pool, 'checkedout', None)) else getattr(self.engine.pool, 'checkedout', 0),
                        "overflow": getattr(self.engine.pool, 'overflow', lambda: 0)() if callable(getattr(self.engine.pool, 'overflow', None)) else getattr(self.engine.pool, 'overflow', 0),
                        "active_connections": self._connection_count
                    }
                except Exception as pool_error:
                    pool_status = {
                        "size": 20,
                        "checked_in": 0,
                        "checked_out": 0,
                        "overflow": 0,
                        "active_connections": self._connection_count,
                        "status": "error",
                        "error": str(pool_error)
                    }
                
                # Get table statistics. DATABASE() is the schema this connection is
                # actually bound to — never hardcode it, because managed hosts impose
                # their own naming (cPanel prefixes every database with the account
                # name, e.g. "acct_hireflow"), which would silently report 0 tables.
                tables_result = connection.execute(text(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()"
                ))
                table_count = tables_result.fetchone()[0]

                # Get database size (if available)
                try:
                    size_result = connection.execute(text(
                        "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 'Size (MB)' "
                        "FROM information_schema.tables WHERE table_schema = DATABASE()"
                    ))
                    db_size = size_result.fetchone()[0] or 0
                except:
                    db_size = 0
                
                self._last_health_check = datetime.utcnow()
                
                return {
                    "status": "healthy",
                    "database": db_info[0],
                    "user": db_info[1],
                    "version": version[0],
                    "timestamp": self._last_health_check.isoformat(),
                    "connection_pool": {
                        "size": pool_status["size"],
                        "checked_in": pool_status["checked_in"],
                        "checked_out": pool_status["checked_out"],
                        "overflow": pool_status["overflow"],
                        "active_connections": self._connection_count
                    },
                    "database_stats": {
                        "table_count": table_count,
                        "size_mb": db_size
                    },
                    "performance": {
                        "pool_recycle": 3600,
                        "timeout": 60,
                        "max_overflow": 30
                    }
                }
                
        except Exception as e:
            logger.error(f"Enhanced MySQL connection check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
                "connection_pool": {
                    "status": "error",
                    "active_connections": self._connection_count
                }
            }
    
    def execute_query(self, query: str, params: Dict = None) -> List[Dict]:
        """Execute raw SQL query with error handling"""
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text(query), params or {})
                columns = result.keys()
                rows = result.fetchall()
                return [dict(zip(columns, row)) for row in rows]
        except SQLAlchemyError as e:
            logger.error(f"Query execution failed: {e}")
            logger.error(f"Query: {query}")
            logger.error(f"Params: {params}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during query execution: {e}")
            raise
    
    def execute_transaction(self, queries: List[tuple]) -> bool:
        """Execute multiple queries in a transaction"""
        try:
            with self.engine.connect() as connection:
                transaction = connection.begin()
                try:
                    for query, params in queries:
                        connection.execute(text(query), params or {})
                    transaction.commit()
                    logger.info(f"Transaction completed: {len(queries)} queries executed")
                    return True
                except Exception as e:
                    transaction.rollback()
                    logger.error(f"Transaction rolled back: {e}")
                    return False
        except SQLAlchemyError as e:
            logger.error(f"Transaction failed: {e}")
            return False
    
    def default_backup_path(self) -> str:
        """Where a backup lands when the caller doesn't specify a path.

        Exposed separately so a caller that runs the backup asynchronously can
        report the real destination up front instead of guessing at one.
        """
        # Write into BACKUP_DIR (default backend/backups/) rather than the
        # process working directory, which varies between systemd, cron and an
        # interactive shell.
        backup_dir = os.getenv("BACKUP_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "backups"
        )
        backup_dir = os.path.abspath(backup_dir)
        os.makedirs(backup_dir, exist_ok=True)
        db_name = self.engine.url.database or "database"
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return os.path.join(backup_dir, f"backup_{db_name}_{stamp}.sql")

    def backup_database(self, backup_path: str = None) -> Optional[str]:
        """Create a database backup. Returns the path written, or None on failure."""
        try:
            if not backup_path:
                backup_path = self.default_backup_path()

            with self.engine.connect() as connection:
                # Get all table data
                tables_result = connection.execute(text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
                ))
                tables = [row[0] for row in tables_result.fetchall()]

                backup_content = []
                for table in tables:
                    try:
                        table_data = connection.execute(text(f"SELECT * FROM {table}"))
                        columns = table_data.keys()
                        rows = table_data.fetchall()
                        backup_content.append(f"-- Table: {table}")
                        backup_content.append(f"-- Columns: {', '.join(columns)}")
                        for row in rows:
                            backup_content.append(f"INSERT INTO {table} VALUES {tuple(row)};")
                        backup_content.append("\n")
                    except Exception as e:
                        logger.warning(f"Failed to backup table {table}: {e}")
                
                # Write backup to file
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(f"-- Database backup: {datetime.now()}\n")
                    f.write("\n".join(backup_content))
                
                logger.info(f"Database backup created: {backup_path}")
                return backup_path

        except Exception as e:
            logger.error(f"Database backup failed: {e}")
            return None
    
    def optimize_database(self) -> Dict[str, Any]:
        """Optimize database performance"""
        try:
            optimization_results = {}
            
            with self.engine.connect() as connection:
                # Analyze tables
                tables_result = connection.execute(text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
                ))
                tables = [row[0] for row in tables_result.fetchall()]

                for table in tables:
                    try:
                        # Analyze table
                        connection.execute(text(f"ANALYZE TABLE {table}"))

                        # Get table statistics. Table name is bound, not interpolated.
                        stats_result = connection.execute(text("""
                            SELECT
                                table_name,
                                ROUND(((data_length + index_length) / 1024 / 1024), 2) AS table_size_mb,
                                table_rows
                            FROM information_schema.tables
                            WHERE table_schema = DATABASE() AND table_name = :table_name
                        """), {"table_name": table})
                        stats = stats_result.fetchone()
                        
                        optimization_results[table] = {
                            "analyzed": True,
                            "size_mb": stats[1] if stats else 0,
                            "row_count": stats[2] if stats else 0
                        }
                        
                    except Exception as e:
                        optimization_results[table] = {
                            "analyzed": False,
                            "error": str(e)
                        }
                
                # Optimize all tables. Unqualified names resolve against the
                # connection's current schema, so this works whatever the host
                # named the database.
                connection.execute(text("OPTIMIZE TABLE users, candidates, job_postings, interviews"))
                
                optimization_results["optimization_completed"] = True
                optimization_results["timestamp"] = datetime.utcnow().isoformat()
                
                logger.info("Database optimization completed")
                return optimization_results
                
        except Exception as e:
            logger.error(f"Database optimization failed: {e}")
            return {"optimization_completed": False, "error": str(e)}
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get detailed connection statistics"""
        try:
            pool_status = self.engine.pool.status() if self.engine.pool else {}
            
            return {
                "active_connections": self._connection_count,
                "pool_size": pool_status.get("size", 0),
                "checked_in": pool_status.get("checkedin", 0),
                "checked_out": pool_status.get("checkedout", 0),
                "overflow": pool_status.get("overflow", 0),
                "last_health_check": self._last_health_check.isoformat() if self._last_health_check else None,
                "engine_status": "initialized" if self.engine else "not_initialized"
            }
        except Exception as e:
            logger.error(f"Failed to get connection stats: {e}")
            return {"error": str(e)}
    
    def close_all_connections(self):
        """Close all database connections"""
        try:
            if self.engine:
                self.engine.dispose()
                self._connection_count = 0
                logger.info("All database connections closed")
        except Exception as e:
            logger.error(f"Error closing connections: {e}")
    
    def __del__(self):
        """Cleanup on object destruction"""
        self.close_all_connections()
    
    def create_database_if_not_exists(self):
        """Create the database named in DATABASE_URL if it doesn't exist.

        The name is taken from the configured URL, never hardcoded: creating
        (and later granting on) a different database than the app actually uses
        succeeds silently while accomplishing nothing.
        """
        try:
            db_name = self.engine.url.database
            if not db_name:
                logger.error("Cannot bootstrap: DATABASE_URL has no database component")
                return False

            # Connect to MySQL without specifying database
            mysql_url = f"mysql+pymysql://root:{_quoted_db_password}@localhost:3306"
            engine = create_engine(mysql_url)

            with engine.connect() as connection:
                connection.execute(text(
                    f"CREATE DATABASE IF NOT EXISTS {_quote_ident(db_name)} "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                ))
                logger.info("Database %s created or already exists!", db_name)

            engine.dispose()
            return True
            
        except Exception as e:
            logger.error(f"Failed to create database: {e}")
            return False
    
    def create_user_if_not_exists(self):
        """Create the local app user if needed, granted on the configured database."""
        try:
            db_name = self.engine.url.database
            if not db_name:
                logger.error("Cannot bootstrap user: DATABASE_URL has no database component")
                return False

            # Connect to MySQL as root
            mysql_url = f"mysql+pymysql://root:{_quoted_db_password}@localhost:3306"
            engine = create_engine(mysql_url)

            with engine.connect() as connection:
                # Check if user exists
                result = connection.execute(text("SELECT 1 FROM mysql.user WHERE User = 'ats_manager'"))
                exists = result.fetchone()

                if not exists:
                    logger.info("Creating ats_manager user...")
                    connection.execute(text("CREATE USER 'ats_manager'@'localhost' IDENTIFIED BY 'password-1234'"))
                    connection.execute(text(
                        f"GRANT ALL PRIVILEGES ON {_quote_ident(db_name)}.* "
                        "TO 'ats_manager'@'localhost'"
                    ))
                    connection.execute(text("FLUSH PRIVILEGES"))
                    logger.info("User created successfully with privileges on %s!", db_name)
                else:
                    logger.info("User ats_manager already exists!")
            
            engine.dispose()
            return True
            
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            return False
    
    def initialize_tables(self):
        """Initialize database tables"""
        try:
            with self.engine.connect() as connection:
                # Create tables
                tables_sql = [
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id VARCHAR(36) PRIMARY KEY,
                        email VARCHAR(255) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        role VARCHAR(50) NOT NULL,
                        first_name VARCHAR(100) NOT NULL,
                        last_name VARCHAR(100) NOT NULL,
                        phone VARCHAR(20),
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS departments (
                        id VARCHAR(36) PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS job_postings (
                        id VARCHAR(36) PRIMARY KEY,
                        title VARCHAR(255) NOT NULL,
                        description TEXT NOT NULL,
                        requirements TEXT NOT NULL,
                        department_id VARCHAR(36),
                        location VARCHAR(100) NOT NULL,
                        employment_type VARCHAR(50) NOT NULL,
                        salary_min DECIMAL(10,2),
                        salary_max DECIMAL(10,2),
                        experience_years INT,
                        required_skills TEXT,
                        status VARCHAR(20) DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_department_id (department_id),
                        INDEX idx_status (status),
                        INDEX idx_created_at (created_at)
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS candidates (
                        id VARCHAR(36) PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        email VARCHAR(255) NOT NULL,
                        phone VARCHAR(20),
                        current_stage VARCHAR(50) DEFAULT 'applied',
                        ats_score DECIMAL(5,2),
                        ats_level VARCHAR(20),
                        ats_color VARCHAR(7),
                        skills_match_score DECIMAL(5,2),
                        semantic_score DECIMAL(5,2),
                        experience_score DECIMAL(5,2),
                        keyword_score DECIMAL(5,2),
                        matched_skills TEXT,
                        missing_skills TEXT,
                        extracted_skills TEXT,
                        experience_years INT,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        resume_path VARCHAR(500),
                        INDEX idx_email (email),
                        INDEX idx_stage (current_stage),
                        INDEX idx_ats_score (ats_score),
                        INDEX idx_applied_at (applied_at)
                    )
                    """
                ]
                
                for table_sql in tables_sql:
                    connection.execute(text(table_sql))
                
                connection.commit()
                logger.info("MySQL tables initialized successfully!")
                return True
                
        except Exception as e:
            logger.error(f"Failed to initialize tables: {e}")
            return False
    
    def test_connection(self) -> bool:
        """Test database connection"""
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text("SELECT 1"))
                result.fetchone()
                return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
    
    def get_database_info(self) -> Dict[str, Any]:
        """Get detailed database information"""
        try:
            with self.engine.connect() as connection:
                # Get table count
                tables_result = connection.execute(text(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()"
                ))
                table_count = tables_result.fetchone()[0]

                # Get table names
                tables = connection.execute(text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
                )).fetchall()
                table_names = [table[0] for table in tables]

                return {
                    "database_type": "MySQL",
                    "database": connection.execute(text("SELECT DATABASE()")).scalar(),
                    "table_count": table_count,
                    "tables": table_names,
                    "status": "healthy"
                }
                
        except Exception as e:
            return {
                "database_type": "MySQL",
                "status": "error",
                "error": str(e)
            }

# Global database manager instance
db_manager = DatabaseManager()

def get_db():
    """Dependency to get database session (FastAPI generator pattern)"""
    db = db_manager.get_db_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    """Initialize database using ORM models (creates all missing tables)"""
    try:
        # CREATE DATABASE/USER only on a self-managed local MySQL (see _DB_BOOTSTRAP).
        # On Cloud SQL the database already exists and this DDL is not permitted.
        if _DB_BOOTSTRAP:
            db_manager.create_database_if_not_exists()
            db_manager.create_user_if_not_exists()
        else:
            logger.info("DB_BOOTSTRAP disabled — skipping CREATE DATABASE/USER (managed DB).")

        # Create all tables from ORM models (safe — skips existing tables)
        from src.models.models import Base
        Base.metadata.create_all(db_manager.engine)
        logger.info("ORM tables created/verified successfully!")

        # Test connection
        if db_manager.test_connection():
            logger.info("MySQL database initialized successfully!")
            return True
        else:
            logger.error("MySQL database initialization failed!")
            return False

    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        return False

if __name__ == "__main__":
    """Test database connection"""
    print("🗄️ Testing MySQL Database Connection")
    print("=" * 50)
    
    # Initialize database
    if init_db():
        print("✅ MySQL database setup completed!")
        
        # Check connection
        status = db_manager.check_connection()
        print(f"📊 Connection Status: {status['status']}")
        print(f"🗄️  Database: {status.get('database')}")
        print(f"👤 User: {status.get('user')}")
        print(f"📊 Version: {status.get('version')}")
        
        # Get database info
        info = db_manager.get_database_info()
        print(f"📋 Tables: {info.get('table_count', 0)}")
        print(f"📝 Table Names: {', '.join(info.get('tables', []))}")
        
    else:
        print("❌ MySQL database setup failed!")
