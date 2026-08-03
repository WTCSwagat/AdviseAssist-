"""
Policy retrieval for Advise Assist. Excerpt from a private codebase.

Loads the university policy corpus, embeds it into a persistent Qdrant store, and
answers questions strictly from what retrieval returns.

Two decisions worth noting:

1. Only prose policy (`*.md`) is embedded. Dates and lookup tables live in JSON and
   are handled by plain Python, because embeddings cannot do date math or exact
   score lookups. A deadline checker computes the fact and injects it into the
   prompt, so the model never reasons about dates itself.

2. Every corpus file carries a source URL in its front matter, which rides along in
   the document metadata. That is what lets an answer cite the policy page it came
   from, which is the difference between an advisor trusting a draft and rewriting it.

Run: python rag.py
To re-embed after editing the corpus, delete the qdrant/ folder and run again.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

KB_DIR = Path(__file__).resolve().parent.parent / "kb"
QDRANT_PATH = Path(__file__).resolve().parent / "qdrant"
COLLECTION_NAME = "advising-db"


def split_yaml_header(text: str) -> tuple[dict[str, str], str]:
    """Pull topic + source URL out of the --- header at the top of each corpus file."""
    if not text.startswith("---"):
        return {}, text

    _, header_block, body = text.split("---", 2)
    header = {}
    for line in header_block.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            header[key.strip()] = value.strip()
    return header, body.lstrip("\n")


def load_data(kb_dir: Path | str = KB_DIR) -> list[Document]:
    """Read every policy markdown file, one Document per file."""
    documents = []
    kb_path = Path(kb_dir)

    for file_path in sorted(kb_path.glob("*.md")):
        if file_path.name == "README.md":
            continue

        header, body = split_yaml_header(file_path.read_text(encoding="utf-8"))
        documents.append(
            Document(
                page_content=body,
                metadata={
                    "file_id": file_path.stem,
                    "source_file": file_path.name,
                    "topic": header.get("topic", file_path.stem),
                    "source": header.get("source", ""),
                },
            )
        )
    return documents


embeddings = OllamaEmbeddings(model="mxbai-embed-large")

# First run embeds the corpus and persists it. Later runs load the saved index,
# so startup does not pay the embedding cost again.
if QDRANT_PATH.exists():
    print("Loading saved index...")
    store = QdrantVectorStore.from_existing_collection(
        embedding=embeddings,
        path=str(QDRANT_PATH),
        collection_name=COLLECTION_NAME,
    )
else:
    docs = load_data()
    print(f"Embedding {len(docs)} documents...")
    store = QdrantVectorStore.from_documents(
        docs,
        embeddings,
        path=str(QDRANT_PATH),
        collection_name=COLLECTION_NAME,
    )
    print(f"Saved to {QDRANT_PATH}")


def get_context(query: str, store: QdrantVectorStore) -> str:
    """Top-k policy passages for a question, each tagged with its source URL."""
    results = store.similarity_search(query, k=3)

    context = ""
    for doc in results:
        context += f"[Source: {doc.metadata['source']}]\n{doc.page_content}\n\n"
    return context


def get_answer(query: str, store: QdrantVectorStore) -> str:
    """Answer strictly from retrieved policy, or refuse.

    The prompt is deliberately restrictive. A wrong drop deadline in an advisor's
    outgoing email is worse than no answer, so specifics are only permitted when
    copied from the retrieved context, and NO_INFO is the preferred failure mode.
    """
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=500,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a university academic policy assistant. "
                    "Your ONLY source of knowledge is the CONTEXT section provided in "
                    "the user message. Ignore anything you think you know from elsewhere.\n"
                    "Rules:\n"
                    "- Answer directly and concisely, using only information found in the context.\n"
                    "- Specifics (dates, GPA cutoffs, dollar amounts, office names) are allowed "
                    "ONLY if they appear in the context, copied exactly as written there.\n"
                    '- End with the source URL(s) you used, under the heading "Sources:".\n'
                    "- If the context does not answer the question, reply with exactly: "
                    "NO_INFO and nothing else. When in doubt, prefer NO_INFO.\n"
                ),
            },
            {
                "role": "user",
                "content": get_context(query, store) + "\n\nSTUDENT'S QUESTION: " + query,
            },
        ],
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    query = input("Enter your query: ")
    print(f"\n{get_answer(query, store)}")
    store.client.close()
