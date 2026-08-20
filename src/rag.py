"""Núcleo del RAG: chunking, indexado y búsqueda híbrida."""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

ROOT = Path(__file__).resolve().parent.parent

# Un corpus son unos documentos y su índice. Cada cuaderno de NotebookLM se
# corresponde con uno, y todos comparten modelos y entorno de Python.
CORPUS_DIR = ROOT / "corpus"
BASE = Path(os.environ.get("RAG_HOME", ROOT)).expanduser().resolve()
DOCS = BASE / "docs"
DB_PATH = BASE / "data" / "index.db"

# Los modelos se comparten entre corpus y viven siempre en el proyecto:
# fastembed usaría /tmp, que Linux vacía en cada arranque (3,2 GB perdidos).
MODEL_CACHE = ROOT / "models"

EMBED_MODEL = "intfloat/multilingual-e5-large"
EMBED_DIM = 1024
RERANK_MODEL = "jinaai/jina-reranker-v2-base-multilingual"

# e5-large trunca a 512 tokens: pasajes más largos se embeberían a medias.
# ~330 palabras de castellano ≈ 480 tokens, con margen de seguridad.
CHUNK_WORDS = 330
OVERLAP_WORDS = 60
MIN_SECTION_WORDS = 60   # secciones más cortas se fusionan con la siguiente
STRUCTURED_MIN_SECTIONS = 12  # a partir de aquí tratamos el doc como articulado

# El reranker es un cross-encoder en CPU: su coste crece con el número de
# candidatos y, sobre todo, con la longitud de cada uno. Juzgar la relevancia
# no necesita el pasaje entero, así que lo recortamos antes de puntuarlo.
RERANK_CANDIDATES = 24
RERANK_MAX_CHARS = 1200

# Encabezados típicos de los textos legales y guías del corpus.
# Encabezados de textos articulados (normas europeas y españolas).
LEGAL_RE = re.compile(
    r"^\s*("
    r"(?:CAPÍTULO|TÍTULO|SECCIÓN|ANEXO|LIBRO|PARTE)\s+[IVXLC\d]+"
    r"|Artículo\s+\d+\s*(?:bis|ter|quater)?"
    r"|Disposición\s+\w+\s+\w+"
    r")\s*$",
    re.IGNORECASE,
)
# Encabezados numerados de guías e informes ("2.2. Perfil del docente").
# Se exige que no terminen en dos puntos ni coma, para no confundirlos con
# los apartados numerados del articulado ("2. El apartado 1 no se aplicará:").
NUM_RE = re.compile(
    r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2}\.?\s+[A-ZÁÉÍÓÚÑ][^.:;]{4,70})\s*$"
)


@dataclass
class Chunk:
    doc_slug: str
    doc_title: str
    source_id: str
    section: str
    text: str
    start_char: int
    end_char: int

    def contextual(self) -> str:
        """Texto que se embebe: el pasaje precedido de su procedencia.

        Da al vector una pista de contexto que el pasaje suelto no tiene
        (de qué norma es, qué artículo), y mejora bastante el recall.
        """
        cabecera = self.doc_title
        if self.section:
            cabecera += f" · {self.section}"
        return f"{cabecera}\n{self.text}"


def parse_doc(path: Path) -> tuple[dict, str]:
    """Separa el frontmatter YAML simple del cuerpo."""
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = raw
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            for line in raw[4:end].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            body = raw[end + 5 :]
    return meta, body


def find_sections(body: str) -> list[tuple[int, str]]:
    """Localiza los encabezados del texto: [(offset, nombre), ...].

    En los textos legales la rúbrica va en la línea siguiente al ordinal
    ("Artículo 22" / "Decisiones individuales automatizadas…"). Unirlas hace
    la etiqueta mucho más informativa, tanto para citar como para recuperar.
    """
    lineas = body.splitlines(keepends=True)
    # Si el documento es articulado, sus apartados numerados no son secciones.
    articulado = sum(bool(LEGAL_RE.match(l.strip())) for l in lineas) >= 10
    patrones = [LEGAL_RE] if articulado else [LEGAL_RE, NUM_RE]

    offsets, pos = [], 0
    for line in lineas:
        offsets.append(pos)
        pos += len(line)

    out: list[tuple[int, str]] = []
    for i, line in enumerate(lineas):
        m = next((p.match(line.strip()) for p in patrones if p.match(line.strip())), None)
        if not m:
            continue
        nombre = " ".join(m.group(1).split())
        if re.match(r"^(Artículo|CAPÍTULO|TÍTULO|SECCIÓN|ANEXO)\b", nombre, re.I):
            siguiente = lineas[i + 1].strip() if i + 1 < len(lineas) else ""
            es_rubrica = (
                0 < len(siguiente) <= 130
                and not LEGAL_RE.match(siguiente)
                and not siguiente[0].isdigit()
                and not siguiente.endswith(".")
            )
            if es_rubrica:
                nombre = f"{nombre} — {siguiente}"
        out.append((offsets[i], nombre))
    return out


