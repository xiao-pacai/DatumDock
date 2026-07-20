"""项目 Markdown 的链接、摘要与当前状态一致性回归。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted((ROOT / "assets").glob("*/README.md")),
)
LINK_PATTERN = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")


def _github_anchor(title: str) -> str:
    """生成当前文档使用范围内的 GitHub 标题锚点。"""

    normalized = unicodedata.normalize("NFKC", title).strip().lower()
    normalized = re.sub(r"[^\w\-\u4e00-\u9fff ]", "", normalized)
    return re.sub(r"[\s-]+", "-", normalized).strip("-")


def test_markdown_files_end_with_english_summary() -> None:
    """所有主文档以中文为主，并把英文摘要保留为最后一个二级章节。"""

    for path in MARKDOWN_FILES:
        text = path.read_text(encoding="utf-8").rstrip()
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        assert headings, path
        assert headings[-1] == "## English Summary", path
        assert text.split("## English Summary", 1)[1].strip(), path


def test_internal_markdown_links_and_anchors_resolve() -> None:
    """仓库内部文档链接必须指向现有文件和现有标题。"""

    for source in MARKDOWN_FILES:
        text = source.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = raw_target.strip().strip("<>")
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, fragment = target.partition("#")
            destination = (
                source if not file_part else (source.parent / unquote(file_part)).resolve()
            )
            assert destination.exists(), f"{source}: {target}"
            if fragment and destination.suffix.casefold() == ".md":
                headings = {
                    _github_anchor(line.lstrip("# "))
                    for line in destination.read_text(encoding="utf-8").splitlines()
                    if line.startswith("#")
                }
                assert unquote(fragment).casefold() in headings, f"{source}: {target}"


def test_current_capability_documents_share_stabilization_facts() -> None:
    """当前状态页不得继续把已关闭缺陷或未来功能写成相反结论。"""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    audit = (ROOT / "docs" / "STABILIZATION_AUDIT.md").read_text(encoding="utf-8")
    visual = (ROOT / "docs" / "VISUAL_DESIGN.md").read_text(encoding="utf-8")
    build = (ROOT / "docs" / "BUILD_WINDOWS.md").read_text(encoding="utf-8")

    assert "248 项" in readme and "248 项" in audit
    assert "42 个 DatumDock 自有 SVG" in visual
    assert "安装包" in build and "未完成" in build
    assert "AI、模型、YOLO 与备份仍明确提示后续接入" in readme
    assert "236 passed、1 skipped、14 warnings" not in readme
