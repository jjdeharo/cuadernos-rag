"""Repara artefactos de la extracción de PDF en los documentos de docs/.

Los PDF maquetados parten palabras al final de renglón. Al extraer el texto
esas particiones quedan dentro de la palabra ("protec­ ción", "per­ sonales"),
lo que rompe tanto la búsqueda léxica como los embeddings.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rag

# Guion suave (U+00AD) usado como partición: siempre es un artefacto.
SOFT = re.compile(r"­\s*")
# Guion ASCII entre minúsculas con espacio detrás: partición de renglón.
HARD = re.compile(r"([a-záéíóúñü])-\s+([a-záéíóúñü])")
# Espacios repetidos dentro de una línea.
SPACES = re.compile(r"[ \t]{2,}")
# Caracteres de control que algunos PDF arrastran (excepto tab y salto).
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanear(text: str) -> str:
    """Elimina lo que ni siquiera es texto válido.

    Las fuentes mal empotradas de algunos PDF producen surrogates sueltos
    (U+D800–U+DFFF) que no se pueden ni escribir en un fichero UTF-8.
    """
    text = text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    return CTRL.sub("", text)


def clean(text: str) -> tuple[str, dict]:
    text = sanear(text)
    n_soft = len(SOFT.findall(text))
    text = SOFT.sub("", text)
    n_hard = len(HARD.findall(text))
    text = HARD.sub(r"\1\2", text)
    text = SPACES.sub(" ", text)
    return text, {"soft": n_soft, "hard": n_hard}


def main() -> None:
    total = {"soft": 0, "hard": 0}
    for f in sorted(rag.DOCS.glob("*.md")):
        meta, body = rag.parse_doc(f)
        nuevo, n = clean(body)
        if n["soft"] or n["hard"]:
            cabecera = "---\n" + "".join(
                f"{k}: {v}\n" for k, v in meta.items()
            ) + "---\n\n"
            f.write_text(cabecera + nuevo, encoding="utf-8")
            print(f"  {n['soft']:>5} suaves  {n['hard']:>5} duros   {f.stem[:45]}")
        total["soft"] += n["soft"]
        total["hard"] += n["hard"]
    print(f"\nReparadas {total['soft'] + total['hard']} particiones de palabra.")


if __name__ == "__main__":
    main()
