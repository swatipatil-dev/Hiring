"""
🗄️ DATABASE MANAGEMENT API ROUTES
Enhanced database management endpoints with advanced features
"""

from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from src.config.database_config_mysql import get_db, db_manager
from src.auth import require_role
from pydantic import BaseModel, Field
from datetime import datetime
import logging

# Every endpoint in this router exposes raw database state (health, structure,
# row counts, query execution, backups). These are platform-operator tools
# that must NEVER be reachable by tenant users — anonymous before this change.
# Lock the whole router down to superadmin.
router = APIRouter(
    prefix="/api/v1/database",
    tags=["Database Management"],
    dependencies=[Depends(require_role(["superadmin"]))],
)

# Pydantic Models
class DatabaseHealthResponse(BaseModel):
    status: str
    database: str
    user: str
    version: str
    timestamp: str
    connection_pool: Dict[str, Any]
    database_stats: Dict[str, Any]
    performance: Dict[str, Any]

class QueryRequest(BaseModel):
    query: str = Field(..., description="SQL query to execute")
    params: Optional[Dict[str, Any]] = Field(None, description="Query parameters")

class QueryResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]]
    columns: List[str]
    row_count: int
    execution_time: float

class BackupRequest(BaseModel):
    backup_path: Optional[str] = Field(None, description="Custom backup path")

class BackupResponse(BaseModel):
    success: bool
    backup_path: str
    timestamp: str
    file_size: Optional[int] = None

class OptimizationResponse(BaseModel):
    optimization_completed: bool
    results: Dict[str, Any]
    timestamp: str
    error: Optional[str] = None

class ConnectionStatsResponse(BaseModel):
    active_connections: int
    pool_size: int
    checked_in: int
    checked_out: int
    overflow: int
    last_health_check: Optional[str]
    engine_status: str

# ==================== HEALTH AND MONITORING ====================

@router.get("/health", response_model=DatabaseHealthResponse)
async def get_database_health():
    """Get comprehensive database health status"""
    try:
        health_status = db_manager.check_connection()
        return DatabaseHealthResponse(**health_status)
    except Exception as e:
        logging.error(f"Database health check failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database health check failed: {str(e)}"
        )

@router.get("/health/simple")
async def get_simple_health():
    """Get simple database health status"""
    try:
        status = db_manager.check_connection()
        return {
            "status": status.get("status", "unknown"),
            "database": status.get("database", "unknown"),
            "timestamp": status.get("timestamp", datetime.utcnow().isoformat())
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Health check failed: {str(e)}"
        )

@router.get("/stats", response_model=ConnectionStatsResponse)
async def get_connection_stats():
    """Get detailed connection statistics"""
    try:
        stats = db_manager.get_connection_stats()
        return ConnectionStatsResponse(**stats)
    except Exception as e:
        logging.error(f"Failed to get connection stats: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get connection stats: {str(e)}"
        )

# ==================== QUERY EXECUTION ====================

