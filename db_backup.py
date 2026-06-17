"""
db_backup.py -- PostgreSQL database backup using pg_dump.
Creates a compressed dump file with a timestamp.
"""

import subprocess
import shutil
import sys
import os
from datetime import datetime
from pathlib import Path

# Import config to get DATABASE_URL
from config import config

def _parse_db_url(url: str) -> dict:
    """Extract connection parameters from DATABASE_URL."""
    # Format: postgresql://user:password@host:port/dbname
    url = url.replace("postgresql://", "")
    auth, rest = url.split("@")
    user, password = auth.split(":") if ":" in auth else (auth, "")
    host_port, dbname = rest.split("/")
    host, port = (host_port.split(":") + ["5432"])[:2]
    return {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
    }

def _find_pg_dump() -> str | None:
    """Locate pg_dump executable on the system."""
    # Try common Windows paths (include version 18)
    if sys.platform == "win32":
        for ver in ["18", "17", "16", "15", "14", "13", "12"]:
            path = f"C:\\Program Files\\PostgreSQL\\{ver}\\bin\\pg_dump.exe"
            if os.path.exists(path):
                return path

    # Try shutil.which (works on Unix and Windows if in PATH)
    which = shutil.which("pg_dump")
    if which:
        return which

    # Try pg_config to find bindir
    pg_config = shutil.which("pg_config")
    if pg_config:
        try:
            bindir = subprocess.check_output([pg_config, "--bindir"], text=True).strip()
            pg_dump = os.path.join(bindir, "pg_dump" + (".exe" if sys.platform == "win32" else ""))
            if os.path.exists(pg_dump):
                return pg_dump
        except Exception:
            pass

    return None

def create_backup(output_dir: str = "backups") -> str:
    """
    Create a compressed database backup.
    Returns the path to the backup file.
    """
    params = _parse_db_url(config.DATABASE_URL)
    pg_dump = _find_pg_dump()

    if not pg_dump:
        raise FileNotFoundError(
            "pg_dump not found. Please install PostgreSQL or add its bin directory to PATH."
        )

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"mega_enrichment_backup_{timestamp}.dump"
    filepath = os.path.join(output_dir, filename)

    env = os.environ.copy()
    env["PGPASSWORD"] = params["password"]

    cmd = [
        pg_dump,
        "-h", params["host"],
        "-p", params["port"],
        "-U", params["user"],
        "-d", params["dbname"],
        "-F", "c",          # compressed format
        "-f", filepath,
    ]

    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
        print(f"[backup] Created: {filepath}")
        return filepath
    except subprocess.CalledProcessError as e:
        print(f"[backup] Error: {e.stderr}")
        raise

if __name__ == "__main__":
    create_backup()