# DatumDock 图标资产

本目录存放 DatumDock 自有的图标源文件和发布派生文件。

## 规则

- 优先保留可编辑的 SVG 源文件；Windows 应用图标同时生成所需的 ICO 与 PNG 尺寸。
- 文件名使用稳定的英文语义名，例如 `import.svg`、`auto_annotate.svg`、`delete.svg`，业务代码只引用语义名，不绑定某个具体文件版本。
- 图标视觉风格遵循 `docs/UX.md`：圆角、简洁线性、低饱和，不复制任何第三方产品的品牌或图标资产。
- 用户提出某个图标需要修改时，更新对应 SVG 源文件，再重新生成派生尺寸并完成亮色、悬停、禁用与高 DPI 检查。

## English Summary

This directory stores DatumDock-owned icon source files and release derivatives. Keep editable SVG sources, use stable semantic English filenames, and regenerate ICO/PNG outputs after a source icon changes. Icons must follow the fresh Morandi visual system and must not copy third-party product branding or graphics.
