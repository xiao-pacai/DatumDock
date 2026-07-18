# DatumDock 步骤一 UI 交付与自检报告

> 结论：DatumDock 全量 UI 原型已准备好供用户视觉审核；真实数据集与模型逻辑将在后续阶段接入。

## 1. 交付范围

- 正式入口已切换到 `ApplicationShell`；普通模式提供无副作用空主页，预览模式使用一次性内存数据。
- 已实现 16 个稳定路由、28 个集中注册弹窗、现代组件库、自有 SVG 图标和中英文资源。
- 标注工作台提供纯内存矩形创建、选择、移动、八点缩放、标签同步、缩放、适配、撤销和重做。
- 设置页提供语言即时切换、快捷键录入/冲突检查/恢复默认、回收站阈值问号帮助和其他配置外观。
- `UiGateway` 隔离新界面与旧业务服务；本轮不会创建数据集、图片、SQLite、模型、导出目录或偏好文件。

## 2. 自动验证

```powershell
python -m ruff check src tests
python -m ruff format --check src tests
py -3.11 -m compileall -q src
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

本轮结果：Ruff 通过、格式检查通过、Python 3.11 编译检查通过、31 项 pytest 通过。普通模式与 `--ui-preview` 均完成进程级启动冒烟；16 个路由和 28 个弹窗完成遍历。

## 3. 视觉验证

本地截图位于忽略提交的 `build/ui-review/`，共 15 张，覆盖：

- 中文 1440×900：安全空主页、预览主页、标注工作台、标签管理、模型管理、设置、组件样例、YOLO 导出、危险删除；
- 英文 1366×768：主页、设置；
- 中文 125% / 1440×900：主页、标注工作台；
- 中文 150% / 1920×1080：主页、标注工作台。

检查并修正了动态卡片残留遮挡、工作台品牌区裁切、矩形控制柄重复位移、中文状态中的英文单位和危险删除范围未翻译等问题。

## 4. 自检评分

| 领域 | 得分 | 说明 |
| --- | ---: | --- |
| UI 页面与流程覆盖 | 25 / 25 | 路由、管理页、状态页和 28 个弹窗均可发现与创建。 |
| 视觉质量与一致性 | 27 / 30 | 现代冷白/浅蓝灰、品牌色、深色画布和自有图标统一；教程正文仍为演示内容。 |
| 导航与演示交互 | 19 / 20 | 页面跳转、对话框、画布、筛选、语言和快捷键均可交互；业务结果仅为内存演示。 |
| 响应式、DPI 与中英文 | 13 / 15 | 三种分辨率与三档 DPI 已检查；更极端窗口尺寸不属于本轮基线。 |
| 测试与工程质量 | 10 / 10 | 静态检查、格式、31 项回归、CLI 启动和安全边界均通过。 |
| **总分** | **94 / 100** | 达到不低于 90 分的步骤一交付要求。 |

硬性安全验收通过：普通模式和预览模式都未调用真实数据集服务，未把任何未接入业务操作伪装为成功。

## 5. 已知边界

- Python 3.11 新虚拟环境因 PyPI TLS `SSLEOFError` 无法安装依赖；已完成 3.11 `compileall`，但依赖与发布环境验证仍需网络恢复。
- 教程阅读器使用内置演示正文，不代表最终教程已经校订。
- 旧业务服务与旧界面代码仍保留，但正式入口不再使用；后续应通过 `UiGateway` 逐项迁移，不应让页面直接访问文件或 SQLite。
- 本轮不是安装包、真实导入、真实标注持久化、模型推理或 YOLO/X-AnyLabeling 导出交付。

## English Summary

The step-one DatumDock UI prototype is ready for visual review and scores 94/100. It includes 16 routes, 28 dialogs, a modern component system, owned SVG icons, bilingual resources, an in-memory annotation canvas, and safe normal/preview gateways. Ruff, formatting, Python 3.11 compile checks, 31 pytest cases, CLI launch smoke tests, and multilingual DPI screenshots pass. Real dataset storage, model inference, import/export, and installer logic remain future phases.
