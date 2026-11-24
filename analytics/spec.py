"""Consultas escritas con cláusulas: SELECT, *, FROM, JOIN, WHERE, GROUP BY,
ORDER BY y LIMIT.

El analizador **siempre busca las ocho cláusulas** y devuelve cuáles aparecen,
para decidir el camino:

1. Si falta ``SELECT`` o ``FROM`` la consulta no es de este modo y se prueba el
   catálogo de lenguaje natural.
2. Si están, se arma un ``QuerySpec`` (tablas, columnas, filtros, agrupación,
   orden y límite) y se valida contra la lista blanca del rol.
3. El SQL final lo compone este módulo a partir del spec; jamás se ejecuta el
   texto del usuario tal cual.

Las cláusulas ausentes se resuelven con valores por defecto explícitos: sin
``WHERE`` no hay filtro, sin ``GROUP BY`` hay un único agregado, sin
``ORDER BY`` se ordena por la primera columna y sin ``LIMIT`` se aplica el tope
de filas.
"""

import re
from dataclasses import dataclass

# Cláusulas que se buscan siempre en la consulta.
CLAUSES = (
    "select",
    "star",
    "from",
    "join",
    "where",
    "group_by",
    "order_by",
    "limit",
)

# Columnas disponibles por tabla (lista blanca).
TABLE_COLUMNS = {
    "Products": (
        "ProductID",
        "ProductName",
        "SupplierID",
        "CategoryID",
        "Unit",
        "Price",
    ),
    "Categories": ("CategoryID", "CategoryName", "Description"),
    "Suppliers": (
        "SupplierID",
        "SupplierName",
        "ContactName",
        "Address",
        "City",
        "PostalCode",
        "Country",
        "Phone",
    ),
    "Customers": (
        "CustomerID",
        "CustomerName",
        "ContactName",
        "Address",
        "City",
        "PostalCode",
        "Country",
    ),
    "Orders": ("OrderID", "CustomerID", "EmployeeID", "OrderDate", "ShipperID"),
    "OrderDetails": ("OrderDetailID", "OrderID", "ProductID", "Quantity"),
    "Employees": ("EmployeeID", "LastName", "FirstName", "BirthDate", "Notes"),
    "Shippers": ("ShipperID", "ShipperName", "Phone"),
    "Users": ("UserID", "name", "email", "role", "is_active", "created_at"),
}

# Tablas reservadas al administrador.
ADMIN_ONLY_TABLES = ("Users",)

# Nombres en español aceptados además de los reales.
SPANISH = {
    "id": "ProductID",
    "producto": "ProductName",
    "nombre": "ProductName",
    "precio": "Price",
    "unidad": "Unit",
    "categoria": "CategoryName",
    "proveedor": "SupplierName",
    "contacto": "ContactName",
    "cliente": "CustomerName",
    "pais": "Country",
    "ciudad": "City",
    "telefono": "Phone",
    "fecha": "OrderDate",
    "pedido": "OrderID",
    "orden": "OrderID",
    "cantidad": "Quantity",
    "unidades": "Quantity",
    "transportista": "ShipperName",
    "rol": "role",
    "correo": "email",
    "activo": "is_active",
    "nacimiento": "BirthDate",
}

# Columnas calculadas: nombre normalizado → (tabla, expresión con ``{a}``).
VIRTUAL = {
    "empleado": ("Employees", "{a}.FirstName || ' ' || {a}.LastName"),
}

# Tope duro de filas y tope por defecto cuando falta LIMIT.
MAX_LIMIT = 200
DEFAULT_LIMIT = 50

TABLES_BY_LOWER = {table.lower(): table for table in TABLE_COLUMNS}

_KEYWORD_RE = re.compile(
    r"\b(select|from|join|where|group\s+by|order\s+by|limit)\b", re.I
)

_AGG = re.compile(
    r"^(count|sum|avg|min|max)\s*\(\s*(distinct\s+)?(\*|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)"
    r"\s*\)(?:\s+as\s+([A-Za-z_]\w*))?$",
    re.I,
)
_ROUND = re.compile(
    r"^round\s*\(\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*,\s*([0-9])\s*\)"
    r"(?:\s+as\s+([A-Za-z_]\w*))?$",
    re.I,
)
_PLAIN = re.compile(
    r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)(?:\s+as\s+([A-Za-z_]\w*))?$", re.I
)
_TABLE = re.compile(
    r"^([A-Za-z_]\w*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?$", re.I
)
_CONDITION = re.compile(
    r"\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*"
    r"(<=|>=|<>|!=|=|<|>|like)\s*"
    r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[-+]?\d+(?:\.\d+)?|"
    r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)"
    r"\s*(and|or)?\s*",
    re.I,
)
_CLAUSE_WORD = {
    "select": "SELECT",
    "from": "FROM",
    "join": "JOIN",
    "where": "WHERE",
    "group_by": "GROUP BY",
    "order_by": "ORDER BY",
    "limit": "LIMIT",
}


