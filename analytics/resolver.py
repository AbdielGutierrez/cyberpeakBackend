"""Motor semántico: pregunta en español → intención, SQL y forma de respuesta.

La resolución es determinista y en tres pasos:

1. Se buscan las intenciones del catálogo cuyas palabras clave están todas en la
   pregunta; gana la más específica.
2. Se extraen los parámetros (año, cantidad, país, categoría, producto, rol,
   monto) y se pasan como parámetros de SQLite. Si falta un parámetro obligatorio
   se prueba la siguiente intención.
3. Si la pregunta pide el catálogo de productos se arma una consulta que devuelve
   solo las columnas pedidas (producto, ID, precio, categoría, proveedor,
   unidad) y respeta el orden indicado.

Si nada encaja se usa un plan genérico métrica × grupo; si tampoco, la pregunta se
considera no reconocida. El SQL nunca se construye con el texto del usuario: las
columnas y las expresiones salen de listas blancas.

Antes que el catálogo se prueba el modo por cláusulas: si la pregunta trae
``SELECT`` y ``FROM`` se analiza como consulta SQL y se valida contra la lista
blanca de tablas del rol (ver ``analytics.spec``).
"""

import re
from dataclasses import dataclass
from typing import Any

from analytics import catalog, lookups, spec
from analytics.catalog import (
    GROUPED_LIST,
    ROW_LIST,
    SCALAR_COUNT,
    SCALAR_DATE,
    SCALAR_NUMBER,
    SINGLE_ROW,
    TABLE,
    VALUE_LIST,
)
from analytics.text import normalize

SCALARS = (SCALAR_COUNT, SCALAR_NUMBER, SCALAR_DATE)

# Plan genérico: métrica × grupo, cuando no hay una intención específica.
METRICS = {
    "ventas": ("ROUND(SUM(od.Quantity * p.Price), 2)", "Ventas"),
    "pedidos": ("COUNT(DISTINCT o.OrderID)", "Pedidos"),
    "unidades": ("SUM(od.Quantity)", "Unidades"),
}

GROUPS = {
    "mes": ("strftime('%Y-%m', o.OrderDate)", "Mes"),
    "producto": ("p.ProductName", "Producto"),
    "categoria": ("c.CategoryName", "Categoría"),
    "empleado": ("e.FirstName || ' ' || e.LastName", "Empleado"),
    "cliente": ("cu.CustomerName", "Cliente"),
    "pais": ("cu.Country", "País"),
}

METRIC_KEYWORDS = (
    ("unidades", ("unidad", "cantidad", "volumen")),
    ("ventas", ("venta", "vend", "ingreso", "factur", "revenue", "monto")),
    ("pedidos", ("pedido", "orden")),
)

GROUP_KEYWORDS = (
    ("mes", ("mes", "mensual", "evolucion", "tendencia")),
    ("producto", ("producto", "articulo")),
    ("categoria", ("categoria",)),
    ("empleado", ("empleado", "vendedor")),
    ("cliente", ("cliente",)),
    ("pais", ("pais", "paises", "region")),
)

GENERIC_JOINS = (
    "FROM Orders o\n"
    "JOIN OrderDetails od ON od.OrderID = o.OrderID\n"
    "JOIN Products p ON p.ProductID = od.ProductID\n"
    "JOIN Categories c ON c.CategoryID = p.CategoryID\n"
    "JOIN Customers cu ON cu.CustomerID = o.CustomerID\n"
    "JOIN Employees e ON e.EmployeeID = o.EmployeeID"
)

MAX_ROWS = 10

# Catálogo de productos: columnas permitidas, en el orden en que se muestran.
PRODUCT_ATTRIBUTES = (
    ("ID", "p.ProductID", ("id", "codigo", "numero")),
    ("Producto", "p.ProductName", ("producto", "nombre", "articulo")),
    ("Precio", "p.Price", ("precio", "cuesta", "vale", "valor")),
    ("Categoría", "c.CategoryName", ("categoria", "rubro")),
    ("Proveedor", "s.SupplierName", ("proveedor",)),
    ("Unidad", "p.Unit", ("unidad", "medida", "presentacion", "empaque")),
)

