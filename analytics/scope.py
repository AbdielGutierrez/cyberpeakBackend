"""Alcance por rol: qué filas puede ver cada usuario.

Reglas acordadas:

- ``admin`` ve todo.
- ``employee`` ve productos, proveedores, clientes y transportistas completos,
  pero de ``Orders``, ``OrderDetails`` y ``Employees`` solo las filas en las que
  aparece él mismo. El vínculo usuario↔empleado es el correo
  ``nombreapellido@cyberpeak.com``.
- ``user`` no consulta la analítica (su panel está pendiente de aprobación).

El filtro se aplica envolviendo esas tres tablas en CTEs ya filtradas por
``EmployeeID``. Así funciona igual para el catálogo (SQL escrito a mano) y para
las consultas por cláusulas, sin reescribir cada consulta.
"""

import re
from dataclasses import dataclass, replace
from functools import lru_cache

from analytics import lookups
from analytics.text import normalize
from db.database import connection

# Tablas que un empleado solo ve cuando él aparece en ellas.
OWN_ROW_TABLES = ("Orders", "OrderDetails", "Employees")

# Tablas reservadas al administrador.
ADMIN_ONLY_TABLES = ("Users",)

# Marca de "no hay empleado asociado": una condición imposible en vez de
# ejecutar la consulta sin filtro (fallar cerrado, nunca abierto).
NO_EMPLOYEE = -1

DENIED_OTHER = (
    "No puedo mostrarte información de otros empleados. Solo puedes consultar "
    "tus propias ventas, pedidos y datos."
)

DENIED_TABLE = "No tienes permisos para consultar la tabla {table}."


@dataclass(frozen=True)
class Scope:
    """Quién pregunta y a qué filas queda limitado."""

    role: str
    employee_id: int | None = None
    employee_name: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def own_id(self) -> int:
        return self.employee_id if self.employee_id is not None else NO_EMPLOYEE

    @property
    def note(self) -> str:
        if self.is_admin:
            return ""
        if self.employee_name is None:
            return (
                " Tu usuario no está vinculado a un empleado, así que no hay "
                "pedidos ni ventas que mostrarte."
            )
        return (
            f" Solo se incluyen los pedidos de {self.employee_name}, tu propio "
            "registro como empleado."
        )


def _key(value: str) -> str:
    return re.sub(r"[^a-z]", "", normalize(value))


@lru_cache(maxsize=None)
def _employees() -> tuple[tuple[int, str, str], ...]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT EmployeeID, FirstName, LastName FROM Employees ORDER BY 1"
        ).fetchall()
    return tuple((row[0], row[1], row[2]) for row in rows)


def employee_of(user) -> tuple[int, str] | None:
    """Empleado que corresponde al usuario, por el correo corporativo."""
    email = (user["email"] or "").split("@")[0]
    wanted = _key(email)
    if not wanted:
        return None
    for employee_id, first, last in _employees():
        if _key(f"{first}{last}") == wanted:
            return employee_id, f"{first} {last}"
    return None


def build_scope(user) -> Scope:
    """Alcance del usuario autenticado."""
    role = user["role"]
    if role == "admin":
        return Scope(role=role)
    employee = employee_of(user)
    if employee is None:
        return Scope(role=role)
    return Scope(role=role, employee_id=employee[0], employee_name=employee[1])


def foreign_employee(question: str, scope: Scope) -> str | None:
    """Nombre de otro empleado mencionado en la pregunta, si lo hay."""
    if scope.is_admin:
        return None
    mentioned = lookups.find_entity("employee", question)
    if mentioned is None:
        return None
    if scope.employee_name is not None and mentioned == scope.employee_name:
        return None
    return mentioned


def _ctes(employee_id: int) -> str:
    """CTEs con las tablas propias, listas para prefijar la consulta."""
    return (
        "scope_orders AS (\n"
        f"    SELECT * FROM Orders WHERE EmployeeID = {employee_id}\n"
        "),\n"
        "scope_order_details AS (\n"
        "    SELECT od.* FROM OrderDetails od\n"
        "    JOIN Orders o ON o.OrderID = od.OrderID\n"
        f"    WHERE o.EmployeeID = {employee_id}\n"
        "),\n"
        "scope_employees AS (\n"
        f"    SELECT * FROM Employees WHERE EmployeeID = {employee_id}\n"
        ")"
    )


RENAMES = (
    ("OrderDetails", "scope_order_details"),
    ("Orders", "scope_orders"),
    ("Employees", "scope_employees"),
)


def scoped_sql(sql: str, employee_id: int) -> str:
    """Reescribe el SQL para que solo vea las filas del empleado."""
    body = sql
    for table, alias in RENAMES:
        body = re.sub(rf"\b{table}\b", alias, body)

    ctes = _ctes(employee_id)
    if re.match(r"\s*WITH\b", body, re.I):
        return re.sub(
            r"^(\s*WITH)\s+", lambda m: f"{m.group(1)} {ctes},\n", body, count=1
        )
    return f"WITH {ctes}\n{body}"


def denies(plan, scope: Scope, question: str) -> str | None:
    """Motivo por el que el plan no puede ejecutarse para este rol."""
    if scope.is_admin or plan is None:
        return None
    for table in ADMIN_ONLY_TABLES:
        if re.search(rf"\b{table}\b", plan.sql):
            return DENIED_TABLE.format(table=table)
    mentioned = foreign_employee(question, scope)
    if mentioned is not None:
        return f"{DENIED_OTHER} Pediste datos de {mentioned}."
    return None


def touches_own_rows(sql: str) -> bool:
    """¿La consulta lee alguna de las tablas restringidas?"""
    return any(re.search(rf"\b{table}\b", sql) for table in OWN_ROW_TABLES)


def apply(plan, scope: Scope):
    """Aplica el alcance a un plan ya resuelto."""
    if plan is None or scope.is_admin:
        return plan
    if not touches_own_rows(plan.sql):
        return plan
    return replace(
        plan,
        sql=scoped_sql(plan.sql, scope.own_id),
        scope_note=scope.note,
    )