class SpecError(Exception):
    """La consulta usa el modo por cláusulas pero no es válida."""


@dataclass(frozen=True)
class QuerySpec:
    """Consulta ya validada, antes de componer el SQL."""

    table: str
    alias: str
    joins: tuple[tuple[str, str, str], ...]
    select: tuple[tuple[str, str], ...]
    where: str
    params: tuple[object, ...]
    group_by: tuple[str, ...]
    order_by: tuple[str, ...]
    limit: int
    columns: tuple[str, ...]
    response_type: str


# ------------------------------------------------------------------ cláusulas


def clause_of(token: str) -> str:
    return "_".join(token.lower().split())


def detect(text: str) -> dict[str, bool]:
    """Qué cláusulas aparecen. Siempre devuelve las ocho."""
    found = {clause: False for clause in CLAUSES}
    for match in _KEYWORD_RE.finditer(text):
        found[clause_of(match.group())] = True
    found["star"] = "*" in text
    return found


def looks_like_clause_query(text: str) -> bool:
    found = detect(text)
    return found["select"] and found["from"]


def segments(text: str) -> dict[str, list[str]]:
    """Texto entre cada cláusula y la siguiente."""
    matches = list(_KEYWORD_RE.finditer(text))
    parts: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        parts.setdefault(clause_of(match.group()), []).append(
            text[match.end() : end].strip()
        )
    return parts


def _split_top(text: str, separator: str = ",") -> list[str]:
    """Separa por comas sin romper paréntesis ni cadenas."""
    items: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == separator and depth == 0:
            items.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return [item for item in items if item]


def allowed_tables(role: str) -> tuple[str, ...]:
    if role == "admin":
        return tuple(TABLE_COLUMNS)
    return tuple(table for table in TABLE_COLUMNS if table not in ADMIN_ONLY_TABLES)


# ------------------------------------------------------------------ validación


def _table_ref(text: str) -> tuple[str, str]:
    match = _TABLE.fullmatch(text.strip())
    if match is None:
        raise SpecError(f"No entiendo la tabla «{text.strip()}».")
    name, alias = match.group(1), match.group(2) or match.group(1)
    table = TABLES_BY_LOWER.get(name.lower())
    if table is None:
        raise SpecError(
            f"La tabla «{name}» no existe. Tablas disponibles: "
            + ", ".join(TABLE_COLUMNS)
            + "."
        )
    return table, alias


def _match_column(table: str, name: str) -> str | None:
    for column in TABLE_COLUMNS[table]:
        if column.lower() == name.lower():
            return column
    mapped = SPANISH.get(name.lower())
    if mapped is not None:
        for column in TABLE_COLUMNS[table]:
            if column == mapped:
                return column
    return None


def _alias_key(name: str, aliases: dict[str, str]) -> str | None:
    """Alias de la consulta, sin distinguir mayúsculas."""
    if name in aliases:
        return name
    for key in aliases:
        if key.lower() == name.lower():
            return key
    return None


def _resolve(ref: str, aliases: dict[str, str]) -> str:
    """Expresión SQL de una columna, validada contra las tablas de la consulta."""
    if "." in ref:
        prefix, name = ref.split(".", 1)
        alias = _alias_key(prefix, aliases)
        if alias is None:
            raise SpecError(f"El alias «{prefix}» no está en la consulta.")
        column = _match_column(aliases[alias], name)
        if column is None:
            raise SpecError(
                f"La columna «{name}» no existe en {aliases[alias]}."
            )
        return f"{alias}.{column}"

    name = ref.strip()
    matches: list[tuple[str, str]] = []
    for alias, table in aliases.items():
        column = _match_column(table, name)
        if column is not None:
            matches.append((alias, column))

    if not matches and name.lower() in VIRTUAL:
        table, expression = VIRTUAL[name.lower()]
        for alias, alias_table in aliases.items():
            if alias_table == table:
                return expression.format(a=alias)

    if not matches:
        raise SpecError(f"La columna «{name}» no existe en las tablas consultadas.")
    if len(matches) > 1:
        raise SpecError(
            f"La columna «{name}» es ambigua; escríbela como alias.columna."
        )
    alias, column = matches[0]
    return f"{alias}.{column}"


