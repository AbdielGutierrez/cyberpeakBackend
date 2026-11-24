import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# backend/db/database.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BACKEND_DIR / "db" / "northwind.db"

# Tablas que debe traer la base Northwind.
NORTHWIND_TABLES = (
    "Categories",
    "Customers",
    "Employees",
    "OrderDetails",
    "Orders",
    "Products",
    "Shippers",
    "Suppliers",
)


def db_path() -> Path:
    """Ruta de la base a usar, configurable con ``CYBERPEAK_DB_PATH``."""
    raw = os.getenv("CYBERPEAK_DB_PATH")
    if not raw:
        return DEFAULT_DB_PATH

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


def connect() -> sqlite3.Connection:
    """Abre la conexión SQLite con filas accesibles por nombre."""
    path = db_path()
    if not path.exists():
        raise FileNotFoundError(
            f"No existe la base de datos {path}. "
            "Se esperaba el archivo northwind.db."
        )

    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def connection():
    """Conexión con commit al salir, rollback si hay error y cierre seguro."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def missing_tables() -> list[str]:
    """Tablas de Northwind que faltan en la base."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        conn.close()

    existing = {row["name"] for row in rows}
    return [name for name in NORTHWIND_TABLES if name not in existing]


def is_initialized() -> bool:
    """Indica si la base existe y tiene todas las tablas de Northwind."""
    try:
        return not missing_tables()
    except sqlite3.Error:
        return False


def check_connection() -> None:
    """Falla con un mensaje claro si la base no está disponible."""
    missing = missing_tables()
    if missing:
        raise RuntimeError(
            f"La base {db_path()} está incompleta. Faltan: {', '.join(missing)}."
        )


if __name__ == "__main__":
    check_connection()
    print(f"Base de datos Northwind lista en {db_path()}")
