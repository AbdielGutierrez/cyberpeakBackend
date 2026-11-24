-- Cyberpeak — tablas propias del proyecto (además de Northwind y Users)
-- Ejecutar sobre backend/db/northwind.db:
--   cd backend
--   .\.venv\Scripts\python.exe -c "import sqlite3; sqlite3.connect('db/northwind.db').executescript(open('db/schema_aurora.sql', encoding='utf-8').read())"

PRAGMA foreign_keys = ON;

-- B5.2 — Auditoría de consultas: una fila por cada POST /ask.
CREATE TABLE IF NOT EXISTS AuditoriaConsultas (
    ConsultaID  INTEGER PRIMARY KEY AUTOINCREMENT,
    UserID      INTEGER NOT NULL REFERENCES Users(UserID) ON DELETE CASCADE,
    pregunta    TEXT    NOT NULL,
    sql         TEXT    NOT NULL DEFAULT '',
    estado      TEXT    NOT NULL DEFAULT 'ok',   -- ok | no_reconocida | error
    latencia_ms INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_consultas_user_fecha
    ON AuditoriaConsultas (UserID, ConsultaID DESC);