def _compile_condition(
    text: str, aliases: dict[str, str], params: list[object]
) -> str:
    text = text.strip()
    if not text:
        raise SpecError("La cláusula WHERE está vacía.")
    pieces: list[str] = []
    position = 0
    while position < len(text):
        match = _CONDITION.match(text, position)
        if match is None:
            raise SpecError(
                f"No entiendo la condición «{text[position:]}». Usa "
                "columna operador valor unidos por AND u OR."
            )
        pieces.append(_resolve(match.group(1), aliases))
        operator = match.group(2).upper()
        pieces.append("LIKE" if operator == "LIKE" else operator)
        raw = match.group(3)
        if raw[0] in "'\"":
            params.append(raw[1:-1].replace(raw[0] * 2, raw[0]))
            pieces.append("?")
        elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw):
            pieces.append(raw)
        else:
            pieces.append(_resolve(raw, aliases))
        if match.group(4):
            pieces.append(match.group(4).upper())
        position = match.end()
    return " ".join(pieces)


def _compile_select(
    items: list[str], aliases: dict[str, str]
) -> tuple[list[tuple[str, str]], bool]:
    """Expresiones del SELECT y si hay agregados."""
    output: list[tuple[str, str]] = []
    aggregated = False
    for item in items:
        if item == "*":
            for alias, table in aliases.items():
                for column in TABLE_COLUMNS[table]:
                    output.append((f"{alias}.{column}", column))
            continue
        star = re.fullmatch(r"([A-Za-z_]\w*)\.\*", item, re.I)
        if star is not None:
            prefix = star.group(1)
            key = _alias_key(prefix, aliases)
            if key is None:
                raise SpecError(f"El alias «{prefix}» no está en la consulta.")
            for column in TABLE_COLUMNS[aliases[key]]:
                output.append((f"{key}.{column}", column))
            continue

        match = _AGG.match(item)
        if match is not None:
            function = match.group(1).upper()
            distinct = bool(match.group(2))
            target = match.group(3)
            inner = "*" if target == "*" else _resolve(target, aliases)
            if inner == "*" and function != "COUNT":
                raise SpecError(f"{function}(*) no es válido.")
            expression = f"{function}({'DISTINCT ' if distinct else ''}{inner})"
            label = match.group(4) or (
                "total"
                if inner == "*"
                else f"{function.lower()}_{inner.split('.')[-1]}"
            )
            output.append((expression, label))
            aggregated = True
            continue

        match = _ROUND.match(item)
        if match is not None:
            inner = _resolve(match.group(1), aliases)
            expression = f"ROUND({inner}, {match.group(2)})"
            label = match.group(3) or f"redondeo_{match.group(1).split('.')[-1]}"
            output.append((expression, label))
            continue

        match = _PLAIN.match(item)
        if match is None:
            raise SpecError(
                f"No entiendo la columna «{item}». Solo acepto columnas, *, "
                "COUNT, SUM, AVG, MIN, MAX y ROUND."
            )
        expression = _resolve(match.group(1), aliases)
        label = match.group(2) or match.group(1).split(".")[-1]
        output.append((expression, label))
    return output, aggregated


def _compile_order(
    items: list[str], aliases: dict[str, str], labels: list[str]
) -> list[str]:
    output: list[str] = []
    for item in items:
        match = re.fullmatch(r"(.+?)(?:\s+(asc|desc))?", item.strip(), re.I)
        if match is None:
            raise SpecError(f"No entiendo el orden «{item}».")
        target = match.group(1).strip()
        direction = (match.group(2) or "ASC").upper()
        if target in labels:
            output.append(f'"{target}" {direction}')
        else:
            output.append(f"{_resolve(target, aliases)} {direction}")
    return output


