"""Convierte documentos propios a Markdown y los deja en docs/.

    python src/importar.py informe.pdf apuntes.txt ~/tesis/*.pdf
    python src/importar.py --corpus ~/mi-otro-corpus  articulo.pdf

Formatos: PDF, TXT, MD, HTML. Después hay que ejecutar `index.py`, que sólo
procesará los documentos nuevos.
"""
import argparse
import html
import os
import re
import sys
from pathlib import Path


def slugify(nombre: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", nombre.lower()).strip("-")
    return base[:70] or "documento"


def a_markdown(ruta: Path) -> str:
    ext = ruta.suffix.lower()
    if ext == ".pdf":
        import pymupdf4llm

        return pymupdf4llm.to_markdown(str(ruta), show_progress=False)
    if ext in {".txt", ".md", ".markdown"}:
        return ruta.read_text(encoding="utf-8", errors="replace")
    if ext in {".html", ".htm"}:
        crudo = ruta.read_text(encoding="utf-8", errors="replace")
        crudo = re.sub(r"(?is)<(script|style).*?</\1>", " ", crudo)
        return html.unescape(re.sub(r"(?s)<[^>]+>", " ", crudo))
    raise ValueError(f"formato no soportado: {ext}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ficheros", nargs="+", type=Path)
    ap.add_argument("--corpus", type=Path,
                    help="carpeta de otro corpus (equivale a RAG_HOME)")
    a = ap.parse_args()

    if a.corpus:
        os.environ["RAG_HOME"] = str(a.corpus.expanduser().resolve())
    sys.path.insert(0, str(Path(__file__).parent))
    import rag
    from clean import clean

    rag.DOCS.mkdir(parents=True, exist_ok=True)
    (rag.BASE / "data").mkdir(parents=True, exist_ok=True)

    for f in a.ficheros:
        if not f.exists():
            print(f"  no existe: {f}")
            continue
        try:
            texto = a_markdown(f)
        except Exception as e:
            print(f"  fallo en {f.name}: {e}")
            continue
        texto, reparado = clean(texto)
        destino = rag.DOCS / f"{slugify(f.stem)}.md"
        destino.write_text(
            f"---\ntitle: {f.name}\ntype: {f.suffix.lstrip('.')}\n---\n\n{texto}",
            encoding="utf-8",
        )
        roto = reparado["soft"] + reparado["hard"]
        extra = f", {roto} particiones reparadas" if roto else ""
        print(f"  {len(texto):>9,} car.  {destino.name}{extra}")

    print(f"\nEn {rag.DOCS}. Ahora: python src/index.py"
          + (f"  (con RAG_HOME={rag.BASE})" if a.corpus else ""))


if __name__ == "__main__":
    main()
