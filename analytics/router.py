"""Endpoints de analítica: resumen, preguntas en lenguaje natural e historial.

Toda consulta pasa por el alcance del rol del usuario antes de ejecutarse: un
empleado solo ve sus propios pedidos, ventas y ficha (ver ``analytics.scope``).
"""

import sqlite3

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from analytics import resolver, scope as scoping
from auth.dependencies import get_current_user, require_roles
from db.database import connection

router = APIRouter(tags=["analytics"])

# Tabla de auditoría creada por el equipo; /history la usa si existe.
AUDIT_TABLE = "AuditoriaConsultas"

# La analítica no está disponible para el rol ``user``: su panel está pendiente
# de aprobación y no debe exponer información de la aplicación.
ANALYTICS_ROLES = ("admin", "employee")


class AskRequest(BaseModel):
    """Pregunta enviada desde la página de Consultas."""

    question: str = Field(min_length=1, max_length=500)


@router.get("/metrics")
def metrics(user: sqlite3.Row = Depends(require_roles(*ANALYTICS_ROLES))) -> dict:
    """Conteos principales de Northwind para las tarjetas del panel."""
    scope = scoping.build_scope(user)
    scoped = "" if scope.is_admin else scope.own_id
    counts = {
        "orders": (
            "SELECT COUNT(*) FROM Orders",
            "SELECT COUNT(*) FROM Orders WHERE EmployeeID = ?",
            (scoped,),
        ),
        "customers": ("SELECT COUNT(*) FROM Customers", None, ()),
        "products": ("SELECT COUNT(*) FROM Products", None, ()),
        "employees": (
            "SELECT COUNT(*) FROM Employees",
            "SELECT COUNT(*) FROM Employees WHERE EmployeeID = ?",
            (scoped,),
        ),
    }

    with connection() as conn:
        result = {}
        for name, (everyone, own, params) in counts.items():
            sql = everyone if scope.is_admin or own is None else own
            result[name] = conn.execute(sql, params).fetchone()[0]
        return result


@router.post("/ask")
def ask(
    payload: AskRequest,
    user: sqlite3.Row = Depends(require_roles(*ANALYTICS_ROLES)),
) -> dict:
    """Responde una pregunta con la forma que pida la intención reconocida.

    ``responseType`` indica qué se devuelve: un valor único (``scalar_count``,
    ``scalar_number``, ``scalar_date``), un objeto (``single_row``), una lista
    plana (``value_list``) o una lista de filas (``row_list``, ``grouped_list``,
    ``table``). ``rows`` siempre lleva las filas crudas para la tabla.
    """
    question = payload.question.strip()
    scope = scoping.build_scope(user)
    plan = resolver.resolve(question, user["role"])

    denial = scoping.denies(plan, scope, question)
    if denial is not None:
        return resolver.denied_answer(question, denial)

    plan = scoping.apply(plan, scope)
    if plan is None:
        return resolver.unsupported_answer(question)

    rows = []
    if plan.sql:
        with connection() as conn:
            rows = [dict(row) for row in conn.execute(plan.sql, plan.params).fetchall()]

    return resolver.build_response(question, plan, rows)


@router.get("/history")
def history(user: sqlite3.Row = Depends(get_current_user)) -> list[dict]:
    """Consultas registradas del usuario autenticado.

    Devuelve una lista vacía mientras no exista la tabla de auditoría. Cuando
    se cree, debe tener al menos estas columnas para que el historial del
    frontend funcione: ``ConsultaID``, ``UserID``, ``pregunta``, ``sql``,
    ``estado``, ``latencia_ms`` y ``created_at``.
    """
    with connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (AUDIT_TABLE,),
        ).fetchone()
        if exists is None:
            return []

        rows = conn.execute(
            "SELECT ConsultaID AS id, pregunta AS question, sql,"
            " estado AS status, created_at AS createdAt"
            f" FROM {AUDIT_TABLE} WHERE UserID = ?"
            " ORDER BY ConsultaID DESC LIMIT 50",
            (user["UserID"],),
        ).fetchall()

    return [dict(row) for row in rows]
