"""Resuelve entidades de la pregunta (país, categoría, producto, rol).

Los valores reales se leen de la base y se cachean. Además de los valores de la
base se aceptan alias en inglés y sinónimos en español, porque la base está en
español pero las preguntas pueden usar cualquiera de los dos idiomas.

Los nombres de producto se buscan primero exactos y, si no aparecen, por
parecido palabra a palabra (``Queso Kabrales`` → ``Queso Cabrales``).
"""

import re
from difflib import SequenceMatcher
from functools import lru_cache

from analytics.text import normalize
from db.database import connection

# Origen de cada tipo de entidad: (tabla, columna). Solo constantes internas.
SOURCES = {
    "country": ("Customers", "Country"),
    "category": ("Categories", "CategoryName"),
    "product": ("Products", "ProductName"),
}

# Palabras que no identifican a un producto dentro de su nombre.
STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "y", "con", "para", "en",
    "a", "al", "the", "of",
}

# Parecido mínimo por palabra para aceptar un nombre mal escrito.
FUZZY_RATIO = 0.82

# Roles válidos del sistema; no se leen de la base para incluir ``user``.
ROLES = {
    "admin": "admin",
    "administrador": "admin",
    "administradora": "admin",
    "employee": "employee",
    "empleado": "employee",
    "empleada": "employee",
    "user": "user",
    "usuario": "user",
    "usuaria": "user",
}

ALIASES = {
    "country": {
        "germany": "Alemania",
        "alemania": "Alemania",
        "brazil": "Brasil",
        "brasil": "Brasil",
        "belgium": "Bélgica",
        "belgica": "Bélgica",
        "canada": "Canadá",
        "denmark": "Dinamarca",
        "dinamarca": "Dinamarca",
        "usa": "EE. UU.",
        "united states": "EE. UU.",
        "estados unidos": "EE. UU.",
        "ee uu": "EE. UU.",
        "spain": "España",
        "espana": "España",
        "finland": "Finlandia",
        "finlandia": "Finlandia",
        "france": "Francia",
        "francia": "Francia",
        "ireland": "Irlanda",
        "irlanda": "Irlanda",
        "italy": "Italia",
        "italia": "Italia",
        "mexico": "México",
        "norway": "Noruega",
        "noruega": "Noruega",
        "poland": "Polonia",
        "polonia": "Polonia",
        "portugal": "Portugal",
        "uk": "Reino Unido",
        "united kingdom": "Reino Unido",
        "reino unido": "Reino Unido",
        "sweden": "Suecia",
        "suecia": "Suecia",
        "switzerland": "Suiza",
        "suiza": "Suiza",
        "venezuela": "Venezuela",
        "argentina": "Argentina",
        "austria": "Austria",
    },
    "category": {
        "beverages": "Bebidas",
        "bebida": "Bebidas",
        "bebidas": "Bebidas",
        "condiments": "Condimentos",
        "condimento": "Condimentos",
        "condimentos": "Condimentos",
        "confections": "Confitería",
        "confiteria": "Confitería",
        "dulces": "Confitería",
        "postres": "Confitería",
        "dairy products": "Productos lácteos",
        "lacteos": "Productos lácteos",
        "grains cereals": "Granos/Cereales",
        "granos": "Granos/Cereales",
        "cereales": "Granos/Cereales",
        "meat poultry": "Carne/Aves",
        "carne": "Carne/Aves",
        "carnes": "Carne/Aves",
        "aves": "Carne/Aves",
        "produce": "Productos agrícolas",
        "productos agricolas": "Productos agrícolas",
        "verduras": "Productos agrícolas",
        "seafood": "Mariscos",
        "mariscos": "Mariscos",
        "pescados": "Mariscos",
    },
}


@lru_cache(maxsize=None)
def distinct_values(table: str, column: str) -> tuple[str, ...]:
    """Valores distintos de una columna de la base (cacheado)."""
    with connection() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM {table}"
            f" WHERE {column} IS NOT NULL ORDER BY 1"
        ).fetchall()
    return tuple(row[0] for row in rows)


@lru_cache(maxsize=None)
def employee_names() -> tuple[str, ...]:
    """Nombre completo de cada empleado, tal como aparece en la base."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT FirstName || ' ' || LastName AS nombre"
            " FROM Employees ORDER BY 1"
        ).fetchall()
    return tuple(row[0] for row in rows)


def _candidates(kind: str) -> list[tuple[str, object]]:
    if kind == "role":
        return [(normalize(alias), value) for alias, value in ROLES.items()]
    if kind == "employee":
        return [(normalize(name), name) for name in employee_names()]

    pairs = [(normalize(value), value) for value in distinct_values(*SOURCES[kind])]
    pairs += [
        (normalize(alias), value) for alias, value in ALIASES.get(kind, {}).items()
    ]
    return pairs


def _find_fuzzy_product(text: str) -> str | None:
    """Producto cuyo nombre se parece palabra a palabra al texto.

    Todas las palabras significativas del nombre deben aparecer (con un parecido
    alto) en la pregunta; gana el nombre más parecido en promedio.
    """
    words = [
        word
        for word in normalize(text).split()
        if len(word) >= 4 and word not in STOPWORDS
    ]
    if not words:
        return None

    best: tuple[float, str] | None = None
    for value in distinct_values(*SOURCES["product"]):
        tokens = [
            word
            for word in normalize(value).split()
            if len(word) >= 3 and word not in STOPWORDS
        ]
        if not tokens:
            continue
        ratios = [
            max(
                (SequenceMatcher(None, token, word).ratio() for word in words),
                default=0.0,
            )
            for token in tokens
        ]
        if min(ratios) < FUZZY_RATIO:
            continue
        score = sum(ratios) / len(ratios) + len(tokens) * 0.05
        if best is None or score > best[0]:
            best = (score, value)
    return None if best is None else best[1]


def _find_employee(norm: str) -> str | None:
    """Empleado nombrado por apellido o por nombre de pila.

    Solo se acepta una palabra suelta del nombre si es lo bastante larga para
    no confundirse con vocabulario de la pregunta.
    """
    best: tuple[int, str] | None = None
    for name in employee_names():
        for token in normalize(name).split():
            if len(token) < 4:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", norm):
                if best is None or len(token) > best[0]:
                    best = (len(token), name)
    return None if best is None else best[1]


def find_entity(kind: str, text: str) -> str | None:
    """Devuelve el valor de la base que mejor coincide con el texto.

    Se compara sobre el texto normalizado y gana la coincidencia más larga, para
    que ``productos lacteos`` no se confunda con ``productos agricolas``.
    """
    norm = normalize(text)
    best: tuple[int, object] | None = None
    for needle, value in _candidates(kind):
        if not needle:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
        if re.search(pattern, norm) and (best is None or len(needle) > best[0]):
            best = (len(needle), value)
    if best is not None:
        return best[1]
    if kind == "product":
        return _find_fuzzy_product(text)
    if kind == "employee":
        return _find_employee(norm)
    return None
