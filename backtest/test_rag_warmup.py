"""Offline coverage for optional legacy-RAG startup warmup."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import rag_manager


class _Embedding:
    calls = 0

    def __init__(self, model_name):
        self.model_name = model_name

    def __call__(self, documents):
        type(self).calls += 1
        return [[0.0] for _ in documents]


class _SlowEmbedding(_Embedding):
    init_calls = 0
    started = threading.Event()
    release = threading.Event()

    def __init__(self, model_name):
        type(self).init_calls += 1
        type(self).started.set()
        type(self).release.wait(timeout=2)
        super().__init__(model_name)


class _Collection:
    def count(self):
        return 0


class _Client:
    def __init__(self, path):
        self.path = path

    def get_or_create_collection(self, **_kwargs):
        return _Collection()


class RagWarmupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original = {
            name: getattr(rag_manager, name)
            for name in (
                "_chromadb", "_embedding_fn", "_collection", "_initialized",
                "_warmed_up", "_available", "_background_warmup_thread",
            )
        }
        rag_manager._chromadb = None
        rag_manager._embedding_fn = None
        rag_manager._collection = None
        rag_manager._initialized = False
        rag_manager._warmed_up = False
        rag_manager._available = True
        rag_manager._background_warmup_thread = None
        _Embedding.calls = 0
        _SlowEmbedding.init_calls = 0
        _SlowEmbedding.started.clear()
        _SlowEmbedding.release.clear()

    def tearDown(self):
        for name, value in self.original.items():
            setattr(rag_manager, name, value)
        self.temp_dir.cleanup()

    def _fake_chromadb_modules(self, embedding_class=_Embedding):
        chromadb = types.ModuleType("chromadb")
        chromadb.PersistentClient = _Client
        utils = types.ModuleType("chromadb.utils")
        embedding_functions = types.ModuleType("chromadb.utils.embedding_functions")
        embedding_functions.SentenceTransformerEmbeddingFunction = embedding_class
        utils.embedding_functions = embedding_functions
        return {
            "chromadb": chromadb,
            "chromadb.utils": utils,
            "chromadb.utils.embedding_functions": embedding_functions,
        }

    def test_warmup_initializes_and_embeds_only_once(self):
        with patch.dict(os.environ, {"LEGACY_RAG_ENABLED": "true"}), \
             patch.dict(sys.modules, self._fake_chromadb_modules()), \
             patch.object(rag_manager, "VECTOR_DB_DIR", Path(self.temp_dir.name)):
            self.assertTrue(rag_manager.warmup())
            self.assertTrue(rag_manager._initialized)
            self.assertTrue(rag_manager._warmed_up)
            self.assertEqual(_Embedding.calls, 1)
            self.assertTrue(rag_manager.warmup())
            self.assertEqual(_Embedding.calls, 1)

    def test_concurrent_initialization_waits_for_ready_collection(self):
        results = []

        def initialize():
            available = rag_manager._ensure_initialized()
            results.append((available, rag_manager._collection is not None))

        with patch.dict(sys.modules, self._fake_chromadb_modules(_SlowEmbedding)), \
             patch.object(rag_manager, "VECTOR_DB_DIR", Path(self.temp_dir.name)):
            first = threading.Thread(target=initialize)
            first.start()
            self.assertTrue(_SlowEmbedding.started.wait(timeout=1))
            second = threading.Thread(target=initialize)
            second.start()
            time.sleep(0.02)
            self.assertTrue(second.is_alive())
            _SlowEmbedding.release.set()
            first.join(timeout=1)
            second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(results, [(True, True), (True, True)])
        self.assertEqual(_SlowEmbedding.init_calls, 1)

    def test_warmup_respects_disabled_legacy_rag(self):
        with patch.dict(os.environ, {"LEGACY_RAG_ENABLED": "false"}):
            self.assertFalse(rag_manager.warmup())
        self.assertFalse(rag_manager._initialized)

    def test_warmup_swallows_embedding_failure(self):
        with patch.dict(os.environ, {"LEGACY_RAG_ENABLED": "true"}), \
             patch.object(rag_manager, "_ensure_initialized", return_value=True), \
             patch.object(rag_manager, "_embedding_fn", side_effect=RuntimeError("boom")):
            self.assertFalse(rag_manager.warmup())
        self.assertTrue(rag_manager._warmed_up)

    def test_background_warmup_starts_one_daemon_thread_without_joining(self):
        with patch.object(rag_manager.threading, "Thread") as thread_class:
            thread = thread_class.return_value
            self.assertIs(rag_manager.start_background_warmup(), thread)
            self.assertIs(rag_manager.start_background_warmup(), thread)
        thread_class.assert_called_once()
        self.assertTrue(thread_class.call_args.kwargs["daemon"])
        self.assertEqual(thread_class.call_args.kwargs["name"], "ciel-rag-warmup")
        thread.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
