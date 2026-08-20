# cuadernos-rag — Copyright (C) 2026 Juan José de Haro
# Software libre bajo licencia AGPL-3.0-or-later; ver el fichero LICENSE.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Gestiona corpus locales a partir de los cuadernos de NotebookLM.

    python src/cuadernos.py listar                  # tus cuadernos en NotebookLM
    python src/cuadernos.py corpus                  # tus corpus locales
    python src/cuadernos.py crear <id|url> [nombre] # descarga uno como corpus
    python src/cuadernos.py actualizar <nombre>     # vuelve a descargarlo
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rag
from clean import clean

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def nb(*args: str) -> dict:
    r = subprocess.run(["notebooklm", *args, "--json"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"notebooklm falló: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def slugify(texto: str, largo: int = 45) -> str:
    t = texto.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")]:
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:largo] or "cuaderno"


def extraer_id(texto: str) -> str:
    m = UUID.search(texto)
    if not m:
        sys.exit(f"No reconozco un id de cuaderno en '{texto}'")
    return m.group(0)


def descargar(corpus: rag.Corpus, notebook_id: str) -> int:
    """Vuelca las fuentes de un cuaderno en docs/ del corpus."""
    listado = nb("source", "list", "-n", notebook_id)
    corpus.ensure()
    corpus.save_meta(
        notebook_id=notebook_id,
        title=listado["notebook_title"],
        n_sources=listado["count"],
    )
    print(f"{listado['notebook_title']} — {listado['count']} fuentes")

    vistos, total = set(), 0
    for s in listado["sources"]:
        if s["status"] != "ready":
            print(f"  omitida ({s['status']}): {s['title'][:55]}")
            continue
        full = nb("source", "fulltext", s["id"], "-n", notebook_id)
        texto, rep = clean(full["content"])
        slug = slugify(s["title"].rsplit(".", 1)[0], 70)
        vistos.add(slug)
        (corpus.docs / f"{slug}.md").write_text(
            f"---\ntitle: {s['title']}\nsource_id: {s['id']}\n"
            f"type: {s['type']}\n---\n\n{texto}",
            encoding="utf-8",
        )
        roto = rep["soft"] + rep["hard"]
        print(f"  {len(texto):>9,} car.  {slug}.md"
              + (f"  ({roto} reparadas)" if roto else ""))
        total += 1

    for viejo in corpus.docs.glob("*.md"):
        if viejo.stem not in vistos:
            viejo.unlink()
            print(f"  eliminado (ya no está en el cuaderno): {viejo.name}")
    return total


def cmd_listar(_) -> None:
    datos = nb("list")
    print(f"{datos['count']} cuadernos en NotebookLM:\n")
    locales = {c.meta.get("notebook_id") for c in rag.list_corpus()}
    for n in datos["notebooks"]:
        marca = "✓" if n["id"] in locales else " "
        print(f" {marca} {n['id'][:8]}  {n['title'][:70]}")
    print("\n(✓ = ya lo tienes como corpus local)")


def cmd_corpus(_) -> None:
    todos = rag.list_corpus()
    if not todos:
        print("No hay ningún corpus todavía.")
        return
    print(f"{len(todos)} corpus locales:\n")
    for c in todos:
        s = c.stats()
        n_docs = len(list(c.docs.glob("*.md")))
        estado = f"{s['chunks']} pasajes" if s["chunks"] else "SIN INDEXAR"
        print(f"  {c.name:<45} {n_docs:>3} docs · {estado}")
        if c.title != c.name:
            print(f"    └ {c.title[:72]}")


def cmd_crear(a) -> None:
    notebook_id = extraer_id(a.cuaderno)
    listado = nb("source", "list", "-n", notebook_id)
    nombre = a.nombre or slugify(listado["notebook_title"])
    corpus = rag.Corpus(rag.CORPUS_DIR / nombre)
    if corpus.docs.exists() and any(corpus.docs.glob("*.md")) and not a.forzar:
        sys.exit(f"El corpus '{nombre}' ya existe. Usa 'actualizar' o --forzar.")
    descargar(corpus, notebook_id)
    print(f"\nCorpus '{nombre}' creado en {corpus.base}")
    print(f"Ahora: python src/index.py --corpus {nombre}")


def cmd_actualizar(a) -> None:
    corpus = rag.resolve_corpus(a.nombre)
    notebook_id = corpus.meta.get("notebook_id")
    if not notebook_id:
        sys.exit(f"El corpus '{corpus.name}' no viene de NotebookLM.")
    descargar(corpus, notebook_id)
    print(f"\nAhora: python src/index.py --corpus {corpus.name}"
          "   (sólo reindexará lo que haya cambiado)")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar", help="cuadernos de tu cuenta").set_defaults(f=cmd_listar)
    sub.add_parser("corpus", help="corpus locales").set_defaults(f=cmd_corpus)
    c = sub.add_parser("crear", help="crear un corpus desde un cuaderno")
    c.add_argument("cuaderno", help="id o URL del cuaderno")
    c.add_argument("nombre", nargs="?", help="nombre del corpus")
    c.add_argument("--forzar", action="store_true")
    c.set_defaults(f=cmd_crear)
    u = sub.add_parser("actualizar", help="re-descargar un corpus")
    u.add_argument("nombre")
    u.set_defaults(f=cmd_actualizar)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
