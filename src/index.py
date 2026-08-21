# cuadernos-rag — Copyright (C) 2026 Juan José de Haro
# Software libre bajo licencia AGPL-3.0-or-later; ver el fichero LICENSE.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Indexa los documentos de docs/, recalculando sólo lo que ha cambiado.

    python src/index.py            # incremental (lo habitual)
    python src/index.py --full     # reconstruye el índice entero
    python src/index.py --status   # qué cambiaría, sin tocar nada
"""
import argparse
import sys
import time
from pathlib import Path

import sqlite_vec

sys.path.insert(0, str(Path(__file__).parent))
import rag


def plan(db) -> tuple[list, list, list]:
    """Compara docs/ con lo ya indexado: (nuevos, modificados, borrados)."""
    en_disco = {f.stem: f for f in sorted(rag.DOCS.glob("*.md"))}
    indexados = {
        r["slug"]: r["hash"]
        for r in db.execute("SELECT slug, hash FROM documents")
    }
    nuevos, modificados = [], []
    for slug, f in en_disco.items():
        if slug not in indexados:
            nuevos.append(f)
        elif rag.doc_hash(f) != indexados[slug]:
            modificados.append(f)
    borrados = [s for s in indexados if s not in en_disco]
    return nuevos, modificados, borrados


def indexar(db, ficheros: list[Path]) -> int:
    """Trocea, embebe e inserta los documentos indicados."""
    total = 0
    for n_doc, f in enumerate(ficheros, 1):
        chunks = rag.chunk_document(f)
        if not chunks:
            # Si antes tenía contenido, no podemos dejar sus pasajes antiguos
            # respondiendo búsquedas. Registrarlo con cero pasajes evita además
            # intentar reindexarlo en cada ejecución.
            rag.drop_document(db, f.stem)
            meta, _ = rag.parse_doc(f)
            db.execute(
                "INSERT INTO documents(slug, title, hash, n_chunks)"
                " VALUES (?,?,?,0)",
                (f.stem, meta.get("title", f.stem), rag.doc_hash(f)),
            )
            db.commit()
            print(f"  [{n_doc}/{len(ficheros)}] {f.stem}: vacío, retirado del índice")
            continue
        rag.drop_document(db, f.stem)  # por si es una reindexación
        cur = db.executemany(
            "INSERT INTO chunks(doc_slug, doc_title, source_id, section, text,"
            " start_char, end_char) VALUES (?,?,?,?,?,?,?)",
            [(c.doc_slug, c.doc_title, c.source_id, c.section, c.text,
              c.start_char, c.end_char) for c in chunks],
        )
        ids = [r[0] for r in db.execute(
            "SELECT id FROM chunks WHERE doc_slug = ? ORDER BY id", (f.stem,))]

        print(f"  [{n_doc}/{len(ficheros)}] {f.stem}: {len(chunks)} pasajes…",
              end="", flush=True)
        t0 = time.time()
        vectores = rag.embed_passages([c.contextual() for c in chunks])
        db.executemany(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?,?)",
            [(i, sqlite_vec.serialize_float32(v)) for i, v in zip(ids, vectores)],
        )
        con_vector = db.execute(
            "SELECT COUNT(*) FROM chunks c JOIN chunks_vec v ON v.chunk_id = c.id"
            " WHERE c.doc_slug = ?", (f.stem,)
        ).fetchone()[0]
        if con_vector != len(chunks):
            raise RuntimeError(
                f"Índice vectorial incompleto para {f.stem}: "
                f"{con_vector}/{len(chunks)}"
            )
        db.execute(
            "INSERT OR REPLACE INTO documents(slug, title, hash, n_chunks)"
            " VALUES (?,?,?,?)",
            (f.stem, chunks[0].doc_title, rag.doc_hash(f), len(chunks)),
        )
        db.commit()
        el = time.time() - t0
        print(f" {el:.0f}s ({len(chunks)/el:.1f}/s)")
        total += len(chunks)
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", help="nombre o ruta del corpus (por defecto, este)")
    ap.add_argument("--full", action="store_true", help="reconstruir todo")
    ap.add_argument("--status", action="store_true", help="sólo informar")
    ap.add_argument("--adopt", action="store_true",
                    help="registrar como ya indexados los documentos que"
                         " tienen pasajes en la base pero no huella")
    a = ap.parse_args()

    corpus = rag.resolve_corpus(a.corpus).ensure()
    rag.DOCS, rag.DB_PATH = corpus.docs, corpus.db_path  # rutas de este corpus
    db = corpus.connect()
    rag.create_schema(db, reset=a.full)

    if a.adopt:
        adoptados, incompletos = 0, []
        for f in sorted(rag.DOCS.glob("*.md")):
            fila = db.execute(
                "SELECT COUNT(*) n, MAX(doc_title) t FROM chunks WHERE doc_slug = ?",
                (f.stem,)).fetchone()
            # Un documento sólo está indexado si TODOS sus pasajes tienen
            # vector: la tabla de pasajes se llena antes que la de embeddings.
            con_vector = db.execute(
                "SELECT COUNT(*) c FROM chunks c JOIN chunks_vec v"
                " ON v.chunk_id = c.id WHERE c.doc_slug = ?", (f.stem,)
            ).fetchone()["c"]
            if fila["n"] and con_vector < fila["n"]:
                incompletos.append(f"{f.stem} ({con_vector}/{fila['n']})")
                continue
            if fila["n"]:
                db.execute(
                    "INSERT OR REPLACE INTO documents(slug, title, hash, n_chunks)"
                    " VALUES (?,?,?,?)",
                    (f.stem, fila["t"], rag.doc_hash(f), fila["n"]))
                adoptados += 1
        db.commit()
        print(f"Adoptados {adoptados} documentos ya indexados.")
        if incompletos:
            print("Sin vectores completos (se reindexarán):")
            for x in incompletos:
                print(f"  - {x}")
        return

    nuevos, modificados, borrados = plan(db)
    if a.full:
        nuevos = sorted(rag.DOCS.glob("*.md"))
        modificados, borrados = [], []

    if a.status:
        print(f"nuevos:      {[f.stem for f in nuevos] or '—'}")
        print(f"modificados: {[f.stem for f in modificados] or '—'}")
        print(f"borrados:    {borrados or '—'}")
        s = rag.stats(db)
        print(f"\nÍndice actual: {s['chunks']} pasajes, {len(s['docs'])} documentos")
        return

    if not (nuevos or modificados or borrados):
        # También repara una interrupción ocurrida después de confirmar los
        # documentos y antes de reconstruir el índice léxico.
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        db.commit()
        s = rag.stats(db)
        print(f"Sin cambios. {s['chunks']} pasajes indexados.")
        return

    for slug in borrados:
        n = rag.drop_document(db, slug)
        print(f"  eliminado {slug} ({n} pasajes)")

    pendientes = nuevos + modificados
    if pendientes:
        print(f"Indexando {len(pendientes)} documento(s)"
              f" ({len(nuevos)} nuevos, {len(modificados)} modificados)…")
        indexar(db, pendientes)

    # El índice léxico se reconstruye entero: es cuestión de segundos.
    db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    db.commit()

    s = rag.stats(db)
    print(f"\nListo: {s['chunks']} pasajes de {len(s['docs'])} documentos"
          f" · {rag.DB_PATH.stat().st_size / 1e6:.1f} MB")
    db.close()


if __name__ == "__main__":
    main()