# Palabras que convierten la pregunta en una agregación, no en un listado.
PRODUCT_LIST_BLOCKERS = (
    "vend",
    "venta",
    "factur",
    "ingres",
    "genera",
    "dinero",
    "cuantos",
    "cuantas",
    "total",
    "promedio",
    "ranking",
    "top",
    "mas",
    "menos",
    "caro",
    "empleado",
    "cliente",
    "compra",
    "compro",
)

# Palabras que piden un orden explícito de las filas.
ORDER_REQUEST = re.compile(
    r"ordenad|ordena|orden por|de mayor a menor|de menor a mayor"
)

DESCENDING = re.compile(r"descendente|\bdesc\b|mayor a menor|de la z a la a|z a la a")
ASCENDING = re.compile(r"ascendente|\basc\b|menor a mayor|de la a a la z|a a la z")


def _mentions(text: str, word: str) -> bool:
    """Palabra completa, en singular o plural.

    ``id`` no debe coincidir dentro de ``pedido`` y ``producto`` sí debe
    encontrar ``productos``.
    """
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(word)}(?:es|s)?(?![a-z0-9])", text
        )
        is not None
    )

EXAMPLES = (
    "¿Cuántos clientes hay registrados?",
    "¿Cuál es el producto más caro del catálogo?",
    "¿Qué empleado ha gestionado más órdenes?",
    "¿Cuáles son los 5 productos más vendidos por cantidad?",
    "¿Cuánto ha facturado cada categoría de producto?",
    "¿Qué clientes están ubicados en Alemania?",
    "¿Cuántas órdenes se realizaron durante 1997?",
)


@dataclass(frozen=True)
class Plan:
    """Intención resuelta, lista para ejecutarse."""

    intent_id: str
    category: str
    response_type: str
    sql: str
    params: tuple = ()
    columns: tuple[str, ...] = ()
    description: str = ""
    scope_note: str = ""


@dataclass(frozen=True)
class _GenericPlan:
    metric: str
    group: str | None
    employee: str | None = None
    product: str | None = None


# --------------------------------------------------------------- extracción


def _numbers(text: str) -> list[tuple[int, float]]:
    """Números de la pregunta con su posición, para asignarlos por tipo."""
    found = []
    for match in re.finditer(r"\d+(?:[.,]\d+)?", text):
        raw = match.group().replace(",", ".")
        try:
            found.append((match.start(), float(raw)))
        except ValueError:
            continue
    return found


def _bind(intent: catalog.Intent, text: str) -> Plan | None:
    """Extrae los parámetros de la intención o ``None`` si falta alguno."""
    numbers = _numbers(text)
    used: set[int] = set()
    values: list[Any] = []
    sql = intent.sql

    def take(kind: str) -> Any:
        for index, (_, number) in enumerate(numbers):
            if index in used:
                continue
            if kind == "year":
                if number.is_integer() and 1900 <= number <= 2100:
                    used.add(index)
                    return int(number)
            elif kind == "order_id":
                if number.is_integer() and number >= 1000:
                    used.add(index)
                    return int(number)
            elif kind == "amount":
                if number >= 100 and not 1900 <= number <= 2100:
                    used.add(index)
                    return number
            elif kind == "price":
                if 0 < number <= 100000 and not 1900 <= number <= 2100:
                    used.add(index)
                    return number
            elif kind == "count":
                if number.is_integer() and 1 <= number <= 100:
                    used.add(index)
                    return int(number)
        return None

    for param in intent.params:
        if param.kind in ("country", "category", "role", "product", "employee"):
            value = lookups.find_entity(param.kind, text)
            if value is None:
                return None
        elif param.kind == "product_or_all":
            # Dos ``?``: el producto, o ``NULL`` para no filtrar por producto.
            product = lookups.find_entity("product", text)
            values.extend((product, product))
            continue
        elif param.kind == "year_or_all":
            # Dos ``?``: el año, o ``NULL`` para no filtrar por año.
            year = take("year")
            bound = str(year) if year is not None else None
            values.extend((bound, bound))
            continue
        elif param.kind == "optional_year":
            year = take("year")
            if year is None:
                # Sin año explícito se usa el último año con datos.
                sql = sql.replace("= ?", f"= {catalog.LAST_YEAR}", 1)
                continue
            value = str(year)
        elif param.kind == "year":
            year = take("year")
            if year is None:
                return None
            value = str(year)
        elif param.kind == "year_start":
            year = take("year")
            if year is None:
                return None
            value = f"{year}-01-01"
        else:
            value = take(param.kind)
            if value is None:
                if param.default is None:
                    return None
                value = param.default
        values.append(value)

    return Plan(
        intent_id=intent.id,
        category=intent.category,
        response_type=intent.response_type,
        sql=sql,
        params=tuple(values),
        columns=intent.columns,
        description=intent.description,
    )


