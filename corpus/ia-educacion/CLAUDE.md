# Corpus: uso ético, legal y responsable de la IA en educación

Base documental de 15 fuentes (normativa europea y española, guías INTEF/AEPD/
UNESCO, marcos de competencia digital) indexada para consulta con RAG.

## Cómo responder preguntas sobre este corpus

Usa las herramientas del servidor MCP `ia-educacion`:

- `buscar(consulta, k, documento, corpus)` — búsqueda híbrida sobre un corpus.
- `listar_fuentes(corpus)` — inventario de documentos y sus slugs.
- `leer_pasaje(chunk_id, contexto, corpus)` — amplía un pasaje concreto.
- `leer_documento(slug, desde, longitud, corpus)` — recorre un documento entero.

El parámetro `corpus` es opcional cuando solo hay uno disponible. Si hay
varios, usa primero `listar_corpus()` y pasa el mismo corpus a todas las
herramientas de una consulta.

Reglas:

1. **Nunca respondas de memoria.** Toda afirmación sobre normativa o sobre el
   contenido de las guías debe venir de una búsqueda en el corpus.
2. **Busca varias veces.** Una sola consulta rara vez basta: reformula con el
   vocabulario propio de la norma (p. ej. "responsable del tratamiento", no
   "quien gestiona los datos") y cruza fuentes.
3. **Cita siempre** con el identificador `[slug#id]` que devuelve `buscar`, y
   nombra la norma y el artículo cuando el pasaje los indique.
4. **Distingue las fuentes.** El RGPD, la LOPDGDD, el Reglamento de IA y la Ley
   de Propiedad Intelectual dicen cosas distintas: no las mezcles en una misma
   afirmación sin señalar cuál dice qué.
5. Si el corpus no cubre la pregunta, dilo en lugar de rellenar el hueco.

## Mantenimiento

- `python src/sync.py` — re-descarga las fuentes desde NotebookLM.
- `python src/index.py` — reconstruye el índice (necesario tras sincronizar).
