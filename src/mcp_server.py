# cuadernos-rag — Copyright (C) 2026 Juan José de Haro
# Software libre bajo licencia AGPL-3.0-or-later; ver el fichero LICENSE.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Servidor MCP: expone el corpus a Claude Code / Codex CLI como herramientas.

Registro en Claude Code:
    claude mcp add ia-educacion -- /ruta/al/proyecto/.venv/bin/python /ruta/src/mcp_server.py
"""
import functools
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).parent))
import rag
from mcp.server import MCPServer
from pydantic import Field

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

mcp = MCPServer("ia-educacion", version="1.0.0", instructions=INSTRUCCIONES)


def responde_al_fallo(fn):
    """Convierte los fallos previsibles en respuestas de texto.

    Que no haya índice o que el nombre del corpus no exista no es una avería
    del servidor: es algo que el modelo puede corregir en la llamada siguiente
    si se lo contamos, mientras que un error de herramienta sólo lo bloquea.
    """
    @functools.wraps(fn)
    def envuelta(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            return str(e)

    return envuelta


def corpus_indexado(nombre: str | None) -> rag.Corpus:
    """Resuelve un corpus y comprueba que tenga un índice utilizable."""
    elegido = rag.resolve_corpus(nombre)
    try:
        pasajes = elegido.stats()["chunks"] if elegido.indexed() else 0
    except sqlite3.OperationalError:
        pasajes = 0   # base a medio crear: para el caso, como si no hubiera
    if not pasajes:
        raise ValueError(
            f"El corpus '{elegido.name}' no está indexado. Créalo con:"
            f" python src/index.py --corpus {elegido.name}"
        )
    return elegido


@mcp.tool()
@responde_al_fallo
def buscar(consulta: str,
           k: Annotated[int, Field(ge=1, le=12)] = 8,
           documento: str | None = None,
           corpus: str | None = None) -> str:
    """Busca pasajes relevantes en el corpus sobre uso ético, legal y responsable
    de la IA en educación (RGPD, LOPDGDD, Reglamento europeo de IA, propiedad
    intelectual, guías INTEF/AEPD/UNESCO, marcos de competencia digital).

    Devuelve los pasajes con un identificador citable `[slug#id]`. Haz varias
    búsquedas con formulaciones distintas si la primera no basta.

    Args:
        consulta: pregunta o descripción de lo que buscas, en lenguaje natural.
        k: número de pasajes a devolver (por defecto 8, máximo 12).
        documento: opcional, restringe la búsqueda a un documento por su slug.
        corpus: opcional, nombre de otro corpus (ver `listar_corpus`).
    """
    elegido = corpus_indexado(corpus)
    res = elegido.search(consulta, k=k, doc=documento)
    if not res:
        return "Sin resultados. Prueba otra formulación o términos más generales."
    return rag.format_context(res, elegido.titulos)


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
@responde_al_fallo
def listar_fuentes(corpus: str | None = None) -> str:
    """Lista los documentos de un corpus con su slug y su tamaño en pasajes.

    Args:
        corpus: opcional, nombre del corpus (ver `listar_corpus`).
    """
    elegido = corpus_indexado(corpus)
    s = elegido.stats()
    lineas = [f"{s['chunks']} pasajes en {len(s['docs'])} documentos:", ""]
    for d in s["docs"]:
        lineas.append(f"- {d['doc_slug']}  ({d['n']} pasajes) — {d['doc_title']}")
    return "\n".join(lineas)


@mcp.tool()
@responde_al_fallo
def leer_pasaje(chunk_id: Annotated[int, Field(ge=1)],
                contexto: Annotated[int, Field(ge=0, le=20)] = 1,
                corpus: str | None = None) -> str:
    """Lee un pasaje concreto por su id, junto con los pasajes contiguos.

    Úsalo cuando un resultado de `buscar` aparece cortado y necesitas el texto
    completo alrededor de una cita.

    Args:
        chunk_id: el número que aparece tras `#` en el identificador del pasaje.
        contexto: cuántos pasajes vecinos incluir a cada lado (por defecto 1).
        corpus: opcional, nombre del corpus en el que buscar el pasaje.
    """
    elegido = corpus_indexado(corpus)
    db = elegido.connect()
    try:
        row = db.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if not row:
            return f"No existe el pasaje {chunk_id}."
        anteriores = db.execute(
            "SELECT * FROM chunks WHERE doc_slug = ? AND id < ?"
            " ORDER BY id DESC LIMIT ?",
            (row["doc_slug"], chunk_id, contexto),
        ).fetchall()
        posteriores = db.execute(
            "SELECT * FROM chunks WHERE doc_slug = ? AND id > ?"
            " ORDER BY id LIMIT ?",
            (row["doc_slug"], chunk_id, contexto),
        ).fetchall()
        vecinos = [*reversed(anteriores), row, *posteriores]
        return rag.format_context([dict(v) for v in vecinos], elegido.titulos)
    finally:
        db.close()


@mcp.tool()
@responde_al_fallo
def leer_documento(
    slug: str,
    desde: Annotated[int, Field(ge=0)] = 0,
    longitud: Annotated[int, Field(ge=1, le=60000)] = 20000,
    corpus: str | None = None,
) -> str:
    """Lee un documento completo del corpus por tramos de caracteres.

    Úsalo cuando necesites recorrer un texto entero (por ejemplo, para localizar
    todos los artículos de una norma) en lugar de buscar pasajes sueltos.

    Args:
        slug: identificador del documento (ver `listar_fuentes`).
        desde: posición inicial en caracteres.
        longitud: cuántos caracteres devolver (máximo 60000).
        corpus: opcional, nombre del corpus que contiene el documento.
    """
    docs = rag.resolve_corpus(corpus).docs
    documentos = list(docs.glob("*.md"))
    exactas = [p for p in documentos if p.stem == slug]
    if exactas:
        ruta = exactas[0]
    else:
        coincidencias = [p for p in documentos if slug.lower() in p.stem.lower()]
        if len(coincidencias) != 1:
            nombres = [p.stem for p in coincidencias]
            return f"No encuentro '{slug}'. Candidatos: {nombres or 'ninguno'}"
        ruta = coincidencias[0]
    _, cuerpo = rag.parse_doc(ruta)
    trozo = cuerpo[desde : desde + longitud]
    fin = desde + len(trozo)
    cola = f"\n\n[…continúa en {fin} de {len(cuerpo)} caracteres]" if fin < len(cuerpo) else ""
    return f"# {ruta.stem} (caracteres {desde}-{fin})\n\n{trozo}{cola}"


def precargar_modelos() -> None:
    """Carga los dos modelos en cuanto arranca el servidor, en segundo plano.

    Cargarlos cuesta unos 15 segundos y, si se espera a la primera búsqueda,
    esos segundos se los come esa llamada. Hacerlo aquí no bloquea el
    handshake: cuando llegue la primera consulta lo normal es que ya estén.
    """
    def cargar():
        try:
            rag.embedder()
            rag.reranker()
        except Exception as e:                      # pragma: no cover
            print(f"No se pudieron precargar los modelos: {e}", file=sys.stderr)

    threading.Thread(target=cargar, daemon=True).start()


if __name__ == "__main__":
    precargar_modelos()
    mcp.run()