# ------------------------------------------------------------------ operaciones matemáticas

MATH_OPS = (
    ("sum", ("suma", "sumar", "sumatoria")),
    ("avg", ("promedio", "media", "promediar")),
    ("sub", ("resta", "restar", "diferencia")),
    ("mul", ("multiplicacion", "multiplicar")),
    ("div", ("division", "dividir", "cociente", "ratio")),
)


def _math_op(text: str) -> str | None:
    for op, words in MATH_OPS:
        if any(word in text for word in words):
            return op
    return None


def _scalar_expr(metric: str, where_clause: str, params: list) -> tuple[str, tuple]:
    """Subconsulta escalar que devuelve el agregado de ``metric`` con el filtro dado."""
    expression, _label = METRICS[metric]
    where = (" WHERE " + where_clause) if where_clause else ""
    sql = f"(SELECT COALESCE({expression}, 0) {GENERIC_JOINS}{where})"
    return sql, tuple(params)


def _match_math(text: str) -> Plan | None:
    op = _math_op(text)
    if op is None:
        return None

    employees = lookups.find_employees(text)
    products = lookups.find_products(text)

    metric = next(
        (name for name, words in METRIC_KEYWORDS if any(w in text for w in words)),
        None,
    )
    if metric is None and ("total" in text or "totales" in text):
        metric = "ventas"

    # No secuestrar preguntas normales (p. ej. "ticket promedio") cuando no hay
    # métrica de ventas/unidades ni entidades ni totales.
    if (
        metric not in ("ventas", "unidades")
        and not employees
        and not products
        and "total" not in text
        and "totales" not in text
    ):
        # Aritmética pura entre dos números, si la hay.
        if op in ("sum", "sub", "mul", "div"):
            nums = [n for _, n in _numbers(text) if not (1900 <= n <= 2100)]
            if len(nums) >= 2:
                a, b = nums[0], nums[1]
                if op == "sum":
                    value = a + b
                elif op == "sub":
                    value = a - b
                elif op == "mul":
                    value = a * b
                else:
                    value = a / b if b else 0
                return Plan(
                    intent_id="matematica_pura",
                    category="Cálculo",
                    response_type=SCALAR_NUMBER,
                    sql=f"SELECT {value} AS resultado",
                    params=(),
                    columns=("resultado",),
                    description=f"Resultado de la operación: {value}.",
                )
        return None

    if metric is None:
        metric = "ventas"

    numbers = [n for _, n in _numbers(text) if not (1900 <= n <= 2100)]
    # descartar números que son claramente años o IDs grandes
    small_numbers = [n for n in numbers if 1 <= n <= 1000]

    _expr, label = METRICS[metric]

    # ----- suma
    if op == "sum":
        where_clauses: list[str] = []
        params: list = []
        if employees:
            placeholders = ", ".join("?" for _ in employees)
            where_clauses.append(
                f"e.FirstName || ' ' || e.LastName IN ({placeholders})"
            )
            params.extend(employees)
        if products:
            placeholders = ", ".join("?" for _ in products)
            where_clauses.append(f"p.ProductName IN ({placeholders})")
            params.extend(products)
        where = " AND ".join(where_clauses)
        where_sql = (" WHERE " + where) if where else ""
        sql = f"SELECT {METRICS[metric][0]} AS \"{label}\"\n{GENERIC_JOINS}{where_sql}"
        names = employees + products
        desc = f"Suma de {label.lower()}"
        if names:
            desc += " de " + ", ".join(names)
        if not names:
            desc += " totales"
        return Plan(
            intent_id="matematica_suma",
            category="Ventas generales",
            response_type=SCALAR_NUMBER,
            sql=sql,
            params=tuple(params),
            columns=(label,),
            description=desc + ".",
        )

    # ----- promedio
    if op == "avg":
        if metric == "pedidos":
            order_value = "1"
        elif metric == "unidades":
            order_value = "SUM(od.Quantity)"
        else:
            order_value = "SUM(od.Quantity * p.Price)"
        where_clauses = []
        params = []
        if employees:
            placeholders = ", ".join("?" for _ in employees)
            where_clauses.append(
                f"e.FirstName || ' ' || e.LastName IN ({placeholders})"
            )
            params.extend(employees)
        if products:
            placeholders = ", ".join("?" for _ in products)
            where_clauses.append(f"p.ProductName IN ({placeholders})")
            params.extend(products)
        where = " AND ".join(where_clauses)
        where_sql = (" WHERE " + where) if where else ""
        sql = (
            f"SELECT ROUND(AVG(order_total), 2) AS promedio_ventas\n"
            f"FROM (\n"
            f"  SELECT o.OrderID, {order_value} AS order_total\n"
            f"  {GENERIC_JOINS}\n"
            f"  {where_sql}\n"
            f"  GROUP BY o.OrderID\n"
            f")"
        )
        names = employees + products
        desc = "Promedio de " + label.lower()
        if names:
            desc += " de " + ", ".join(names)
        else:
            desc += " totales"
        return Plan(
            intent_id="matematica_promedio",
            category="Ventas generales",
            response_type=SCALAR_NUMBER,
            sql=sql,
            params=tuple(params),
            columns=("promedio_ventas",),
            description=desc + ".",
        )

    # ----- resta / multiplicación / división
    if op in ("sub", "mul", "div"):
        # Dos entidades del mismo tipo → operación entre sus totales.
        if len(employees) >= 2:
            left_clause = "e.FirstName || ' ' || e.LastName = ?"
            right_clause = "e.FirstName || ' ' || e.LastName = ?"
            left_sql, left_params = _scalar_expr(metric, left_clause, [employees[0]])
            right_sql, right_params = _scalar_expr(metric, right_clause, [employees[1]])
            operands = (employees[0], employees[1])
        elif len(products) >= 2:
            left_sql, left_params = _scalar_expr(metric, "p.ProductName = ?", [products[0]])
            right_sql, right_params = _scalar_expr(metric, "p.ProductName = ?", [products[1]])
            operands = (products[0], products[1])
        elif (employees or products) and small_numbers:
            entity = (employees or products)[0]
            is_emp = bool(employees)
            clause = (
                "e.FirstName || ' ' || e.LastName = ?"
                if is_emp
                else "p.ProductName = ?"
            )
            left_sql, left_params = _scalar_expr(metric, clause, [entity])
            right_sql, right_params = None, ()
            number = small_numbers[0]
            operands = (entity, number)
        elif len(small_numbers) >= 2:
            left_sql, left_params = None, ()
            right_sql, right_params = None, ()
            operands = (small_numbers[0], small_numbers[1])
        else:
            return None

        if left_sql is not None and right_sql is not None:
            if op == "sub":
                expr = f"ROUND({left_sql} - {right_sql}, 2)"
            elif op == "mul":
                expr = f"ROUND({left_sql} * {right_sql}, 2)"
            else:
                expr = f"ROUND({left_sql} * 1.0 / NULLIF({right_sql}, 0), 2)"
            params = left_params + right_params
            nombres_op = {"sub": "Diferencia", "mul": "Producto", "div": "Cociente"}
            desc = f"{nombres_op[op]} entre {operands[0]} y {operands[1]}"
        elif left_sql is not None:
            number = operands[1]
            if op == "sub":
                expr = f"ROUND({left_sql} - ?, 2)"
            elif op == "mul":
                expr = f"ROUND({left_sql} * ?, 2)"
            else:
                expr = f"ROUND({left_sql} / NULLIF(?, 0), 2)"
            params = left_params + (number,)
            nombres_op = {"sub": "Diferencia", "mul": "Producto", "div": "Cociente"}
            desc = f"{nombres_op[op]} de {operands[0]} y {number}"
        else:
            a, b = operands
            if op == "sub":
                value = a - b
            elif op == "mul":
                value = a * b
            else:
                value = a / b if b else 0
            return Plan(
                intent_id="matematica_pura",
                category="Cálculo",
                response_type=SCALAR_NUMBER,
                sql=f"SELECT {value} AS resultado",
                params=(),
                columns=("resultado",),
                description=f"Resultado de la operación: {value}.",
            )

        label_out = "resultado"
        return Plan(
            intent_id=f"matematica_{op}",
            category="Ventas generales",
            response_type=SCALAR_NUMBER,
            sql=f"SELECT {expr} AS {label_out}",
            params=tuple(params),
            columns=(label_out,),
            description=desc + ".",
        )

    return None