def parse(text: str, role: str) -> QuerySpec:
    """Analiza y valida la consulta; lanza ``SpecError`` si no es válida."""
    parts = segments(text)
    if "select" not in parts or "from" not in parts:
        raise SpecError("La consulta necesita SELECT y FROM.")

    select_items = _split_top(parts["select"][0])
    if not select_items:
        raise SpecError("La cláusula SELECT está vacía.")

    table, alias = _table_ref(parts["from"][0])
    allowed = allowed_tables(role)
    if table not in allowed:
        raise SpecError(
            f"No tienes permisos para consultar la tabla {table}."
        )

    aliases: dict[str, str] = {alias: table}
    joins: list[tuple[str, str, str]] = []
    join_conditions: list[str] = []
    for raw in parts.get("join", []):
        match = re.fullmatch(r"(.+?)\s+on\s+(.+)", raw, re.I | re.S)
        if match is None:
            raise SpecError("Cada JOIN necesita «ON tabla.columna = tabla.columna».")
        joined, joined_alias = _table_ref(match.group(1))
        if joined not in allowed:
            raise SpecError(
                f"No tienes permisos para consultar la tabla {joined}."
            )
        aliases[joined_alias] = joined
        joins.append((joined, joined_alias, match.group(2).strip()))
        join_conditions.append(match.group(2).strip())

    params: list[object] = []
    select_sql, aggregated = _compile_select(select_items, aliases)

    where_sql = ""
    if parts.get("where"):
        where_sql = _compile_condition(parts["where"][0], aliases, params)

    group_by = tuple(
        _resolve(item, aliases) for item in _split_top(parts.get("group_by", [""])[0])
    ) if parts.get("group_by") else ()

    labels = [label for _, label in select_sql]
    order_by = (
        tuple(_compile_order(_split_top(parts["order_by"][0]), aliases, labels))
        if parts.get("order_by")
        else ()
    )

    limit = DEFAULT_LIMIT
    if parts.get("limit"):
        raw = parts["limit"][0].strip()
        if not re.fullmatch(r"\d+", raw):
            raise SpecError("LIMIT necesita un número entero.")
        limit = min(int(raw), MAX_LIMIT)

    conditions = list(join_conditions)
    if where_sql:
        conditions.append(where_sql)

    from_sql = f"FROM {table} {alias}"
    for joined, joined_alias, condition in joins:
        from_sql += f"\nJOIN {joined} {joined_alias} ON {condition}"

    lines = [
        "SELECT " + ",\n       ".join(f'{expr} AS "{label}"' for expr, label in select_sql),
        from_sql,
    ]
    if conditions:
        lines.append("WHERE " + "\n  AND ".join(conditions))
    if group_by:
        lines.append("GROUP BY " + ", ".join(group_by))
    if order_by:
        lines.append("ORDER BY " + ", ".join(order_by))
    if group_by or not aggregated:
        lines.append(f"LIMIT {limit}")

    if group_by:
        response_type = "grouped_list"
    elif aggregated:
        response_type = "single_row"
    else:
        response_type = "table"

    return QuerySpec(
        table=table,
        alias=alias,
        joins=tuple(joins),
        select=tuple(select_sql),
        where=where_sql,
        params=tuple(params),
        group_by=group_by,
        order_by=order_by,
        limit=limit,
        columns=tuple(labels),
        response_type=response_type,
    )


def plan_sql(spec: QuerySpec) -> str:
    """SQL final de un spec (ya validado)."""
    lines = [
        "SELECT "
        + ",\n       ".join(f'{expr} AS "{label}"' for expr, label in spec.select),
        f"FROM {spec.table} {spec.alias}",
    ]
    for joined, joined_alias, condition in spec.joins:
        lines.append(f"JOIN {joined} {joined_alias} ON {condition}")
    conditions = [condition for _, _, condition in spec.joins]
    if spec.where:
        conditions.append(spec.where)
    if conditions:
        lines.append("WHERE " + "\n  AND ".join(conditions))
    if spec.group_by:
        lines.append("GROUP BY " + ", ".join(spec.group_by))
    if spec.order_by:
        lines.append("ORDER BY " + ", ".join(spec.order_by))
    if spec.group_by or spec.response_type != "single_row":
        lines.append(f"LIMIT {spec.limit}")
    return "\n".join(lines)


def summary(text: str, spec: QuerySpec) -> str:
    """Descripción legible de la consulta armada."""
    found = detect(text)
    used = [name for name in CLAUSES if found[name]]
    missing = [name for name in CLAUSES if not found[name]]
    parts = [
        f"Consulta por cláusulas sobre {spec.table}",
        "con " + ", ".join(used),
    ]
    if missing:
        parts.append("sin " + ", ".join(missing))
    return ". ".join(parts) + "."
