import importlib
import sqlite3
from typing import Any


def query_v2ray_python_stats(host: str, port: int, identifier: str) -> dict[str, Any]:
    """
    Optional integration with v2ray-python.
    Runtime import is used so the checker can run even when dependency is absent.
    """
    try:
        rpc_mod = importlib.import_module("v2ray_python.rpc")
        client_cls = getattr(rpc_mod, "V2RayClient", None)
        if client_cls is None:
            return {"ok": False, "error": "V2RayClient_missing"}
        client = client_cls(host, int(port))
        stats = client.get_user_stats(identifier)
        if not stats:
            return {"ok": True, "available": False, "downlink": 0, "uplink": 0}
        down = int(getattr(stats, "downlink", 0) or 0)
        up = int(getattr(stats, "uplink", 0) or 0)
        return {"ok": True, "available": True, "downlink": down, "uplink": up}
    except Exception as e:
        return {"ok": False, "error": f"v2ray_python_error:{type(e).__name__}"}


def query_sqlite_user_presence(db_path: str, table: str, column: str, identifier: str) -> dict[str, Any]:
    if not db_path:
        return {"ok": False, "error": "sqlite_db_path_empty"}
    try:
        conn = sqlite3.connect(db_path, timeout=2.0)
        try:
            cur = conn.cursor()
            query = f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1"
            cur.execute(query, (identifier,))
            row = cur.fetchone()
            return {"ok": True, "present": bool(row)}
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": f"sqlite_error:{type(e).__name__}"}