# ------------------------------------------------------------------ listas de empleados


def _match_employee_list(text: str) -> Plan | None:
    if "emplead" not in text:
        return None
    list_words = (
        "lista", "listado", "dame", "muestra", "todos", "todas",
        "quienes", "catalogo", "catalogo",
    )
    names = lookups.find_employees(text)
    if not (any(word in text for word in list_words) or names):
        return None
    blockers = (
        "vend", "venta", "factur", "ingres", "orden", "pedido", "cuant",
        "nac", "mas", "menos", "monto", "gestion", "rol", "top",
    )
    if any(word in text for word in blockers):
        return None

    numbers = _numbers(text)
    limit = None
    for _, n in numbers:
        if n.is_integer() and 1 <= n <= 1000 and not (1900 <= n <= 2100):
            limit = int(n)
            break

    params: list = []
    where = ""
    if names:
        placeholders = ", ".join("?" for _ in names)
        where = f"WHERE e.FirstName || ' ' || e.LastName IN ({placeholders})"
        params.extend(names)

    lines = [
        "SELECT e.FirstName || ' ' || e.LastName AS empleado,",
        "       e.BirthDate AS nacimiento",
        "FROM Employees e",
    ]
    if where:
        lines.append(where)
    lines.append("ORDER BY empleado")
    if limit is not None:
        lines.append(f"LIMIT {limit}")

    desc = "Listado de empleados"
    if names:
        desc += " (" + ", ".join(names) + ")"
    if limit is not None:
        desc += f", máximo {limit}"
    return Plan(
        intent_id="empleados_lista_filtrada",
        category="Empleados",
        response_type=ROW_LIST,
        sql="\n".join(lines),
        params=tuple(params),
        columns=("empleado", "nacimiento"),
        description=desc + ".",
    )


