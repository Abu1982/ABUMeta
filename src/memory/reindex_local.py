"""全量本地向量重索引脚本。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _maybe_reexec_gpu_venv() -> None:
    current_python = Path(sys.executable).resolve()
    target_python = REPO_ROOT / ".venv-gpu" / "Scripts" / "python.exe"
    if target_python.exists() and current_python != target_python.resolve():
        os = __import__("os")
        if os.environ.get("ABU_GPU_VENV_ACTIVE") != "1":
            os.environ["ABU_GPU_VENV_ACTIVE"] = "1"
            os.execv(str(target_python), [str(target_python), *sys.argv])


if __name__ == "__main__":
    _maybe_reexec_gpu_venv()

from src.memory import MemoryManager
from src.utils.map_exporter import export_evolution_map


def main() -> None:
    memory_manager = MemoryManager()
    stats = memory_manager.reindex_local_embeddings()
    payload = export_evolution_map(memory_manager, repo_root=str(REPO_ROOT))
    print(
        json.dumps(
            {
                "episodic_reindexed": stats["episodic_reindexed"],
                "wisdom_reindexed": stats["wisdom_reindexed"],
                "wisdom_nodes": len(payload.get("wisdom_nodes", [])),
                "generated_at": payload.get("generated_at"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