def _label(sections: list[tuple[int, str]], start: int, end: int) -> str:
    """Etiqueta un tramo con TODAS las secciones que abarca, no sólo la primera.

    Un pasaje que cruza del artículo 21 al 22 debe decirlo: etiquetarlo sólo
    como "Artículo 21" produce citas incorrectas.
    """
    dentro = [n for off, n in sections if start <= off < end]
    previa = ""
    for off, n in sections:
        if off > start:
            break
        previa = n
    nombres = ([previa] if previa else []) + [n for n in dentro if n != previa]
    if not nombres:
        return ""
    if len(nombres) == 1:
        return nombres[0]
    return f"{nombres[0]}–{nombres[-1]}" if len(nombres) > 2 else " / ".join(nombres)


def _ventana(words, sections, meta, slug, body, desde=0, hasta=None) -> list[Chunk]:
    """Trocea un tramo con ventana deslizante y solape."""
    hasta = len(words) if hasta is None else hasta
    step = CHUNK_WORDS - OVERLAP_WORDS
    chunks: list[Chunk] = []
    for i in range(desde, hasta, step):
        win = words[i : min(i + CHUNK_WORDS, hasta)]
        if not win or (len(win) < 40 and chunks):
            break
        start, end = win[0][1], win[-1][2]
        chunks.append(
            Chunk(
                doc_slug=slug,
                doc_title=meta.get("title", slug),
                source_id=meta.get("source_id", ""),
                section=_label(sections, start, end),
                text=body[start:end].strip(),
                start_char=start,
                end_char=end,
            )
        )
    return chunks


def chunk_document(path: Path) -> list[Chunk]:
    """Trocea un documento, respetando su articulado si lo tiene."""
    meta, body = parse_doc(path)
    slug = path.stem
    words = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", body)]
    if not words:
        return []
    sections = find_sections(body)

    # Documentos sin articulado (guías, informes): ventana deslizante y listo.
    if len(sections) < STRUCTURED_MIN_SECTIONS:
        return _ventana(words, sections, meta, slug, body)

    # Documentos articulados: cada artículo es la unidad natural de cita.
    # Los muy cortos se fusionan; los muy largos se subdividen con ventana.
    limites = [off for off, _ in sections]
    if limites[0] > 0:
        limites.insert(0, 0)
    limites.append(len(body))

    chunks: list[Chunk] = []
    i = 0
    while i < len(limites) - 1:
        ini = limites[i]
        j = i + 1
        # fusionar secciones demasiado cortas para sostenerse solas
        while j < len(limites) - 1:
            trozo = body[ini : limites[j]]
            if len(trozo.split()) >= MIN_SECTION_WORDS:
                break
            j += 1
        fin = limites[j]
        idx = [n for n, w in enumerate(words) if ini <= w[1] < fin]
        if idx:
            if len(idx) <= CHUNK_WORDS:
                start, end = words[idx[0]][1], words[idx[-1]][2]
                chunks.append(
                    Chunk(
                        doc_slug=slug,
                        doc_title=meta.get("title", slug),
                        source_id=meta.get("source_id", ""),
                        section=_label(sections, start, end),
                        text=body[start:end].strip(),
                        start_char=start,
                        end_char=end,
                    )
                )
            else:
                chunks += _ventana(
                    words, sections, meta, slug, body, idx[0], idx[-1] + 1
                )
        i = j
    return chunks


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def create_schema(db: sqlite3.Connection, reset: bool = False) -> None:
    """Crea el esquema si falta. Con reset=True lo reconstruye desde cero."""
    if reset:
        db.executescript(
            "DROP TABLE IF EXISTS chunks;"
            "DROP TABLE IF EXISTS chunks_fts;"
            "DROP TABLE IF EXISTS chunks_vec;"
            "DROP TABLE IF EXISTS documents;"
        )
    db.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS chunks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_slug   TEXT NOT NULL,
            doc_title  TEXT NOT NULL,
            source_id  TEXT,
            section    TEXT,
            text       TEXT NOT NULL,
            start_char INTEGER,
            end_char   INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_slug);

        -- Huella de cada documento ya indexado, para saltarse los que no
        -- han cambiado: recalcular embeddings es lo caro de todo el proceso.
        CREATE TABLE IF NOT EXISTS documents (
            slug      TEXT PRIMARY KEY,
            title     TEXT,
            hash      TEXT NOT NULL,
            n_chunks  INTEGER,
            indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            content='chunks',
            content_rowid='id',
            tokenize="unicode61 remove_diacritics 2"
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding float[{EMBED_DIM}]
        );
        """
    )


def doc_hash(path: Path) -> str:
    """Huella del contenido, para detectar cambios reales (no de fecha)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def drop_document(db: sqlite3.Connection, slug: str) -> int:
    """Elimina un documento del índice con todo lo que cuelga de él."""
    ids = [r[0] for r in db.execute(
        "SELECT id FROM chunks WHERE doc_slug = ?", (slug,))]
    if ids:
        marcas = ",".join("?" * len(ids))
        db.execute(f"DELETE FROM chunks_vec WHERE chunk_id IN ({marcas})", ids)
        db.execute("DELETE FROM chunks WHERE doc_slug = ?", (slug,))
    db.execute("DELETE FROM documents WHERE slug = ?", (slug,))
    return len(ids)