# ------------------------------------------------------------------ listas de proveedores


def _match_supplier_list(text: str) -> Plan | None:
    if "proveedor" not in text:
        return None
    blockers = (
        "cuant", "total", "mas", "menos", "producto", "ingreso", "factur",
        "vend", "rol",
    )
    if any(word in text for word in blockers):
        return None

    country = lookups.find_entity("country", text)
    params: list = []
    where = ""
    if country is not None:
        where = "WHERE Country = ?"
        params.append(country)

    lines = [
        "SELECT SupplierName AS proveedor,",
        "       Country AS pais,",
        "       City AS ciudad",
        "FROM Suppliers",
    ]
    if where:
        lines.append(where)

    desc = "Listado de proveedores con su país y ciudad."
    if country is not None:
        desc += f" ({country})"
    return Plan(
        intent_id="proveedores_lista_filtrada",
        category="Proveedores",
        response_type=ROW_LIST,
        sql="\n".join(lines),
        params=tuple(params),
        columns=("proveedor", "pais", "ciudad"),
        description=desc,
    )


# ------------------------------------------------------------------ resolver


def resolve(question: str, role: str = "admin") -> Plan | None:
    """Devuelve el plan de la pregunta o ``None`` si no se reconoce."""
    plan = _match_clauses(question, role)
    if plan is not None:
        return plan

    text = normalize(question)
    if not text:
        return None

    plan = _match_math(text)
    if plan is not None:
        return plan

    plan = _match_employee_list(text)
    if plan is not None:
        return plan

    plan = _match_supplier_list(text)
    if plan is not None:
        return plan

    plan = _match_catalog(text)
    if plan is not None:
        return plan

    plan = _match_product_list(text)
    if plan is not None:
        return plan

    generic = _match_generic(text)
    if generic is not None:
        return _build_generic(generic)

    # Una pregunta que solo nombra a un empleado devuelve su ficha (o vacío).
    return _employee_card(text)


