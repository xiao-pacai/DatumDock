# DatumDock 图标资产

本目录存放 DatumDock 自有的图标源文件和发布派生文件。

## 规则

- 优先保留可编辑的 SVG 源文件；Windows 应用图标同时生成所需的 ICO 与 PNG 尺寸。
- 文件名使用稳定的英文语义名，例如 `import.svg`、`auto_annotate.svg`、`delete.svg`，业务代码只引用语义名，不绑定某个具体文件版本。
- 图标视觉风格遵循 `docs/VISUAL_DESIGN.md`：圆角、简洁线性、品牌蓝与语义状态着色，不复制任何第三方产品的品牌或图标资产。
- 用户提出某个图标需要修改时，更新对应 SVG 源文件，再重新生成派生尺寸并完成亮色、悬停、禁用与高 DPI 检查。
- 当前 42 个 SVG 已通过 `IconRegistry` 绑定到首页导航、工作台主操作、数据集卡片和标签/图片治理页面；注册表提供资源枚举与缺失诊断，禁止缺图时静默退化为字符占位。
- `delete_annotation.svg`、`delete_image.svg` 和 `delete_dataset.svg` 分别表达删除当前矩形、受管图片和整数据集，危险操作不得共用一个含义模糊的删除图标。

## English Summary

This directory stores 42 DatumDock-owned SVG icons and release derivatives. `IconRegistry` now binds and validates the source-GUI icon set, including distinct annotation, image, and whole-dataset deletion semantics. Desktop and Start-menu integration remains part of the future installer stage.