@functools.lru_cache(maxsize=1)
def embedder():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(MODEL_CACHE))


@functools.lru_cache(maxsize=1)
def reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=RERANK_MODEL, cache_dir=str(MODEL_CACHE))


def embed_passages(texts: list[str]):
    # multilingual-e5 exige los prefijos "passage: " / "query: ".
    return embedder().embed([f"passage: {t}" for t in texts])


def embed_query(text: str):
    return next(iter(embedder().query_embed([f"query: {text}"])))


def _fts_query(text: str) -> str:
    """Convierte lenguaje natural en una consulta FTS5 segura."""
    terms = re.findall(r"\w{3,}", text, flags=re.UNICODE)
    return " OR ".join(f'"{t}"' for t in terms[:32])


def search(
    query: str,
    k: int = 8,
    candidates: int = RERANK_CANDIDATES,
    doc: str | None = None,
    rerank: bool = True,
    db: sqlite3.Connection | None = None,
) -> list[dict]:
    """Búsqueda híbrida: vectorial + BM25, fusión RRF y reranking opcional."""
    own = db is None
    db = db or connect()
    try:
        filt = " AND c.doc_slug LIKE ?" if doc else ""
        arg = [f"%{doc}%"] if doc else []

        pool = max(candidates * 2, 40)
        vec = sqlite_vec.serialize_float32(embed_query(query))
        vec_hits = db.execute(
            f"""SELECT c.id, v.distance FROM chunks_vec v
                JOIN chunks c ON c.id = v.chunk_id
                WHERE v.embedding MATCH ? AND k = ?{filt}
                ORDER BY v.distance""",
            [vec, pool, *arg],
        ).fetchall()

        fts = _fts_query(query)
        bm_hits = (
            db.execute(
                f"""SELECT c.id, bm25(chunks_fts) AS score FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.rowid
                    WHERE chunks_fts MATCH ?{filt}
                    ORDER BY score LIMIT ?""",
                [fts, *arg, pool],
            ).fetchall()
            if fts
            else []
        )

        # Reciprocal Rank Fusion: robusto porque sólo usa el rango, no la escala.
        RRF_K = 60
        scores: dict[int, float] = {}
        for rank, row in enumerate(vec_hits):
            scores[row["id"]] = scores.get(row["id"], 0) + 1 / (RRF_K + rank + 1)
        for rank, row in enumerate(bm_hits):
            scores[row["id"]] = scores.get(row["id"], 0) + 1 / (RRF_K + rank + 1)
        if not scores:
            return []

        ranked = sorted(scores, key=scores.get, reverse=True)[:candidates]
        rows = {
            r["id"]: dict(r)
            for r in db.execute(
                f"SELECT * FROM chunks WHERE id IN ({','.join('?' * len(ranked))})",
                ranked,
            )
        }
        results = [{**rows[i], "rrf": scores[i]} for i in ranked if i in rows]

        if rerank and results:
            recortados = [r["text"][:RERANK_MAX_CHARS] for r in results]
            rr = list(reranker().rerank(query, recortados))
            for r, s in zip(results, rr):
                r["score"] = float(s)
            results.sort(key=lambda r: r["score"], reverse=True)
        else:
            for r in results:
                r["score"] = r["rrf"]

        return results[:k]
    finally:
        if own:
            db.close()