def _match_clauses(question: str, role: str) -> Plan | None:
    """Consulta escrita con cláusulas SQL; ``None`` si no lo es."""
    if not spec.looks_like_clause_query(question):
        return None
    try:
        query = spec.parse(question, role)
    except spec.SpecError as error:
        return Plan(
            intent_id="consulta_invalida",
            category="Consulta no válida",
            response_type="invalid",
            sql="",
            description=str(error),
        )
    return Plan(
        intent_id="consulta_clausulas",
        category="Consulta por cláusulas",
        response_type=query.response_type,
        sql=spec.plan_sql(query),
        params=query.params,
        columns=query.columns,
        description=spec.summary(question, query),
    )


def _employee_card(text: str) -> Plan | None:
    """Ficha del empleado nombrado, sin métricas de ventas de por medio."""
    employee = lookups.find_entity("employee", text)
    if employee is None:
        if "emplead" not in text:
            return None
        metric = next(
            (name for name, words in METRIC_KEYWORDS if any(w in text for w in words)),
            None,
        )
        group = next(
            (name for name, words in GROUP_KEYWORDS if any(w in text for w in words)),
            None,
        )
        if metric is not None or (group is not None and group != "empleado"):
            return None
    return Plan(
        intent_id="empleado_por_nombre",
        category="Empleados",
        response_type=SINGLE_ROW,
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       e.BirthDate AS nacimiento\n"
            "FROM Employees e\n"
            "WHERE e.EmployeeID = (\n"
            "    SELECT EmployeeID FROM Employees\n"
            "    WHERE FirstName || ' ' || LastName = ?"
            ")"
        ),
        params=(employee or "",),
        columns=("empleado", "nacimiento"),
        description="Ficha del empleado indicado.",
    )


def _match_catalog(text: str) -> Plan | None:
    scored: list[tuple[int, catalog.Intent]] = []
    for intent in catalog.INTENTS:
        if not all(keyword in text for keyword in intent.keywords):
            continue
        if any(forbidden in text for forbidden in intent.forbid):
            continue

        score = sum(len(keyword) for keyword in intent.keywords)
        score += len(intent.params) * 3

        if intent.regex:
            match = re.search(intent.regex, text)
            if match is None:
                continue
            score += len(match.group())

        scored.append((score, intent))

    scored.sort(key=lambda item: -item[0])

    for _, intent in scored:
        plan = _bind(intent, text)
        if plan is not None:
            return plan
    return None


def _match_product_list(text: str) -> Plan | None:
    """Consulta del catálogo: solo las columnas pedidas y el orden pedido."""
    product = lookups.find_entity("product", text)
    if product is None and not any(
        word in text for word in ("producto", "articulo", "catalogo")
    ):
        return None
    if any(word in text for word in PRODUCT_LIST_BLOCKERS):
        return None
    category = lookups.find_entity("category", text)

    headers = [
        header
        for header, _, words in PRODUCT_ATTRIBUTES
        if any(_mentions(text, word) for word in words)
    ]
    if not headers:
        headers = ["Producto", "Precio", "Categoría"]
    elif product and "Producto" not in headers:
        headers.insert(0, "Producto")

    expression = {header: expr for header, expr, _ in PRODUCT_ATTRIBUTES}
    select = ", ".join(f'{expression[h]} AS "{h}"' for h in headers)

    product_id = None
    if "ID" in headers:
        for _, number in _numbers(text):
            if (
                number.is_integer()
                and 1 <= number <= 9999
                and not 1900 <= number <= 2100
            ):
                product_id = int(number)
                break

    sort_header = "Producto"
    if ORDER_REQUEST.search(text) and "alfabetic" not in text:
        if _mentions(text, "id") and "ID" in headers:
            sort_header = "ID"
        elif "precio" in text:
            sort_header = "Precio"
    if sort_header not in headers:
        sort_header = headers[0]
    direction = "DESC" if DESCENDING.search(text) else "ASC"
    if not DESCENDING.search(text) and ASCENDING.search(text):
        direction = "ASC"

    condition, params = "", ()
    if product is not None:
        condition, params = "WHERE p.ProductName = ?", (product,)
    elif product_id is not None:
        condition, params = "WHERE p.ProductID = ?", (product_id,)
    elif category is not None:
        condition, params = "WHERE c.CategoryName = ?", (category,)

    sql = [
        f"SELECT {select}",
        "FROM Products p",
        "JOIN Categories c ON c.CategoryID = p.CategoryID",
        "JOIN Suppliers s ON s.SupplierID = p.SupplierID",
    ]
    if condition:
        sql.append(condition)
    sql.append(f"ORDER BY {expression[sort_header]} {direction}")

    if product is not None:
        description = (
            f"Ficha del producto {product} con las columnas pedidas."
        )
        response_type = SINGLE_ROW
    elif product_id is not None:
        description = f"Ficha del producto con ID {product_id}."
        response_type = SINGLE_ROW
    else:
        description = (
            f"Catálogo de productos ordenado por {sort_header.lower()} "
            f"({'descendente' if direction == 'DESC' else 'ascendente'})."
        )
        response_type = TABLE

    return Plan(
        intent_id="catalogo_productos",
        category="Productos",
        response_type=response_type,
        sql="\n".join(sql),
        params=params,
        columns=tuple(headers),
        description=description,
    )