@router.post("/query", response_model=QueryResponse)
async def execute_query(
    request: QueryRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Execute raw SQL query with safety checks"""
    start_time = datetime.utcnow()
    
    # Basic SQL injection protection
    dangerous_keywords = ['DROP', 'DELETE', 'TRUNCATE', 'ALTER', 'CREATE', 'INSERT', 'UPDATE']
    query_upper = request.query.upper()
    
    for keyword in dangerous_keywords:
        if keyword in query_upper:
            raise HTTPException(
                status_code=400,
                detail=f"Dangerous SQL keyword detected: {keyword}. Only SELECT queries are allowed."
            )
    
    if not query_upper.strip().startswith('SELECT'):
        raise HTTPException(
            status_code=400,
            detail="Only SELECT queries are allowed for security reasons."
        )
    
    try:
        # Log query execution
        logging.info(f"Executing query: {request.query[:100]}...")
        logging.info(f"Parameters: {request.params}")
        
        # Execute query using enhanced database manager
        results = db_manager.execute_query(request.query, request.params)
        
        execution_time = (datetime.utcnow() - start_time).total_seconds()
        
        # Get column names from first result if available
        columns = list(results[0].keys()) if results else []
        
        response = QueryResponse(
            success=True,
            data=results,
            columns=columns,
            row_count=len(results),
            execution_time=execution_time
        )
        
        logging.info(f"Query executed successfully. Rows: {len(results)}, Time: {execution_time}s")
        return response
        
    except Exception as e:
        logging.error(f"Query execution failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Query execution failed: {str(e)}"
        )

@router.get("/tables")
async def get_database_tables(db: Session = Depends(get_db)):
    """Get list of all database tables"""
    try:
        query = """
            SELECT 
                table_name,
                table_rows,
                ROUND(((data_length + index_length) / 1024 / 1024), 2) AS size_mb
            FROM information_schema.tables 
            WHERE table_schema = 'recruitment_db'
            ORDER BY table_name
        """
        
        results = db_manager.execute_query(query)
        
        # Add row counts for each table
        for table_info in results:
            table_name = table_info['table_name']
            try:
                count_query = f"SELECT COUNT(*) FROM {table_name}"
                count_result = db_manager.execute_query(count_query)
                table_info['table_rows'] = count_result[0]['COUNT(*)'] if count_result else 0
            except Exception as e:
                logging.warning(f"Failed to get row count for {table_name}: {e}")
                table_info['table_rows'] = 0
        
        return {
            "success": True,
            "tables": results,
            "total_tables": len(results)
        }
        
    except Exception as e:
        logging.error(f"Failed to get database tables: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get database tables: {str(e)}"
        )

@router.get("/tables/{table_name}")
async def get_table_details(
    table_name: str,
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific table"""
    try:
        # Validate table name to prevent SQL injection
        if not table_name.replace('_', '').isalnum():
            raise HTTPException(
                status_code=400,
                detail="Invalid table name"
            )
        
        # Get table structure
        structure_query = f"""
            SELECT 
                column_name,
                data_type,
                is_nullable,
                column_default,
                character_maximum_length
            FROM information_schema.columns 
            WHERE table_schema = 'recruitment_db' AND table_name = '{table_name}'
            ORDER BY ordinal_position
        """
        
        structure = db_manager.execute_query(structure_query)
        
        # Get table statistics
        stats_query = f"""
            SELECT 
                COUNT(*) as row_count
            FROM {table_name}
        """
        
        stats = db_manager.execute_query(stats_query)
        row_count = stats[0]['row_count'] if stats else 0
        
        return {
            "table_name": table_name,
            "structure": structure,
            "row_count": row_count,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logging.error(f"Failed to get table details for {table_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get table details: {str(e)}"
        )

# ==================== BACKUP AND RESTORE ====================

@router.post("/backup", response_model=BackupResponse)
async def create_database_backup(
    request: BackupRequest,
    background_tasks: BackgroundTasks
):
    """Create database backup"""
    try:
        logging.info("Starting database backup...")
        
        # Resolve the destination up front so the response names the file that
        # will actually be written. The backup itself runs in the background, so
        # recomputing a path here (different directory, different timestamp)
        # would send the caller looking for a file that never exists.
        backup_path = request.backup_path or db_manager.default_backup_path()

        def backup_task():
            # backup_database raises on failure and removes any partial file, so the
            # only thing to do here is record why. The HTTP response has already been
            # sent by this point — the log is the sole record that it did not work.
            try:
                written = db_manager.backup_database(backup_path)
                logging.info(f"Database backup completed: {written}")
            except Exception as e:
                logging.error(f"Database backup FAILED, no file written: {e}")

        background_tasks.add_task(backup_task)

        return BackupResponse(
            success=True,
            backup_path=backup_path,
            timestamp=datetime.utcnow().isoformat()
        )
        
    except Exception as e:
        logging.error(f"Backup creation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Backup creation failed: {str(e)}"
        )

@router.get("/backups")
async def list_backups():
    """List available database backups"""
    try:
        import os
        import glob
        import pathlib
        
        # Look where backups are actually written. This used to glob "." for
        # "backup_recruitment_db_*.sql" — the process working directory, which varies
        # between systemd, cron and a shell, and the local dev database name rather than
        # the deployed one. It could never list a real backup on any deployment.
        backup_dir = os.path.dirname(db_manager.default_backup_path())
        backup_pattern = "backup_*.sql"
        backup_files = []
        
        for backup_file in glob.glob(os.path.join(backup_dir, backup_pattern)):
            try:
                stat = os.stat(backup_file)
                backup_files.append({
                    "filename": os.path.basename(backup_file),
                    "size": stat.st_size,
                    "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
            except Exception as e:
                logging.warning(f"Failed to get backup file info: {e}")
        
        # Sort by creation time (newest first)
        backup_files.sort(key=lambda x: x['created'], reverse=True)
        
        return {
            "success": True,
            "backups": backup_files,
            "total_backups": len(backup_files)
        }
        
    except Exception as e:
        logging.error(f"Failed to list backups: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list backups: {str(e)}"
        )

# ==================== OPTIMIZATION ====================

@router.post("/optimize", response_model=OptimizationResponse)
async def optimize_database():
    """Optimize database performance"""
    try:
        logging.info("Starting database optimization...")
        
        results = db_manager.optimize_database()
        
        return OptimizationResponse(**results)
        
    except Exception as e:
        logging.error(f"Database optimization failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database optimization failed: {str(e)}"
        )

@router.get("/performance")
async def get_performance_metrics():
    """Get database performance metrics"""
    try:
        # Get performance statistics
        performance_query = """
            SELECT 
                table_name,
                ROUND(((data_length + index_length) / 1024 / 1024), 2) AS size_mb,
                table_rows
            FROM information_schema.tables 
            WHERE table_schema = 'recruitment_db'
            ORDER BY ((data_length + index_length)) DESC
        """
        
        tables = db_manager.execute_query(performance_query)
        
        # Calculate total database size
        total_size = sum(table['size_mb'] for table in tables if table['size_mb'])
        total_rows = sum(table['table_rows'] for table in tables if table['table_rows'])
        
        # Get connection stats
        conn_stats = db_manager.get_connection_stats()
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "database_stats": {
                "total_size_mb": round(total_size, 2),
                "total_rows": total_rows,
                "table_count": len(tables),
                "largest_table": tables[0] if tables else None
            },
            "connection_stats": conn_stats,
            "tables": tables[:10],  # Top 10 largest tables
            "performance_indicators": {
                "avg_table_size_mb": round(total_size / len(tables), 2) if tables else 0,
                "tables_over_100mb": len([t for t in tables if t.get('size_mb', 0) > 100]),
                "total_connections": conn_stats.get('active_connections', 0)
            }
        }
        
    except Exception as e:
        logging.error(f"Failed to get performance metrics: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get performance metrics: {str(e)}"
        )

# ==================== MAINTENANCE ====================

@router.post("/maintenance/clear-connections")
async def clear_connections():
    """Clear all database connections"""
    try:
        db_manager.close_all_connections()
        return {
            "success": True,
            "message": "All database connections cleared",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logging.error(f"Failed to clear connections: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear connections: {str(e)}"
        )

@router.post("/maintenance/reconnect")
async def reconnect_database():
    """Reconnect to database"""
    try:
        # Close existing connections
        db_manager.close_all_connections()
        
        # Reinitialize
        db_manager.initialize_engine()
        
        # Test new connection
        health = db_manager.check_connection()
        
        return {
            "success": health.get("status") == "healthy",
            "message": "Database reconnected successfully" if health.get("status") == "healthy" else "Database reconnection failed",
            "health_status": health,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logging.error(f"Database reconnection failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Database reconnection failed: {str(e)}"
        )

@router.get("/info")
async def get_database_info():
    """Get comprehensive database information"""
    try:
        info = db_manager.get_database_info()
        conn_stats = db_manager.get_connection_stats()
        
        return {
            "database_info": info,
            "connection_stats": conn_stats,
            "features": {
                "connection_pooling": True,
                "query_execution": True,
                "backup_capability": True,
                "optimization_support": True,
                "health_monitoring": True,
                "transaction_support": True
            },
            "configuration": {
                "pool_size": 20,
                "max_overflow": 30,
                "pool_recycle": 3600,
                "connection_timeout": 60,
                "sql_mode": "STRICT_TRANS_TABLES"
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logging.error(f"Failed to get database info: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get database info: {str(e)}"
        )
