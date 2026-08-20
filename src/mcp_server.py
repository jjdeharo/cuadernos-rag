"""Servidor MCP: expone el corpus a Claude Code / Codex CLI como herramientas.

Registro en Claude Code:
    claude mcp add ia-educacion -- /ruta/al/proyecto/.venv/bin/python /ruta/src/mcp_server.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rag
from mcp.server import MCPServer

# Estas instrucciones viajan con el servidor: las recibe cualquier cliente al
# conectarse (Claude Code, Codex, Antigravity…). Es la diferencia entre dar
# acceso al corpus y decir cómo debe usarse.
INSTRUCCIONES = """
Este servidor da acceso a un corpus sobre uso ético, legal y responsable de la
IA en educación: RGPD, LOPDGDD, Reglamento europeo de IA, Ley de Propiedad
Intelectual, y guías de INTEF, AEPD y UNESCO.

Cómo responder preguntas sobre este material:

1. Nunca respondas de memoria. Toda afirmación sobre normativa debe salir de
   una búsqueda en el corpus.
2. Busca varias veces. Una sola consulta rara vez basta: reformula con el
   vocabulario de la norma ("responsable del tratamiento", no "quien gestiona
   los datos") y cruza fuentes.
3. Cita siempre con el identificador [slug#id] que devuelve `buscar`, y nombra
   la norma y el artículo cuando el pasaje los indique.
4. Distingue las fuentes. El RGPD, la LOPDGDD, el Reglamento de IA y la Ley de
   Propiedad Intelectual dicen cosas distintas: no las mezcles en una misma
   afirmación sin señalar cuál dice qué.
5. Si el corpus no cubre la pregunta, dilo en lugar de rellenar el hueco.
"""

mcp = MCPServer("ia-educacion", instructions=INSTRUCCIONES)


@mcp.tool()
def buscar(consulta: str, k: int = 8, documento: str | None = None,
           corpus: str | None = None) -> str:
    """Busca pasajes relevantes en el corpus sobre uso ético, legal y responsable
    de la IA en educación (RGPD, LOPDGDD, Reglamento europeo de IA, propiedad
    intelectual, guías INTEF/AEPD/UNESCO, marcos de competencia digital).

    Devuelve los pasajes con un identificador citable `[slug#id]`. Haz varias
    búsquedas con formulaciones distintas si la primera no basta.

    Args:
        consulta: pregunta o descripción de lo que buscas, en lenguaje natural.
        k: número de pasajes a devolver (por defecto 8).
        documento: opcional, restringe la búsqueda a un documento por su slug.
        corpus: opcional, nombre de otro corpus (ver `listar_corpus`).
    """
    res = rag.resolve_corpus(corpus).search(consulta, k=k, doc=documento)
    if not res:
        return "Sin resultados. Prueba otra formulación o términos más generales."
    return rag.format_context(res)


@mcp.tool()
def listar_corpus() -> str:
    """Lista los corpus disponibles: cada uno es un conjunto de documentos
    independiente, con su propio índice."""
    todos = rag.list_corpus()
    if not todos:
        return "No hay ningún corpus."
    return "\n".join(
        f"- {c.name}: {c.title} ({c.stats()['chunks']} pasajes)" for c in todos
    )


@mcp.tool()
def listar_fuentes() -> str:
    """Lista los documentos del corpus con su slug y su tamaño en pasajes."""
    s = rag.stats()
    lineas = [f"{s['chunks']} pasajes en {len(s['docs'])} documentos:", ""]
    for d in s["docs"]:
        lineas.append(f"- {d['doc_slug']}  ({d['n']} pasajes) — {d['doc_title']}")
    return "\n".join(lineas)


@mcp.tool()
def leer_pasaje(chunk_id: int, contexto: int = 1) -> str:
    """Lee un pasaje concreto por su id, junto con los pasajes contiguos.

    Úsalo cuando un resultado de `buscar` aparece cortado y necesitas el texto
    completo alrededor de una cita.

    Args:
        chunk_id: el número que aparece tras `#` en el identificador del pasaje.
        contexto: cuántos pasajes vecinos incluir a cada lado (por defecto 1).
    """
    db = rag.connect()
    try:
        row = db.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if not row:
            return f"No existe el pasaje {chunk_id}."
        vecinos = db.execute(
            "SELECT * FROM chunks WHERE doc_slug = ? AND id BETWEEN ? AND ?"
            " ORDER BY id",
            (row["doc_slug"], chunk_id - contexto, chunk_id + contexto),
        ).fetchall()
        return rag.format_context([dict(v) for v in vecinos])
    finally:
        db.close()


@mcp.tool()
def leer_documento(slug: str, desde: int = 0, longitud: int = 20000) -> str:
    """Lee un documento completo del corpus por tramos de caracteres.

    Úsalo cuando necesites recorrer un texto entero (por ejemplo, para localizar
    todos los artículos de una norma) en lugar de buscar pasajes sueltos.

    Args:
        slug: identificador del documento (ver `listar_fuentes`).
        desde: posición inicial en caracteres.
        longitud: cuántos caracteres devolver (máximo 60000).
    """
    ruta = rag.DOCS / f"{slug}.md"
    if not ruta.exists():
        coincidencias = [p.stem for p in rag.DOCS.glob(f"*{slug}*.md")]
        if len(coincidencias) != 1:
            return f"No encuentro '{slug}'. Candidatos: {coincidencias or 'ninguno'}"
        ruta = rag.DOCS / f"{coincidencias[0]}.md"
    _, cuerpo = rag.parse_doc(ruta)
    longitud = min(longitud, 60000)
    trozo = cuerpo[desde : desde + longitud]
    fin = desde + len(trozo)
    cola = f"\n\n[…continúa en {fin} de {len(cuerpo)} caracteres]" if fin < len(cuerpo) else ""
    return f"# {ruta.stem} (caracteres {desde}-{fin})\n\n{trozo}{cola}"


if __name__ == "__main__":
    mcp.run()
