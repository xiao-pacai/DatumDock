"""工程骨架的最小配置验证。"""

from pathlib import Path


def test_quality_configuration_files_exist() -> None:
    """确保新环境克隆仓库后能找到统一的质量检查配置。"""

    project_root = Path(__file__).resolve().parents[1]

    assert (project_root / "pyproject.toml").is_file()
    assert (project_root / ".pre-commit-config.yaml").is_file()