def format_context(results: list[dict]) -> str:
    """Bloque de contexto listo para el prompt, con IDs citables."""
    parts = []
    for r in results:
        loc = f" · {r['section']}" if r["section"] else ""
        parts.append(
            f"[{r['doc_slug']}#{r['id']}] {r['doc_title']}{loc}\n{r['text']}"
        )
    return "\n\n---\n\n".join(parts)


def stats(db: sqlite3.Connection | None = None) -> dict:
    own = db is None
    db = db or connect()
    try:
        docs = db.execute(
            "SELECT doc_slug, doc_title, COUNT(*) n, SUM(LENGTH(text)) chars"
            " FROM chunks GROUP BY doc_slug ORDER BY n DESC"
        ).fetchall()
        return {
            "chunks": db.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"],
            "docs": [dict(d) for d in docs],
        }
    finally:
        if own:
            db.close()


class Corpus:
    """Un conjunto de documentos con su propio índice."""

    def __init__(self, base: Path):
        self.base = Path(base).expanduser().resolve()
        self.docs = self.base / "docs"
        self.db_path = self.base / "data" / "index.db"
        self.name = self.base.name

    # -- metadatos -------------------------------------------------------
    @property
    def meta_path(self) -> Path:
        return self.base / "corpus.json"

    @property
    def meta(self) -> dict:
        if self.meta_path.exists():
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        return {}

    def save_meta(self, **campos) -> None:
        datos = {**self.meta, **campos}
        self.base.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.name

    # -- operaciones -----------------------------------------------------
    def ensure(self) -> "Corpus":
        self.docs.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return self

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def indexed(self) -> bool:
        return self.db_path.exists()

    def search(self, query: str, **kw) -> list[dict]:
        db = self.connect()
        try:
            return search(query, db=db, **kw)
        finally:
            db.close()

    def stats(self) -> dict:
        if not self.indexed():
            return {"chunks": 0, "docs": []}
        db = self.connect()
        try:
            return stats(db)
        finally:
            db.close()

    def __repr__(self) -> str:
        return f"<Corpus {self.name}>"


def default_corpus() -> Corpus:
    """El corpus que se usa cuando no se especifica ninguno.

    Con RAG_HOME, ese. Si el propio proyecto tiene documentos (montaje de un
    solo corpus), ese. Si hay uno solo bajo corpus/, ese. Con varios hay que
    elegir: adivinar sería peor que preguntar.
    """
    if os.environ.get("RAG_HOME"):
        return Corpus(BASE)
    if (ROOT / "docs").is_dir():
        return Corpus(ROOT)
    todos = list_corpus()
    if len(todos) == 1:
        return todos[0]
    if not todos:
        raise ValueError(
            f"No hay ningún corpus. Crea uno en {CORPUS_DIR}/<nombre>/docs/"
        )
    raise ValueError(
        "Hay varios corpus; indica cuál con --corpus: "
        + ", ".join(c.name for c in todos)
    )


def list_corpus() -> list[Corpus]:
    """Todos los corpus disponibles: los de corpus/ y el del propio proyecto."""
    encontrados = []
    if (ROOT / "docs").is_dir():
        encontrados.append(Corpus(ROOT))
    if CORPUS_DIR.is_dir():
        encontrados += [
            Corpus(d) for d in sorted(CORPUS_DIR.iterdir())
            if d.is_dir() and (d / "docs").is_dir()
        ]
    return encontrados


def resolve_corpus(nombre: str | None) -> Corpus:
    """Encuentra un corpus por nombre, por ruta, o el que haya por defecto."""
    if not nombre:
        return default_corpus()
    ruta = Path(nombre).expanduser()
    if ruta.is_dir() and (ruta / "docs").is_dir():
        return Corpus(ruta)
    candidato = CORPUS_DIR / nombre
    if candidato.is_dir():
        return Corpus(candidato)
    disponibles = [c.name for c in list_corpus()]
    parciales = [n for n in disponibles if nombre.lower() in n.lower()]
    if len(parciales) == 1:
        return resolve_corpus(parciales[0])
    raise ValueError(
        f"No encuentro el corpus '{nombre}'. Disponibles: {disponibles or 'ninguno'}"
    )
