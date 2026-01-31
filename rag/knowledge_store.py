"""
Knowledge Store
===============

Lightweight vector database for storing and retrieving trading learnings.
Uses ChromaDB for local/VPS deployment (no external dependencies).

Features:
- Store learnings from trade outcomes
- Semantic search for relevant patterns
- Persist to disk for VPS deployment
- Low memory footprint for Oracle Cloud Always Free tier
"""

import logging
import json
import time
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ChromaDB is optional - graceful fallback to simple JSON storage
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.warning("ChromaDB not installed - using JSON fallback storage")


class KnowledgeStore:
    """
    Persistent knowledge storage for trading bot learnings.

    Supports two backends:
    1. ChromaDB (preferred) - Vector search for semantic retrieval
    2. JSON fallback - Simple keyword matching when ChromaDB unavailable
    """

    def __init__(
        self,
        persist_directory: str = "data/rag",
        collection_name: str = "trading_learnings",
    ):
        """
        Initialize knowledge store.

        Args:
            persist_directory: Where to store the database
            collection_name: Name of the ChromaDB collection
        """
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name

        self._client = None
        self._collection = None
        self._json_store: List[Dict] = []
        self._json_path = self.persist_directory / "learnings.json"

        self._initialized = False

    async def initialize(self):
        """Initialize the knowledge store backend"""
        if self._initialized:
            return

        if CHROMADB_AVAILABLE:
            try:
                # Use persistent storage for VPS deployment
                self._client = chromadb.PersistentClient(
                    path=str(self.persist_directory),
                    settings=Settings(
                        anonymized_telemetry=False,
                        allow_reset=True,
                    )
                )
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"description": "Trading bot learnings and patterns"}
                )
                logger.info(f"ChromaDB initialized at {self.persist_directory}")
                self._initialized = True
                return
            except Exception as e:
                logger.error(f"ChromaDB initialization failed: {e}, falling back to JSON")

        # JSON fallback
        await self._load_json_store()
        self._initialized = True
        logger.info(f"JSON knowledge store initialized at {self._json_path}")

    async def _load_json_store(self):
        """Load JSON store from disk"""
        if self._json_path.exists():
            try:
                with open(self._json_path, "r") as f:
                    self._json_store = json.load(f)
                logger.info(f"Loaded {len(self._json_store)} learnings from JSON")
            except Exception as e:
                logger.error(f"Failed to load JSON store: {e}")
                self._json_store = []
        else:
            self._json_store = []

    async def _save_json_store(self):
        """Save JSON store to disk"""
        try:
            with open(self._json_path, "w") as f:
                json.dump(self._json_store, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save JSON store: {e}")

    async def add_learning(
        self,
        learning_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> str:
        """
        Add a learning to the knowledge store.

        Args:
            learning_type: Type of learning (error_pattern, successful_pattern, decision)
            content: The learning content
            metadata: Additional metadata (strategy, market, outcome, etc.)
            tags: Searchable tags

        Returns:
            ID of the stored learning
        """
        if not self._initialized:
            await self.initialize()

        learning_id = f"{learning_type}_{int(time.time() * 1000)}"
        timestamp = datetime.now(timezone.utc).isoformat()

        # Serialize tags to string for ChromaDB compatibility
        tags_str = ",".join(tags) if tags else ""

        full_metadata = {
            "learning_type": learning_type,
            "timestamp": timestamp,
            "tags": tags_str,  # ChromaDB requires str, not list
            **(metadata or {}),
        }

        # Ensure all metadata values are ChromaDB-compatible (str, int, float, bool)
        for key, value in list(full_metadata.items()):
            if isinstance(value, list):
                full_metadata[key] = ",".join(str(v) for v in value)
            elif isinstance(value, dict):
                full_metadata[key] = json.dumps(value)
            elif value is None:
                full_metadata[key] = ""

        if CHROMADB_AVAILABLE and self._collection is not None:
            try:
                self._collection.add(
                    documents=[content],
                    metadatas=[full_metadata],
                    ids=[learning_id],
                )
                logger.debug(f"Added learning to ChromaDB: {learning_id}")
                return learning_id
            except Exception as e:
                logger.error(f"ChromaDB add failed: {e}, using JSON fallback")

        # JSON fallback
        self._json_store.append({
            "id": learning_id,
            "content": content,
            "metadata": full_metadata,
        })
        await self._save_json_store()
        logger.debug(f"Added learning to JSON store: {learning_id}")
        return learning_id

    async def search_learnings(
        self,
        query: str,
        learning_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant learnings.

        Args:
            query: Search query (semantic with ChromaDB, keyword with JSON)
            learning_type: Filter by learning type
            tags: Filter by tags
            n_results: Maximum number of results

        Returns:
            List of relevant learnings
        """
        if not self._initialized:
            await self.initialize()

        if CHROMADB_AVAILABLE and self._collection is not None:
            try:
                # Build where filter
                where_filter = {}
                if learning_type:
                    where_filter["learning_type"] = learning_type
                if tags:
                    where_filter["tags"] = {"$contains": tags[0]}  # ChromaDB limitation

                results = self._collection.query(
                    query_texts=[query],
                    n_results=n_results,
                    where=where_filter if where_filter else None,
                )

                learnings = []
                if results["documents"] and results["documents"][0]:
                    for i, doc in enumerate(results["documents"][0]):
                        learnings.append({
                            "id": results["ids"][0][i] if results["ids"] else None,
                            "content": doc,
                            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                            "distance": results["distances"][0][i] if results.get("distances") else None,
                        })
                return learnings

            except Exception as e:
                logger.error(f"ChromaDB search failed: {e}, using JSON fallback")

        # JSON fallback - simple keyword search
        query_lower = query.lower()
        matches = []

        for item in self._json_store:
            content_lower = item["content"].lower()
            metadata = item.get("metadata", {})

            # Score based on keyword matches
            score = 0
            if query_lower in content_lower:
                score += 10

            # Check tags
            item_tags = metadata.get("tags", [])
            if tags:
                for tag in tags:
                    if tag.lower() in [t.lower() for t in item_tags]:
                        score += 5

            # Filter by type
            if learning_type and metadata.get("learning_type") != learning_type:
                continue

            if score > 0:
                matches.append({
                    "id": item["id"],
                    "content": item["content"],
                    "metadata": metadata,
                    "score": score,
                })

        # Sort by score and return top N
        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches[:n_results]

    async def get_learning(self, learning_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific learning by ID"""
        if not self._initialized:
            await self.initialize()

        if CHROMADB_AVAILABLE and self._collection is not None:
            try:
                result = self._collection.get(ids=[learning_id])
                if result["documents"]:
                    return {
                        "id": learning_id,
                        "content": result["documents"][0],
                        "metadata": result["metadatas"][0] if result["metadatas"] else {},
                    }
            except Exception as e:
                logger.error(f"ChromaDB get failed: {e}")

        # JSON fallback
        for item in self._json_store:
            if item["id"] == learning_id:
                return item

        return None

    async def get_stats(self) -> Dict[str, Any]:
        """Get knowledge store statistics"""
        if not self._initialized:
            await self.initialize()

        if CHROMADB_AVAILABLE and self._collection is not None:
            try:
                count = self._collection.count()
                return {
                    "backend": "chromadb",
                    "total_learnings": count,
                    "persist_directory": str(self.persist_directory),
                }
            except Exception as e:
                logger.error(f"ChromaDB stats failed: {e}")

        return {
            "backend": "json",
            "total_learnings": len(self._json_store),
            "persist_path": str(self._json_path),
        }

    async def close(self):
        """Close the knowledge store"""
        if CHROMADB_AVAILABLE and self._client is not None:
            # ChromaDB persistent client handles cleanup
            pass
        self._initialized = False
