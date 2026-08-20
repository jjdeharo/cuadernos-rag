# cuadernos-rag — Copyright (C) 2026 Juan José de Haro
# Software libre bajo licencia AGPL-3.0-or-later; ver el fichero LICENSE.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Re-descarga las fuentes del notebook de NotebookLM a docs/.

Uso:  python src/sync.py [notebook_id]
Luego:  python src/index.py   para reconstruir el índice.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rag
from clean import clean

NOTEBOOK_ID = "7037ce01-2663-49de-9eb6-8f26a7b4d649"


def nb(*args: str) -> dict:
    out = subprocess.run(
        ["notebooklm", *args, "--json"], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def slugify(title: str) -> str:
    base = title.rsplit(".", 1)[0].lower()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:70]


def main() -> None:
    notebook = sys.argv[1] if len(sys.argv) > 1 else NOTEBOOK_ID
    listado = nb("source", "list", "-n", notebook)
    print(f"{listado['notebook_title']}: {listado['count']} fuentes")

    rag.DOCS.mkdir(exist_ok=True)
    vistos = set()
    for s in listado["sources"]:
        if s["status"] != "ready":
            print(f"  omitida ({s['status']}): {s['title']}")
            continue
        full = nb("source", "fulltext", s["id"], "-n", notebook)
        texto, reparado = clean(full["content"])
        slug = slugify(s["title"])
        vistos.add(slug)
        destino = rag.DOCS / f"{slug}.md"
        cabecera = (
            f"---\ntitle: {s['title']}\nsource_id: {s['id']}\n"
            f"type: {s['type']}\n---\n\n"
        )
        destino.write_text(cabecera + texto, encoding="utf-8")
        roto = reparado["soft"] + reparado["hard"]
        aviso = f"  ({roto} particiones reparadas)" if roto else ""
        print(f"  {full['char_count']:>9,}  {destino.name}{aviso}")

    for viejo in rag.DOCS.glob("*.md"):
        if viejo.stem not in vistos:
            print(f"  (ya no está en el notebook: {viejo.name})")

    print("\nAhora ejecuta:  python src/index.py")


if __name__ == "__main__":
    main()
