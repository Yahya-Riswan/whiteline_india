# main.py
from fastapi import FastAPI, HTTPException, Body
import mysql.connector
import json
import certifi
from fastapi.middleware.cors import CORSMiddleware
import re
import uuid
from typing import List, Optional
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],  
)
# --- CONFIGURATION ---
db_config = {
    "host": "gateway01.ap-southeast-1.prod.aws.tidbcloud.com", # Your TiDB Host
    "port": 4000,
    "user": "2EpER6W6BZfJXS9.root",
    "password": "R3VhEnJHRCkHG4sE",
    "database": "app_db",
    "ssl_ca": certifi.where(),
    "ssl_verify_cert": True,
    "ssl_verify_identity": True
}

class Filter(BaseModel):
    field: str
    operator: str
    value: str | int | float | bool

class QueryRequest(BaseModel):
    filters: List[Filter] = []
    sort_field: Optional[str] = None
    sort_direction: str = "ASC" # "ASC" or "DESC"
    limit: int = 100

def get_db():
    return mysql.connector.connect(**db_config)

def validate_name(name: str):
    """Security: Only allow safe table names."""
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        raise HTTPException(400, "Invalid name. Use only letters, numbers, _")
    return name

# ==========================================
# 1. COLLECTION (TABLE) OPERATIONS
# ==========================================

@app.post("/collections/{name}")
async def add_collection(name: str):
    """Creates a new Collection (Table)"""
    table = validate_name(name)
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Create table with ID and JSON Document column
        sql = f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id VARCHAR(255) PRIMARY KEY,
            doc JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        );
        """
        cursor.execute(sql)
        return {"status": "success", "message": f"Collection '{table}' created."}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.get("/collections/{name}")
async def read_collection(name: str, limit: int = 100):
    table = validate_name(name)
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"SHOW TABLES LIKE '{table}'")
        if not cursor.fetchone(): return []

        # Add LIMIT to SQL
        cursor.execute(f"SELECT id, doc FROM {table} LIMIT %s", (limit,))
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            data = json.loads(row['doc']) if isinstance(row['doc'], str) else row['doc']
            results.append({**data, "id": row['id']})
        return results
    finally:
        conn.close()

@app.delete("/collections/{name}")
async def delete_collection(name: str):
    """Deletes a Collection (Drops Table)"""
    table = validate_name(name)
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
        return {"status": "success", "message": f"Collection '{table}' deleted."}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ==========================================
# 2. DOCUMENT OPERATIONS
# ==========================================
@app.post("/doc/{collection}")
async def add_document_auto(collection: str, data: dict = Body(...)):
    """Adds a document and auto-generates a UUID."""
    table = validate_name(collection)
    
    # Generate a unique ID
    doc_id = str(uuid.uuid4()) 
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Ensure table exists
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table} (id VARCHAR(255) PRIMARY KEY, doc JSON)")
        
        # Insert
        json_data = json.dumps(data)
        sql = f"INSERT INTO {table} (id, doc) VALUES (%s, %s)"
        cursor.execute(sql, (doc_id, json_data))
        conn.commit()
        
        # Return the new ID so the frontend knows it
        return {"status": "created", "id": doc_id}
    finally:
        conn.close()

@app.post("/doc/{collection}/{doc_id}")
async def add_document(collection: str, doc_id: str, data: dict = Body(...)):
    """Adds (or fully overwrites) a document"""
    table = validate_name(collection)
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Ensure collection exists first (optional, but safe)
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table} (id VARCHAR(255) PRIMARY KEY, doc JSON)")
        
        json_data = json.dumps(data)
        # Upsert: Insert, or Update if ID exists
        sql = f"INSERT INTO {table} (id, doc) VALUES (%s, %s) ON DUPLICATE KEY UPDATE doc=%s"
        cursor.execute(sql, (doc_id, json_data, json_data))
        conn.commit()
        return {"status": "success", "id": doc_id}
    finally:
        conn.close()

@app.get("/doc/{collection}/{doc_id}")
async def read_document(collection: str, doc_id: str):
    """Reads a single document"""
    table = validate_name(collection)
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"SHOW TABLES LIKE '{table}'")
        if not cursor.fetchone(): return None

        cursor.execute(f"SELECT doc FROM {table} WHERE id=%s", (doc_id,))
        result = cursor.fetchone()
        if result:
            return json.loads(result['doc']) if isinstance(result['doc'], str) else result['doc']
        return None
    finally:
        conn.close()

@app.patch("/doc/{collection}/{doc_id}")
async def edit_document(collection: str, doc_id: str, data: dict = Body(...)):
    """Edits specific fields (Merge Update)"""
    table = validate_name(collection)
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # 1. Fetch existing
        cursor.execute(f"SELECT doc FROM {table} WHERE id=%s", (doc_id,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(404, "Document not found")
            
        current_data = json.loads(result['doc']) if isinstance(result['doc'], str) else result['doc']
        
        # 2. Merge Python dicts
        current_data.update(data)
        
        # 3. Save back
        new_json = json.dumps(current_data)
        cursor.execute(f"UPDATE {table} SET doc=%s WHERE id=%s", (new_json, doc_id))
        conn.commit()
        return {"status": "updated", "id": doc_id}
    finally:
        conn.close()

@app.delete("/doc/{collection}/{doc_id}")
async def delete_document(collection: str, doc_id: str):
    """Deletes a single document"""
    table = validate_name(collection)
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(f"DELETE FROM {table} WHERE id=%s", (doc_id,))
        conn.commit()
        return {"status": "deleted", "id": doc_id}
    finally:
        conn.close()

@app.post("/query/{collection}")
async def query_collection(collection: str, query: QueryRequest):
    """
    Advanced Query: Filter, Sort, and Limit using TiDB JSON capabilities.
    """
    table = validate_name(collection)
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Check table existence
        cursor.execute(f"SHOW TABLES LIKE '{table}'")
        if not cursor.fetchone(): return []

        # --- BUILD SQL ---
        sql = f"SELECT id, doc FROM {table}"
        params = []
        where_clauses = []

        # A. Handle Filters (WHERE)
        # Safe operator whitelist to prevent SQL Injection
        allowed_ops = {
            "==": "=", "=": "=", 
            "!=": "!=", 
            ">": ">", ">=": ">=", 
            "<": "<", "<=": "<="
        }

        for f in query.filters:
            op = allowed_ops.get(f.operator)
            if not op: continue # Skip invalid operators
            
            # TiDB JSON Syntax: JSON_EXTRACT(doc, '$.age')
            # JSON_UNQUOTE removes quotes from strings so "25" becomes 25
            clause = f"JSON_UNQUOTE(JSON_EXTRACT(doc, '$.{f.field}')) {op} %s"
            where_clauses.append(clause)
            params.append(f.value)

        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        # B. Handle Sorting (ORDER BY)
        if query.sort_field:
            direction = "DESC" if query.sort_direction.upper() == "DESC" else "ASC"
            # Cast to meaningful type if possible, otherwise string sort
            sql += f" ORDER BY JSON_UNQUOTE(JSON_EXTRACT(doc, '$.{query.sort_field}')) {direction}"

        # C. Handle Limit
        sql += " LIMIT %s"
        params.append(query.limit)

        # --- EXECUTE ---
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()

        results = []
        for row in rows:
            data = json.loads(row['doc']) if isinstance(row['doc'], str) else row['doc']
            results.append({**data, "id": row['id']})
        return results

    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()

  
@app.get("/")
async def root():
    return {"message": "Welcome to the TiDB API"}