def _match_generic(text: str) -> _GenericPlan | None:
    metric = next(
        (name for name, words in METRIC_KEYWORDS if any(w in text for w in words)),
        None,
    )
    group = next(
        (name for name, words in GROUP_KEYWORDS if any(w in text for w in words)),
        None,
    )
    employee = lookups.find_entity("employee", text)
    if metric is None and group is None:
        return None
    # Sin métrica, "empleado" suele ser un nombre que hay que buscar, no un grupo.
    if metric is None and group == "empleado" and employee is None:
        return None
    return _GenericPlan(
        metric=metric or "ventas",
        group=group,
        employee=employee,
        product=lookups.find_entity("product", text),
    )


def _build_generic(plan: _GenericPlan) -> Plan:
    expression, label = METRICS[plan.metric]

    clauses: list[str] = []
    params: tuple = ()
    if plan.product is not None:
        clauses.append("p.ProductName = ?")
        params += (plan.product,)
    if plan.employee is not None:
        clauses.append(
            "e.EmployeeID = (\n"
            "    SELECT EmployeeID FROM Employees\n"
            "    WHERE FirstName || ' ' || LastName = ?\n"
            ")"
        )
        params += (plan.employee,)
    where = "\nWHERE " + "\n  AND ".join(clauses) if clauses else ""
    filters = []
    if plan.product is not None:
        filters.append(f"producto {plan.product}")
    if plan.employee is not None:
        filters.append(f"empleado {plan.employee}")
    scope = f" filtrado por {', '.join(filters)}" if filters else ""

    if plan.group is None:
        return Plan(
            intent_id=f"generico_{plan.metric}",
            category="Ventas generales",
            response_type=SCALAR_NUMBER,
            sql=f"SELECT {expression} AS \"{label}\"\n{GENERIC_JOINS}{where}",
            params=params,
            columns=(label,),
            description=(
                f"Total de {label.lower()} sobre los pedidos de Northwind{scope}."
            ),
        )

    # Un solo producto: ficha con su precio y la métrica pedida.
    if plan.group == "producto" and plan.product is not None:
        sql = (
            'SELECT p.ProductName AS "producto",\n'
            '       p.Price AS "precio",\n'
            f'       {expression} AS "{label}"\n'
            f"{GENERIC_JOINS}{where}\n"
            "GROUP BY p.ProductID"
        )
        return Plan(
            intent_id=f"generico_{plan.metric}_{plan.group}",
            category="Ventas generales",
            response_type=SINGLE_ROW,
            sql=sql,
            params=params,
            columns=("producto", "precio", label),
            description=(
                f"{label} del producto {plan.product} con su precio."
            ),
        )

    column, header = GROUPS[plan.group]
    if plan.group == "mes":
        order, limit = "ORDER BY 1 ASC", MAX_ROWS * 12
    else:
        order, limit = "ORDER BY 2 DESC", MAX_ROWS

    sql = (
        f"SELECT {column} AS \"{header}\", {expression} AS \"{label}\"\n"
        f"{GENERIC_JOINS}{where}\n"
        f"GROUP BY 1\n{order}\nLIMIT {limit}"
    )
    return Plan(
        intent_id=f"generico_{plan.metric}_{plan.group}",
        category="Ventas generales",
        response_type=GROUPED_LIST,
        sql=sql,
        params=params,
        columns=(header, label),
        description=(
            f"{label} agrupadas por {header.lower()}, de mayor a menor{scope}."
        ),
    )


