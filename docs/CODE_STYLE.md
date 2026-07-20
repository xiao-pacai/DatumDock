# DatumDock 代码规范

本规范以“你可以快速读懂、工具可以稳定检查、数据操作不会藏在花哨写法里”为目标。所有新增 Python 代码必须遵守本文件和 `pyproject.toml` 的 Ruff 配置。

## 1. 自动格式化与检查

- 使用 Ruff 统一执行 import 排序、静态检查与代码格式化；不得手工对抗格式化结果。
- 行宽为 100 个字符，使用 4 个空格缩进、双引号、LF 换行；Ruff 的输出是唯一格式基准。
- 提交前依次运行：

```powershell
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -m compileall -q src
python -m pytest --cov=datumdock --cov-branch
```

- 安装开发依赖后运行 `pre-commit install`；之后每次提交自动执行 Ruff 检查与格式化。
- 禁止无说明地使用 `# noqa`、`# type: ignore` 或跳过测试。确有必要时，在同一行或紧邻上方用中文说明原因、影响范围和预期移除条件。
- DatumDock 自身产生的弃用警告必须修复，不能长期依靠过滤器隐藏；外部依赖警告需记录来源、影响和恢复条件。
- 受管文件事务、标注保存和 X-AnyLabeling 互操作核心模块以至少 90% 分支覆盖为目标；其他当前生产模块以至少 85% 为目标。覆盖率工具不可用时必须记录外部阻塞，不得伪造百分比。
- 可复现实机缺陷必须先建立失败回归。涉及鼠标/键盘顺序时使用真实 Qt 事件序列，不能只测试直接调用私有方法。

## 2. 注释、Docstring 与日志

- 所有代码注释、docstring、TODO/FIXME 和开发日志默认使用中文，方便项目所有者逐段审核；Python 标识符、协议字段、库名、文件格式名和业界固定术语保持英文。
- 注释解释“为什么、约束或风险”，不要逐字复述代码正在做什么。能用清晰函数名、类型和小函数表达的内容，不再额外加注释。
- 公共类、公共函数、复杂数据迁移、文件写入、删除、导入、导出、并发任务和非直观算法必须提供中文 docstring 或紧邻中文注释，说明输入、输出、副作用、失败处理与数据安全边界。
- TODO 使用格式 `# TODO(负责人或模块): 中文待办；完成条件。`；临时绕过使用 `# 临时方案:` 开头并说明替换条件。
- 用户可见文字不直接写死在 Python 控件中，必须使用 i18n 资源；中文日志不等同于中文界面文案。

示例：

```python
def delete_managed_sample(sample_id: str) -> None:
    """删除受管样本及其关联文件；外部导入源始终不在删除范围内。"""

    # 先校验全部关联路径，避免删除到一半才发现标注文件不可访问。
    related_paths = collect_managed_sample_paths(sample_id)
    validate_managed_paths(related_paths)
    remove_paths_atomically(related_paths)
```

## 3. Python 编码约定

- Python 版本基线为 3.11；新增函数、方法和复杂属性必须声明类型。
- 领域模型优先使用不可变或显式的数据模型；UI 回调只协调状态和服务，不直接承载数据集、文件或导出业务规则。
- 模块、函数和变量使用清晰的英文 `snake_case`；类使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`。
- 优先早返回、短函数、显式异常和可测试的纯函数。禁止宽泛捕获 `Exception` 后静默忽略，也禁止用布尔值掩盖失败原因。
- 文件系统、多文件写入和索引更新必须按架构文档使用预检、临时文件、原子替换或可恢复状态；不要在 UI 事件中直接进行耗时 I/O。

## 4. 审核清单

- [ ] Ruff 检查和格式检查通过。
- [ ] 完整测试无 DatumDock 自身警告；覆盖率达到当前阶段门槛，或已记录可信依赖阻塞和恢复命令。
- [ ] 新增复杂逻辑具有中文注释或 docstring，且没有无意义注释。
- [ ] 用户可见文案已进入中英文翻译资源。
- [ ] 修改受管数据、LabelMe/X-AnyLabeling、划分或导出时已添加对应测试。
- [ ] 没有提交模型、数据集、缓存、密钥或个人路径。

## English Summary

DatumDock uses Ruff as the formatting and lint baseline. Full verification also includes compileall, warning-free tests for project-owned code, branch coverage, and real Qt event-order regressions for reproduced GUI defects. Core managed-file, annotation-save, and interoperability modules target 90% branch coverage; other current production modules target 85%. External tool blockers must be recorded rather than bypassed or fabricated.
