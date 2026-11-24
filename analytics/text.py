"""Normalización de texto compartida por el resolver y los lookups."""

import re
import unicodedata

# Variantes escritas de la misma palabra (``año`` se escribe de varias formas).
FIXES = {
    "anno": "ano",
    "annos": "anos",
    "anio": "ano",
    "anios": "anos",
    "probeedor": "proveedor",
    "probeedores": "proveedores",
    "provehedor": "proveedor",
    "provehedores": "proveedores",
    "trabajador": "empleado",
    "trabajadores": "empleados",
}


def normalize(text: str) -> str:
    """Minúsculas, sin acentos y sin signos: solo ``a-z0-9`` y espacios."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    plain = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    cleaned = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", plain)).strip()
    return " ".join(FIXES.get(word, word) for word in cleaned.split())
