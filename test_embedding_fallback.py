"""向量检索三级降级链验证：远程 API → 本地 torch → 都没有就跳过向量腿（纯 FTS5+BM25）。

  F1. 远程模式：配了 openai_compatible 就用远程（mock HTTP，不依赖 torch）
  F2. 远程失败 → 回退本地 SentenceTransformer（真实 1024 维）
  F3. 无远程 + 本地不可用 → encode 返回 []、store_embedding 不落库、
      检索纯走 FTS5 不崩溃且语义命中
  F4. available 属性三档取值正确
"""

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from Memory.retrieval.EmbeddingService import EmbeddingService

REMOTE_VEC = [0.01] * 1536


def _mock_remote_ok(texts):
    resp = mock.Mock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: {"data": [{"index": i, "embedding": REMOTE_VEC} for i in range(len(texts))]}
    return resp


def _make_service(provider, tmp):
    cfg = {
        "memory.root_dir": str(Path(tmp) / "rm"),
        "memory.retrieval.embedding_provider": provider,
        "memory.retrieval.embedding_base_url": "https://fake.example.com/v1" if provider == "openai_compatible" else "",
        "memory.retrieval.embedding_api_key_env": "FAKE_EMB_KEY" if provider == "openai_compatible" else "",
    }
    return EmbeddingService(cfg=cfg)


def test_f1_remote_mode():
    tmp = tempfile.mkdtemp()
    try:
        import os
        with mock.patch.dict(os.environ, {"FAKE_EMB_KEY": "sk-fake"}):
            svc = _make_service("openai_compatible", tmp)
            with mock.patch("requests.post", side_effect=lambda *a, **kw: _mock_remote_ok(["x"])) as post:
                vec = svc.encode("远程向量测试")
            assert vec == REMOTE_VEC, f"应返回远程向量，实际维度 {len(vec)}"
            assert post.called, "远程模式下应发起 HTTP 请求"
            assert svc.available is True
        print("[F1] 远程模式：mock HTTP 返回 1536 维向量 OK（无需 torch）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_f2_remote_fails_fallback_local():
    tmp = tempfile.mkdtemp()
    try:
        import os
        with mock.patch.dict(os.environ, {"FAKE_EMB_KEY": "sk-fake"}):
            svc = _make_service("openai_compatible", tmp)
            with mock.patch("requests.post", side_effect=ConnectionError("remote down")):
                vec = svc.encode("回退本地测试")
            assert len(vec) == 1024, f"远程失败应回退本地 1024 维，实际 {len(vec)}"
        print("[F2] 远程失败 → 回退本地 SentenceTransformer（1024 维）OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_f3_no_backend_skip_vector_leg():
    tmp = tempfile.mkdtemp()
    try:
        svc = _make_service("local", tmp)
        svc._local_failed = True  # 模拟本地模型不可用（如 haru 上未装 torch）

        vec = svc.encode("无后端测试")
        assert vec == [], f"无后端应返回 []，实际维度 {len(vec)}"
        assert svc.available is False

        # store_embedding 不落库：embeddings 表必须保持空
        svc.store_embedding("l1", 999, "无后端测试文本")
        rows = svc.db.query_all("SELECT * FROM embeddings WHERE source_id = 999")
        assert len(rows) == 0, f"无后端不应写入假向量，实际 {len(rows)} 条"

        # 检索纯走 FTS5：不崩溃且能命中
        from Memory.bridge import MemoryBridge
        mem = MemoryBridge(overrides={"memory.root_dir": str(Path(tmp) / "rm2")})
        mem.retriever.embedding_service._local_failed = True  # 同样禁用
        mem.append({"role": "user", "content": "部署目标是瑞芯微 RK3588 开发板"})
        mem.ingest_atoms([{"atom_type": "fact", "subject": "hardware",
                           "canonical_text": "部署目标是瑞芯微 RK3588 开发板", "salience": 0.8}])
        pack = mem.retrieve("RK3588 是什么")
        hits = [m.get("text", "") for m in pack["memories"]]
        assert any("RK3588" in t for t in hits), f"纯 FTS5 应命中，实际 {hits}"
        # embeddings 表无新增（向量腿整体跳过）
        n = mem.db.scalar("SELECT COUNT(*) FROM embeddings")
        assert n == 0, f"向量腿禁用后 embeddings 表应为空，实际 {n} 条"
        print("[F3] 无后端：encode=[]、不落库、纯 FTS5 检索命中 OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_f4_available_property():
    tmp = tempfile.mkdtemp()
    try:
        svc_ok = _make_service("local", tmp)
        assert svc_ok.available is True, "本机本地模型可用，available 应为 True"
        svc_bad = _make_service("local", tmp)
        svc_bad._local_failed = True  # 未加载过模型 + 加载失败 → 不可用
        assert svc_bad.available is False, "本地加载失败后 available 应为 False"
        print("[F4] available 属性：本地可用=True / 失败=False / 远程=True(F1已验) OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_f1_remote_mode()
    test_f2_remote_fails_fallback_local()
    test_f3_no_backend_skip_vector_leg()
    test_f4_available_property()
    print("\n=== 向量检索降级链验证：全部通过 ===")
