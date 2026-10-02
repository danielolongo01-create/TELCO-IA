from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import chromadb

# Modèle d'embeddings :
#   - par défaut : modèle ONNX intégré à Chroma (all-MiniLM-L6-v2, léger, surtout anglais)
#   - NOC_EMBED_MODEL=paraphrase-multilingual-MiniLM-L12-v2 : meilleur en français,
#     nécessite `pip install sentence-transformers` (lourd : PyTorch)
EMBED_MODEL = os.getenv("NOC_EMBED_MODEL", "").strip()


def _make_embedding_function():
    if not EMBED_MODEL:
        return None, "default"
    from chromadb.utils import embedding_functions

    fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    return fn, hashlib.md5(EMBED_MODEL.encode()).hexdigest()[:6]


class VectorMemoryStore:
    """ChromaDB local : mémoire épisodique (incidents) + base de connaissances télécom."""

    def __init__(self, persist_dir: str = "data/chroma_db") -> None:
        project_root = Path(__file__).resolve().parent.parent
        self.root = project_root
        abs_path = project_root / persist_dir
        abs_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(path=str(abs_path))
        embed_fn, tag = _make_embedding_function()
        # Le suffixe évite le conflit si on change de modèle d'embeddings plus tard.
        suffix = "" if tag == "default" else f"_{tag}"
        kwargs = {"embedding_function": embed_fn} if embed_fn else {}
        self.collection = self.client.get_or_create_collection(
            name=f"telecom_episodic_memory{suffix}", **kwargs
        )
        self.knowledge = self.client.get_or_create_collection(
            name=f"telecom_knowledge{suffix}", **kwargs
        )

    # ---------- Mémoire épisodique ----------
    def index_episode(
        self,
        episode_id: int | str,
        cell_id: str,
        anomaly_type: str,
        summary_text: str,
    ) -> None:
        self.collection.upsert(
            documents=[summary_text],
            metadatas=[
                {
                    "cell_id": cell_id,
                    "anomaly_type": anomaly_type,
                    "episode_id": str(episode_id),
                }
            ],
            ids=[f"ep_{episode_id}"],
        )

    def search_similar_episodes(
        self, query: str, top_k: int = 3, cell_id: str | None = None
    ) -> list[dict[str, Any]]:
        total = self.collection.count()
        if total == 0:
            return []

        where = {"cell_id": cell_id} if cell_id else None
        try:
            results = self.collection.query(
                query_texts=[query], n_results=min(top_k, total), where=where
            )
        except Exception:
            # where sans résultat sur certaines versions : on retente sans filtre
            results = self.collection.query(query_texts=[query], n_results=min(top_k, total))
        return self._format(results)

    # ---------- Base de connaissances ----------
    def ingest_knowledge(self, directory: str = "rag/knowledge") -> int:
        """Indexe les fichiers .md (un chunk par section '## ').

        Un chunk n'est ré-embeddé que si son contenu a changé (hash).
        """
        folder = self.root / directory
        if not folder.exists():
            return 0

        ids, docs, metas = [], [], []
        for path in sorted(folder.glob("*.md")):
            chunks = _split_markdown(path.read_text(encoding="utf-8"))
            for i, (title, body) in enumerate(chunks):
                text = f"{title}\n{body}".strip()
                ids.append(f"{path.stem}:{i}")
                docs.append(text)
                metas.append(
                    {
                        "source": path.name,
                        "title": title,
                        "hash": hashlib.md5(text.encode()).hexdigest(),
                    }
                )
        if not ids:
            return 0

        existing = self.knowledge.get(ids=ids, include=["metadatas"])
        known = {
            i: (m or {}).get("hash")
            for i, m in zip(existing.get("ids", []), existing.get("metadatas", []))
        }
        todo = [k for k, i in enumerate(ids) if known.get(i) != metas[k]["hash"]]
        if todo:
            self.knowledge.upsert(
                ids=[ids[k] for k in todo],
                documents=[docs[k] for k in todo],
                metadatas=[metas[k] for k in todo],
            )
        return len(todo)

    def search_knowledge(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        total = self.knowledge.count()
        if total == 0:
            return []
        results = self.knowledge.query(query_texts=[query], n_results=min(top_k, total))
        return self._format(results)

    @staticmethod
    def _format(results: dict) -> list[dict[str, Any]]:
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        return [
            {
                "summary": documents[i],
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else None,
            }
            for i in range(len(documents))
        ]


def _split_markdown(text: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    title, buf = "Introduction", []
    for line in text.splitlines():
        if line.startswith("## "):
            if "".join(buf).strip():
                chunks.append((title, "\n".join(buf).strip()))
            title, buf = line[3:].strip(), []
        elif not line.startswith("# "):
            buf.append(line)
    if "".join(buf).strip():
        chunks.append((title, "\n".join(buf).strip()))
    return chunks
