# Code samples

Two representative files from the private Advise Assist codebase, included so the
architecture described in the [main README](../README.md) isn't just a diagram.

| File | What it shows |
|---|---|
| `scrubber.py` | Two-layer PII removal, and the disambiguation problem underneath it: telling a deadline from a grade from a GPA in free-form student email. |
| `rag.py` | Policy retrieval: corpus loading with citation metadata, a persistent vector store, and a grounded prompt that refuses rather than invents. |

These are excerpts, not a runnable project. `scrubber.py` needs `spacy` and the
`en_core_web_sm` model; `rag.py` needs `langchain`, `qdrant`, a local Ollama
embedding model, and a `GROQ_API_KEY`, plus the policy corpus that is not published here.