# ------------------------------------------------------------------ respuesta


def _format(value: Any) -> str:
    if value is None:
        return "sin datos"
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", " ")
    return str(value)


def explain(plan: Plan, rows: list[dict]) -> str:
    """Frase que acompaña al resultado."""
    if not rows and plan.response_type not in SCALARS:
        return f"{plan.description} No se encontraron resultados."

    if plan.response_type in SCALARS:
        value = rows[0][plan.columns[0]] if rows else None
        text = f"{plan.description} Resultado: {_format(value)}."
    elif plan.response_type == SINGLE_ROW:
        text = f"{plan.description} Se encontró un único resultado."
    else:
        text = f"{plan.description} Se muestran {len(rows)} fila(s)."
    return text + plan.scope_note


def _chart(plan: Plan, rows: list[dict]) -> dict:
    """Serie para el gráfico: solo listas de dos columnas con valores numéricos."""
    if plan.response_type not in (GROUPED_LIST, ROW_LIST) or len(plan.columns) < 2:
        return {"labels": [], "values": []}

    labels = [row[plan.columns[0]] for row in rows]
    values = [row[plan.columns[1]] for row in rows]
    if not all(isinstance(value, (int, float)) for value in values):
        return {"labels": [], "values": []}
    return {"labels": labels, "values": values}


def build_response(question: str, plan: Plan, rows: list[dict]) -> dict:
    """Arma la respuesta según el tipo: valor único, objeto o lista."""
    if plan.response_type == "invalid":
        return invalid_answer(question, plan.description)

    data = [
        {column: row[column] for column in plan.columns if column in row}
        for row in rows
    ]

    response = {
        "question": question,
        "responseType": plan.response_type,
        "category": plan.category,
        "columns": list(plan.columns),
        "rows": data,
        "chart": _chart(plan, data),
        "sql": plan.sql,
        "params": list(plan.params),
        "explanation": explain(plan, data),
    }

    if plan.response_type in SCALARS:
        response["value"] = data[0][plan.columns[0]] if data else None
    elif plan.response_type == SINGLE_ROW:
        response["value"] = data[0] if data else None
    elif plan.response_type == VALUE_LIST:
        response["values"] = [row[plan.columns[0]] for row in data]

    return response


def denied_answer(question: str, reason: str) -> dict:
    """Respuesta cuando el rol no puede ver esos datos."""
    return {
        "question": question,
        "responseType": "denied",
        "category": "Sin permisos",
        "columns": [],
        "rows": [],
        "chart": {"labels": [], "values": []},
        "sql": "",
        "params": [],
        "explanation": reason,
    }


def invalid_answer(question: str, reason: str) -> dict:
    """Respuesta cuando la consulta por cláusulas no es válida."""
    return {
        "question": question,
        "responseType": "invalid",
        "category": "Consulta no válida",
        "columns": [],
        "rows": [],
        "chart": {"labels": [], "values": []},
        "sql": "",
        "params": [],
        "explanation": reason,
    }


def unsupported_answer(question: str) -> dict:
    """Respuesta cuando la pregunta no coincide con ninguna intención."""
    return {
        "question": question,
        "responseType": "unsupported",
        "category": "Sin categoría",
        "columns": [],
        "rows": [],
        "chart": {"labels": [], "values": []},
        "sql": "",
        "params": [],
        "explanation": (
            "Todavía no sé responder esa pregunta. Entiendo preguntas sobre "
            "ventas, pedidos, unidades, productos, categorías, clientes, "
            "empleados, proveedores, transportistas y usuarios. Ejemplos: "
            + " · ".join(EXAMPLES)
        ),
    }
