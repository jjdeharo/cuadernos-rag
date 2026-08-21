# cuadernos-rag — Copyright (C) 2026 Juan José de Haro
# Software libre bajo licencia AGPL-3.0-or-later; ver el fichero LICENSE.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Consulta el índice desde la terminal."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rag


def main() -> None:
    p = argparse.ArgumentParser(description="Busca en el corpus indexado.")
    p.add_argument("query", nargs="+", help="consulta en lenguaje natural")
    p.add_argument("-k", type=int, default=8, help="resultados a devolver")
    p.add_argument("-d", "--doc", help="filtrar por slug de documento")
    p.add_argument("-c", "--corpus", help="corpus a consultar")
    p.add_argument("--no-rerank", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--context", action="store_true",
                   help="imprime el bloque de contexto listo para un prompt")
    a = p.parse_args()

    q = " ".join(a.query)
    corpus = rag.resolve_corpus(a.corpus)
    res = corpus.search(q, k=a.k, doc=a.doc, rerank=not a.no_rerank)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif a.context:
        print(rag.format_context(res, corpus.titulos))
    else:
        if not res:
            print("Sin resultados.")
        for i, r in enumerate(res, 1):
            loc = f" · {r['section']}" if r["section"] else ""
            titulo = corpus.titulos.get(r["doc_slug"]) or rag.titulo_legible(
                r["doc_title"], r["doc_slug"])
            print(f"\n\033[1m{i}. [{r['score']:.3f}] {titulo}{loc}\033[0m")
            print(f"   id={r['doc_slug']}#{r['id']}  chars {r['start_char']}-{r['end_char']}")
            txt = " ".join(r["text"].split())
            print(f"   {txt[:400]}{'…' if len(txt) > 400 else ''}")


if __name__ == "__main__":
    main()
