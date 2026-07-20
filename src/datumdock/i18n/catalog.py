"""集中管理 DatumDock 的中英文系统文案。"""

from __future__ import annotations

from collections.abc import Callable

from datumdock.i18n.prototype_catalog import PROTOTYPE_CATALOGS

CATALOGS: dict[str, dict[str, str]] = {
    "zh_CN": {
        "app.title": "DatumDock",
        "menu.file": "文件",
        "menu.project": "项目",
        "menu.help": "帮助",
        "action.new_workspace": "新建工作区",
        "action.open_workspace": "打开工作区",
        "action.new_project": "新建项目",
        "action.new_dataset": "新建数据集",
        "action.import_images": "导入图片",
        "action.import_xany": "导入 X-AnyLabeling 项目",
        "action.labels": "管理项目标签集",
        "action.models": "管理自动标注模型",
        "action.settings": "设置",
        "action.export": "导出数据集",
        "action.export_xany": "导出 X-AnyLabeling 项目",
        "action.export_backup": "导出项目备份",
        "action.import_backup": "导入项目备份",
        "action.similarity": "检查近似图片",
        "action.about": "关于 DatumDock",
        "action.collapse_sidebar": "折叠侧栏",
        "action.delete_sample": "删除当前图片",
        "action.trash": "打开回收站",
        "action.rename_samples": "按规则重命名数据集图片",
        "action.quality_check": "检查当前图片标注质量",
        "action.previous_sample": "上一张图片",
        "action.next_sample": "下一张图片",
        "action.undo": "撤销标注编辑",
        "action.redo": "重做标注编辑",
        "action.fit_image": "适应窗口",
        "action.zoom_in": "放大",
        "action.zoom_out": "缩小",
        "panel.workspace": "工作区与数据集",
        "panel.samples": "数据集池",
        "panel.labels": "标签与属性",
        "panel.no_workspace": "尚未打开工作区",
        "panel.no_project": "请先创建或选择项目",
        "panel.no_labels": "当前项目尚未配置标签",
        "welcome.title": "管理数据集，从一个可靠的数据池开始",
        "welcome.description": "创建或打开工作区，集中管理项目、数据集、标签和标注。",
        "welcome.create": "创建工作区",
        "welcome.open": "打开已有工作区",
        "status.ready": "就绪",
        "status.saved": "已保存",
        "status.no_dataset": "未选择数据集",
        "dialog.workspace.title": "选择新工作区位置",
        "dialog.workspace.exists": "所选目录已包含 DatumDock 工作区。",
        "dialog.project.title": "新建项目",
        "dialog.project.name": "项目名称",
        "dialog.project.copy_template": (
            "是否复制当前项目的标签集作为新项目模板？不会复制图片、标注或模型。"
        ),
        "dialog.dataset.title": "新建数据集",
        "dialog.dataset.name": "数据集名称",
        "dialog.dataset.copy_template": (
            "是否复制当前数据集的命名与预览配置？项目标签集会自动共享，不会复制图片或标注。"
        ),
        "dialog.required": "请输入名称。",
        "dialog.error": "操作失败",
        "settings.title": "设置",
        "settings.language": "界面语言",
        "settings.shortcuts": "快捷键",
        "settings.trash": "回收站少量样本阈值",
        "settings.split": "默认训练/验证/测试比例",
        "settings.close": "关闭",
        "about.title": "关于 DatumDock",
        "about.body": "本地优先的视觉数据集管理与标注桌面应用。",
        "empty.canvas": "选择数据集后即可在这里浏览和标注图片。",
        "tooltip.brand": "返回工作区概览",
        "tooltip.trash_threshold": (
            "当前数据集样本数不超过此值时，删除图片会移入项目回收站；超过后将永久删除。"
        ),
        "label.alias": "中文别名",
        "label.name": "英文训练名",
        "label.description": "描述",
        "label.synonyms": "同义词（用逗号分隔）",
        "label.color": "颜色",
        "label.class_id": "类别 ID",
        "label.status": "状态",
        "label.usage": "使用次数",
        "label.search": "搜索标签、别名、描述或同义词",
        "label.status.active": "活动",
        "label.status.archived": "已归档",
        "label.add": "新增标签",
        "label.edit": "编辑标签",
        "label.archive": "归档 / 恢复",
        "label.inspect": "检查标签图片",
        "label.updated_at": "标注更新时间",
        "label.dialog.add": "新增数据集标签",
        "label.dialog.edit": "编辑数据集标签",
        "label.choose_color": "选择颜色",
        "label.migration.title": "确认训练映射迁移",
        "label.migration.confirm": (
            "此修改会影响 {images} 张图片中的 {shapes} 个矩形。"
            "训练名变化将原子迁移对应 LabelMe JSON，是否继续？"
        ),
        "browser.search": "按文件名搜索",
        "browser.all_status": "全部状态",
        "browser.page": "第 {page} / {total} 页，共 {count} 张",
        "browser.previous_page": "上一页",
        "browser.next_page": "下一页",
        "browser.preview": "显示标注预览",
        "canvas.no_sample": "从数据集池选择一张图片开始标注。",
        "canvas.no_label": "请先在项目标签集中选择一个标签。",
        "canvas.autosaved": "标注已自动保存",
        "canvas.ready": "标注已就绪",
        "canvas.saving": "正在保存标注…",
        "canvas.recovering": "正在恢复标注…",
        "canvas.save_failed": "自动保存失败，原文件未被覆盖；请检查磁盘权限后重试。",
        "canvas.leave_failed_title": "标注尚未安全保存",
        "canvas.leave_failed_body": "请选择重试保存、放弃内存修改或取消离开。",
        "canvas.retry": "重试保存",
        "canvas.discard": "放弃内存修改",
        "canvas.review": "图片复核状态",
        "canvas.label": "当前绘制标签",
        "review.none": "无复核结论",
        "review.pending_review": "待复核",
        "review.completed": "已完成",
        "review.mark_completed": "确认已完成",
        "label.filter_all": "全部标签",
        "label.archived_hint": "已归档",
        "annotation.delete_selected": "删除所选框",
        "toast.label_saved": "标签已保存",
        "toast.annotation_warning": "当前标注包含需要检查的兼容或质量提示",
        "toast.review_requires_box": "标记为已完成前，至少需要一个有效矩形框",
        "toast.negative_requires_empty": "只有零矩形图片才能标记为已完成（无目标）",
        "dialog.import.title": "导入图片到受管数据集池",
        "dialog.import.filter": "图片文件 (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff)",
        "dialog.import.summary": "已导入 {imported} 张；跳过 {skipped} 张；失败 {failed} 张。",
        "dialog.duplicate.title": "发现完全相同的图片",
        "dialog.duplicate.body": "左侧是待导入图片，右侧是数据集中的已有图片。是否仍然保留两张？",
        "dialog.duplicate.keep": "继续导入",
        "dialog.duplicate.skip": "跳过这张",
        "dialog.delete.title": "删除当前图片",
        "dialog.delete.trash": "将删除受管图片、标注与缓存，并移动到回收站，可稍后恢复。继续吗？",
        "dialog.delete.permanent": "将永久删除受管图片、标注与缓存，此操作不可恢复。继续吗？",
        "dialog.trash.title": "项目回收站",
        "dialog.trash.id": "标识",
        "dialog.trash.restore": "恢复选中图片",
        "dialog.trash.empty": "回收站为空。",
        "dialog.rename.title": "按规则重命名数据集图片",
        "dialog.rename.prefix": "文件名前缀",
        "dialog.rename.start": "起始序号",
        "dialog.rename.padding": "序号位数",
        "dialog.rename.preview": "预览（前 {shown} / 共 {total} 张）",
        "dialog.rename.confirm": (
            "将重命名 {count} 张受管图片及其标注。外部源文件不会变更，是否继续？"
        ),
        "dialog.rename.complete": "已安全重命名 {count} 张图片。",
        "dialog.quality.title": "标注质量检查",
        "dialog.quality.none": "没有发现结构性标注问题。",
        "quality.missing_image": "受管图片文件缺失",
        "quality.missing_annotation": "受管标注文件缺失",
        "quality.empty_label": "矩形框缺少标签",
        "quality.unknown_label": "矩形框引用了未知标签",
        "quality.invalid_area": "矩形框面积无效",
        "quality.out_of_bounds": "矩形框超出图片边界",
        "dialog.export.title": "导出 YOLO 检测数据集",
        "dialog.export.directory": "导出目录",
        "dialog.export.choose": "选择目录",
        "dialog.export.include_negative": "包含无标注图片作为负样本",
        "dialog.export.train": "训练集比例",
        "dialog.export.val": "验证集比例",
        "dialog.export.test": "测试集比例",
        "dialog.export.complete": "YOLO 数据集导出完成。",
        "dialog.export.invalid_split": "训练、验证、测试比例之和必须为 100。",
        "dialog.export.no_annotated": "当前筛选条件下没有可导出的已标注图片。",
        "dialog.labels.title": "管理项目标签集",
        "dialog.labels.add": "新增标签",
        "dialog.labels.edit": "编辑标签",
        "dialog.labels.archive": "归档标签",
        "dialog.labels.merge": "合并其他项目标签集",
        "dialog.labels.merge_choose": "选择要合并标签集的来源项目",
        "dialog.labels.merge_preview": (
            "将新增 {count} 个无冲突标签。重复训练标签的完整信息必须一致，是否继续？"
        ),
        "dialog.labels.merge_no_source": "当前工作区没有可用于合并的其他项目。",
        "dialog.labels.inspect": "查看图片",
        "dialog.labels.inspect_title": "标签检查：{label}",
        "dialog.labels.inspect_dataset": "数据集",
        "dialog.labels.inspect_filename": "文件名",
        "dialog.labels.inspect_shapes": "目标框数量",
        "dialog.labels.inspect_review": "复核状态",
        "dialog.labels.inspect_open": "打开标注",
        "dialog.labels.inspect_empty": "当前项目中还没有包含此标签的图片。",
        "dialog.labels.name": "英文训练名",
        "dialog.labels.alias": "显示别名",
        "dialog.labels.description": "描述",
        "dialog.labels.color": "标签颜色",
        "dialog.labels.class_id": "类别 ID",
        "dialog.labels.migrate": "英文训练名会改写 {count} 张图片中的关联矩形，是否继续？",
        "dialog.shortcuts.title": "快捷键设置",
        "dialog.shortcuts.restore": "恢复默认",
        "dialog.shortcuts.sequence": "快捷键",
        "dialog.shortcuts.invalid": "快捷键无效或与其他操作冲突。",
        "dialog.choose_label": "切换矩形标签",
        "dialog.choose_label.prompt": "选择新的标签",
        "dialog.similarity.title": "近似图片检查",
        "dialog.similarity.group": "近似组",
        "dialog.similarity.samples": "图片",
        "dialog.similarity.status": "导出绑定",
        "dialog.similarity.confirm": "确认绑定",
        "dialog.similarity.unconfirm": "取消绑定",
        "dialog.similarity.pending": "未确认",
        "dialog.similarity.confirmed": "已确认：导出时不会拆分",
        "dialog.similarity.help": (
            "确认后，同组近似图片将固定放入同一个训练、验证或测试集合，以降低数据泄露风险。"
        ),
        "dialog.models.title": "自动标注模型库",
        "dialog.models.import": "导入模型",
        "dialog.models.update": "更新模型",
        "dialog.models.delete": "删除模型",
        "dialog.models.mapping": "配置类别映射",
        "dialog.models.current": "标注当前图片",
        "dialog.models.all": "一键标注全部图片",
        "dialog.models.unannotated": "标注所有未标注图片",
        "dialog.models.name": "模型名称",
        "dialog.models.format": "格式",
        "dialog.models.classes": "类别数",
        "dialog.models.status": "状态",
        "dialog.models.filter": "模型文件 (*.onnx *.pt)",
        "dialog.models.delete_confirm": "将永久删除当前项目中的模型文件，此操作不可恢复。继续吗？",
        "dialog.models.cpu.title": "将使用 CPU 推理",
        "dialog.models.cpu.body": "未检测到可用 GPU。可以继续使用 CPU 推理，或查看 GPU 配置说明。",
        "dialog.models.cpu.continue": "继续使用 CPU",
        "dialog.models.cpu.guide": "查看配置说明",
        "dialog.models.cpu.guide_body": (
            "请安装支持当前 NVIDIA 驱动的 PyTorch CUDA 版本，并在设置好 CUDA 后重启 DatumDock。"
        ),
        "dialog.models.mapping.class": "模型类别",
        "dialog.models.mapping.label": "项目标签",
        "dialog.models.mapping.none": "跳过此类别",
        "dialog.models.no_mapping": "请先至少映射一个模型类别到项目标签。",
        "dialog.models.complete": "自动标注完成：已处理 {count} 张图片。",
        "dialog.backup.export_title": "导出项目备份",
        "dialog.backup.import_title": "导入项目备份",
        "dialog.backup.filter": "DatumDock 项目备份 (*.ddbackup)",
        "dialog.backup.export_complete": "项目备份已创建。模型二进制按设计未包含在备份中。",
        "dialog.backup.import_complete": "项目备份验证通过并已导入。模型二进制需要单独重新导入。",
        "dialog.xany.import_title": "导入 X-AnyLabeling / LabelMe 目录",
        "dialog.xany.export_title": "导出 X-AnyLabeling 项目",
        "dialog.xany.folder_name": "导出目录名称",
        "dialog.xany.readonly_notice": (
            "外部目录只读。DatumDock 会把图片复制并规范化到当前数据集，不会修改源图片或 JSON。"
        ),
        "dialog.xany.choose_source": "选择交换目录",
        "dialog.xany.choose_source_hint": "请选择包含图片和同名 LabelMe JSON 的目录。",
        "dialog.xany.issue_level": "级别",
        "dialog.xany.issue_file": "文件",
        "dialog.xany.issue_detail": "详情",
        "dialog.xany.issue_severity_error": "错误",
        "dialog.xany.issue_severity_warning": "警告",
        "dialog.xany.issue_missing_annotation": "图片没有同名 JSON，将作为无标注图片导入",
        "dialog.xany.issue_orphan_annotation": "JSON 没有同名图片，已跳过且未修改来源文件",
        "dialog.xany.issue_symlink_skipped": "符号链接已按安全规则跳过",
        "dialog.xany.issue_image_prepare_failed": "图片准备失败：{detail}",
        "dialog.xany.issue_invalid_annotation": "标注 JSON 无效：{detail}",
        "dialog.xany.issue_sample_not_exportable": "样本无法导出：{detail}",
        "dialog.xany.external_label": "外部标签",
        "dialog.xany.shape_count": "引用数",
        "dialog.xany.mapping": "导入方式",
        "dialog.xany.preserve_readonly": "只读保留（不丢失）",
        "dialog.xany.create_label": "新建数据集标签",
        "dialog.xany.created_description": "从 X-AnyLabeling / LabelMe 目录导入",
        "dialog.xany.preflight": "开始预检",
        "dialog.xany.preflighting": "正在只读扫描、转码和校验…",
        "dialog.xany.preflight_summary": (
            "发现 {images} 张图片，存在 {errors} 个阻断问题。请确认标签映射后继续。"
        ),
        "dialog.xany.start_import": "确认导入",
        "dialog.xany.importing": "正在提交图片、标注和索引…",
        "dialog.xany.import_report": (
            "导入 {imported} 张，跳过 {skipped} 张，失败 {failed} 张；"
            "保留兼容 shape {compatibility} 个。"
        ),
        "dialog.xany.cancelling": "正在当前样本边界安全取消…",
        "dialog.xany.choose_target_parent": "选择导出父目录",
        "dialog.xany.target_parent": "父目录",
        "dialog.xany.invalid_folder_name": "请输入不含路径分隔符的新目录名称。",
        "dialog.xany.scope": "导出范围",
        "dialog.xany.scope_all": "全部活动图片",
        "dialog.xany.scope_selected": "选中的 {count} 张图片",
        "dialog.xany.export_hint": "导出只会创建一个新目录，不会修改数据集池。",
        "dialog.xany.start_export": "确认导出",
        "dialog.xany.export_preflight_summary": (
            "将导出 {images} 张图片：有标注 {annotated} 张，空标注 {empty} 张。"
        ),
        "dialog.xany.exporting": "正在生成并验证独立交换目录…",
        "dialog.xany.export_report": (
            "已导出 {images} 张图片、{rectangles} 个矩形，其中空标注 {empty} 张。"
        ),
        "dialog.xany.cancel_title": "取消当前任务",
        "dialog.xany.cancel_body": "将在当前样本安全提交完成后取消，已完成项目会保留。继续吗？",
        "dialog.xany.import_complete": (
            "已导入 {imported} 张；缺少标注 {missing} 张；无效 JSON {invalid} 个。"
        ),
        "dialog.xany.export_complete": "X-AnyLabeling 交换目录已导出。",
    },
    "en_US": {
        "app.title": "DatumDock",
        "menu.file": "File",
        "menu.project": "Project",
        "menu.help": "Help",
        "action.new_workspace": "New Workspace",
        "action.open_workspace": "Open Workspace",
        "action.new_project": "New Project",
        "action.new_dataset": "New Dataset",
        "action.import_images": "Import Images",
        "action.import_xany": "Import X-AnyLabeling Project",
        "action.labels": "Manage Project Labels",
        "action.models": "Manage Auto-annotation Models",
        "action.settings": "Settings",
        "action.export": "Export Dataset",
        "action.export_xany": "Export X-AnyLabeling Project",
        "action.export_backup": "Export Project Backup",
        "action.import_backup": "Import Project Backup",
        "action.similarity": "Review Similar Images",
        "action.about": "About DatumDock",
        "action.collapse_sidebar": "Collapse Sidebar",
        "action.delete_sample": "Delete Current Image",
        "action.trash": "Open Trash",
        "action.rename_samples": "Rename Dataset Images by Rule",
        "action.quality_check": "Check Current Annotation Quality",
        "action.previous_sample": "Previous Image",
        "action.next_sample": "Next Image",
        "action.undo": "Undo Annotation Edit",
        "action.redo": "Redo Annotation Edit",
        "action.fit_image": "Fit to Window",
        "action.zoom_in": "Zoom In",
        "action.zoom_out": "Zoom Out",
        "panel.workspace": "Workspace & Datasets",
        "panel.samples": "Dataset Pool",
        "panel.labels": "Labels & Properties",
        "panel.no_workspace": "No workspace is open",
        "panel.no_project": "Create or select a project first",
        "panel.no_labels": "The current project has no labels yet",
        "welcome.title": "Start dataset management with a reliable data pool",
        "welcome.description": (
            "Create or open a workspace to manage projects, datasets, labels, and annotations."
        ),
        "welcome.create": "Create Workspace",
        "welcome.open": "Open Existing Workspace",
        "status.ready": "Ready",
        "status.saved": "Saved",
        "status.no_dataset": "No dataset selected",
        "dialog.workspace.title": "Choose a New Workspace Location",
        "dialog.workspace.exists": "The selected folder already contains a DatumDock workspace.",
        "dialog.project.title": "New Project",
        "dialog.project.name": "Project Name",
        "dialog.project.copy_template": (
            "Copy the current project's label set as the new project template? "
            "Images, annotations, and models are not copied."
        ),
        "dialog.dataset.title": "New Dataset",
        "dialog.dataset.name": "Dataset Name",
        "dialog.dataset.copy_template": (
            "Copy the current dataset's naming and preview settings? "
            "The project label set is shared; "
            "images and annotations are not copied."
        ),
        "dialog.required": "Enter a name.",
        "dialog.error": "Operation Failed",
        "settings.title": "Settings",
        "settings.language": "Interface Language",
        "settings.shortcuts": "Shortcuts",
        "settings.trash": "Small-sample Trash Threshold",
        "settings.split": "Default Train / Val / Test Split",
        "settings.close": "Close",
        "about.title": "About DatumDock",
        "about.body": (
            "A local-first desktop application for visual dataset management and annotation."
        ),
        "empty.canvas": "Select a dataset to browse and annotate images here.",
        "tooltip.brand": "Return to workspace overview",
        "tooltip.trash_threshold": (
            "When the current dataset has no more than this number of samples, deleting "
            "an image moves it to the project trash; larger datasets delete permanently."
        ),
        "label.alias": "Chinese Alias",
        "label.name": "Training Name",
        "label.description": "Description",
        "label.synonyms": "Synonyms (comma separated)",
        "label.color": "Color",
        "label.class_id": "Class ID",
        "label.status": "Status",
        "label.usage": "Usage",
        "label.search": "Search labels, aliases, descriptions, or synonyms",
        "label.status.active": "Active",
        "label.status.archived": "Archived",
        "label.add": "Add Label",
        "label.edit": "Edit Label",
        "label.archive": "Archive / Restore",
        "label.inspect": "Inspect Label Images",
        "label.updated_at": "Annotation Updated",
        "label.dialog.add": "Add Dataset Label",
        "label.dialog.edit": "Edit Dataset Label",
        "label.choose_color": "Choose Color",
        "label.migration.title": "Confirm Training Mapping Migration",
        "label.migration.confirm": (
            "This change affects {shapes} rectangles in {images} images. "
            "A training-name change will atomically migrate the related LabelMe JSON files. "
            "Continue?"
        ),
        "browser.search": "Search filenames",
        "browser.all_status": "All statuses",
        "browser.page": "Page {page} / {total}, {count} images",
        "browser.previous_page": "Previous Page",
        "browser.next_page": "Next Page",
        "browser.preview": "Show annotation preview",
        "canvas.no_sample": "Choose an image from the dataset pool to start annotating.",
        "canvas.no_label": "Choose a label from the project label set first.",
        "canvas.autosaved": "Annotation saved automatically",
        "canvas.ready": "Annotation ready",
        "canvas.saving": "Saving annotation…",
        "canvas.recovering": "Recovering annotation…",
        "canvas.save_failed": (
            "Automatic save failed. The original file was not overwritten; "
            "check permissions and retry."
        ),
        "canvas.leave_failed_title": "Annotation is not safely saved",
        "canvas.leave_failed_body": (
            "Retry saving, discard the in-memory changes, or cancel leaving this image."
        ),
        "canvas.retry": "Retry Save",
        "canvas.discard": "Discard In-Memory Changes",
        "canvas.review": "Image review status",
        "canvas.label": "Drawing label",
        "review.none": "No review decision",
        "review.pending_review": "Pending Review",
        "review.completed": "Completed",
        "review.mark_completed": "Mark Completed",
        "label.filter_all": "All Labels",
        "label.archived_hint": "Archived",
        "annotation.delete_selected": "Delete Selected Box",
        "toast.label_saved": "Label saved",
        "toast.annotation_warning": (
            "This annotation contains compatibility or quality warnings that need review"
        ),
        "toast.review_requires_box": "Completed requires at least one valid rectangle",
        "toast.negative_requires_empty": (
            "Only an image with zero rectangles can be completed as no objects"
        ),
        "dialog.import.title": "Import Images into the Managed Dataset Pool",
        "dialog.import.filter": "Image files (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff)",
        "dialog.import.summary": "Imported {imported}; skipped {skipped}; failed {failed}.",
        "dialog.duplicate.title": "Identical Image Detected",
        "dialog.duplicate.body": (
            "The pending image is on the left and an existing dataset image is on the right. "
            "Keep both?"
        ),
        "dialog.duplicate.keep": "Keep and Import",
        "dialog.duplicate.skip": "Skip This Image",
        "dialog.delete.title": "Delete Current Image",
        "dialog.delete.trash": (
            "The managed image, annotation, and cache will move to Trash and can be restored. "
            "Continue?"
        ),
        "dialog.delete.permanent": (
            "The managed image, annotation, and cache will be permanently deleted. "
            "This cannot be undone. Continue?"
        ),
        "dialog.trash.title": "Project Trash",
        "dialog.trash.id": "ID",
        "dialog.trash.restore": "Restore Selected Image",
        "dialog.trash.empty": "Trash is empty.",
        "dialog.rename.title": "Rename Dataset Images by Rule",
        "dialog.rename.prefix": "Filename Prefix",
        "dialog.rename.start": "Starting Number",
        "dialog.rename.padding": "Number Padding",
        "dialog.rename.preview": "Preview (first {shown} of {total})",
        "dialog.rename.confirm": (
            "This will rename {count} managed images and their annotations. "
            "External source files stay unchanged. Continue?"
        ),
        "dialog.rename.complete": "Safely renamed {count} images.",
        "dialog.quality.title": "Annotation Quality Check",
        "dialog.quality.none": "No structural annotation issue was found.",
        "quality.missing_image": "The managed image file is missing",
        "quality.missing_annotation": "The managed annotation file is missing",
        "quality.empty_label": "A rectangle has no label",
        "quality.unknown_label": "A rectangle references an unknown label",
        "quality.invalid_area": "A rectangle has an invalid area",
        "quality.out_of_bounds": "A rectangle extends beyond image bounds",
        "dialog.export.title": "Export YOLO Detection Dataset",
        "dialog.export.directory": "Export directory",
        "dialog.export.choose": "Choose Directory",
        "dialog.export.include_negative": "Include unannotated images as negative samples",
        "dialog.export.train": "Train split",
        "dialog.export.val": "Validation split",
        "dialog.export.test": "Test split",
        "dialog.export.complete": "YOLO dataset export complete.",
        "dialog.export.invalid_split": "Train, validation, and test splits must add up to 100.",
        "dialog.export.no_annotated": "There are no annotated images available for export.",
        "dialog.labels.title": "Manage Project Label Set",
        "dialog.labels.add": "Add Label",
        "dialog.labels.edit": "Edit Label",
        "dialog.labels.archive": "Archive Label",
        "dialog.labels.merge": "Merge Another Project's Label Set",
        "dialog.labels.merge_choose": "Select the source project whose label set to merge",
        "dialog.labels.merge_preview": (
            "This adds {count} non-conflicting labels. All information of duplicate "
            "training labels must match. Continue?"
        ),
        "dialog.labels.merge_no_source": "There is no other project available to merge.",
        "dialog.labels.inspect": "View Images",
        "dialog.labels.inspect_title": "Label Inspection: {label}",
        "dialog.labels.inspect_dataset": "Dataset",
        "dialog.labels.inspect_filename": "Filename",
        "dialog.labels.inspect_shapes": "Boxes",
        "dialog.labels.inspect_review": "Review Status",
        "dialog.labels.inspect_open": "Open Annotation",
        "dialog.labels.inspect_empty": "No image in this project contains this label yet.",
        "dialog.labels.name": "English Training Name",
        "dialog.labels.alias": "Display Alias",
        "dialog.labels.description": "Description",
        "dialog.labels.color": "Label Color",
        "dialog.labels.class_id": "Class ID",
        "dialog.labels.migrate": (
            "Changing the training name rewrites matching rectangles in {count} images. Continue?"
        ),
        "dialog.shortcuts.title": "Shortcut Settings",
        "dialog.shortcuts.restore": "Restore Defaults",
        "dialog.shortcuts.sequence": "Shortcut",
        "dialog.shortcuts.invalid": "The shortcut is invalid or conflicts with another action.",
        "dialog.choose_label": "Change Rectangle Label",
        "dialog.choose_label.prompt": "Choose the new label",
        "dialog.similarity.title": "Similar Image Review",
        "dialog.similarity.group": "Similarity Group",
        "dialog.similarity.samples": "Images",
        "dialog.similarity.status": "Export Binding",
        "dialog.similarity.confirm": "Confirm Binding",
        "dialog.similarity.unconfirm": "Remove Binding",
        "dialog.similarity.pending": "Unconfirmed",
        "dialog.similarity.confirmed": "Confirmed: never split during export",
        "dialog.similarity.help": (
            "Once confirmed, similar images in a group stay in the same train, validation, "
            "or test split to reduce data leakage."
        ),
        "dialog.models.title": "Auto-annotation Model Library",
        "dialog.models.import": "Import Model",
        "dialog.models.update": "Update Model",
        "dialog.models.delete": "Delete Model",
        "dialog.models.mapping": "Configure Class Mapping",
        "dialog.models.current": "Annotate Current Image",
        "dialog.models.all": "Annotate All Images",
        "dialog.models.unannotated": "Annotate All Unannotated Images",
        "dialog.models.name": "Model Name",
        "dialog.models.format": "Format",
        "dialog.models.classes": "Class Count",
        "dialog.models.status": "Status",
        "dialog.models.filter": "Model files (*.onnx *.pt)",
        "dialog.models.delete_confirm": (
            "The current project model file will be permanently deleted. Continue?"
        ),
        "dialog.models.cpu.title": "CPU Inference Will Be Used",
        "dialog.models.cpu.body": (
            "No usable GPU was detected. Continue with CPU inference or view GPU setup guidance."
        ),
        "dialog.models.cpu.continue": "Continue with CPU",
        "dialog.models.cpu.guide": "View Setup Guidance",
        "dialog.models.cpu.guide_body": (
            "Install a PyTorch CUDA build compatible with your NVIDIA driver, "
            "then restart DatumDock after CUDA is configured."
        ),
        "dialog.models.mapping.class": "Model Class",
        "dialog.models.mapping.label": "Project Label",
        "dialog.models.mapping.none": "Skip This Class",
        "dialog.models.no_mapping": "Map at least one model class to a project label first.",
        "dialog.models.complete": "Auto-annotation complete: processed {count} images.",
        "dialog.backup.export_title": "Export Project Backup",
        "dialog.backup.import_title": "Import Project Backup",
        "dialog.backup.filter": "DatumDock project backup (*.ddbackup)",
        "dialog.backup.export_complete": (
            "Project backup created. Model binaries are intentionally excluded."
        ),
        "dialog.backup.import_complete": (
            "Project backup verified and imported. Re-import model binaries separately."
        ),
        "dialog.xany.import_title": "Import X-AnyLabeling / LabelMe Directory",
        "dialog.xany.export_title": "Export X-AnyLabeling Project",
        "dialog.xany.folder_name": "Export folder name",
        "dialog.xany.readonly_notice": (
            "The external directory is read-only. DatumDock copies and normalizes images "
            "into this dataset without changing source images or JSON files."
        ),
        "dialog.xany.choose_source": "Choose Exchange Directory",
        "dialog.xany.choose_source_hint": (
            "Choose a directory containing images and same-stem LabelMe JSON files."
        ),
        "dialog.xany.issue_level": "Level",
        "dialog.xany.issue_file": "File",
        "dialog.xany.issue_detail": "Details",
        "dialog.xany.issue_severity_error": "Error",
        "dialog.xany.issue_severity_warning": "Warning",
        "dialog.xany.issue_missing_annotation": (
            "The image has no same-stem JSON and will be imported without annotations."
        ),
        "dialog.xany.issue_orphan_annotation": (
            "The JSON has no same-stem image; it was skipped without changing the source."
        ),
        "dialog.xany.issue_symlink_skipped": "The symbolic link was skipped for safety.",
        "dialog.xany.issue_image_prepare_failed": "Image preparation failed: {detail}",
        "dialog.xany.issue_invalid_annotation": "Invalid annotation JSON: {detail}",
        "dialog.xany.issue_sample_not_exportable": "The sample cannot be exported: {detail}",
        "dialog.xany.external_label": "External Label",
        "dialog.xany.shape_count": "Uses",
        "dialog.xany.mapping": "Import As",
        "dialog.xany.preserve_readonly": "Preserve Read-only (No Loss)",
        "dialog.xany.create_label": "Create Dataset Label",
        "dialog.xany.created_description": "Imported from an X-AnyLabeling / LabelMe directory",
        "dialog.xany.preflight": "Run Preflight",
        "dialog.xany.preflighting": "Scanning, normalizing, and validating read-only sources…",
        "dialog.xany.preflight_summary": (
            "Found {images} images with {errors} blocking issues. "
            "Confirm label mappings to continue."
        ),
        "dialog.xany.start_import": "Import",
        "dialog.xany.importing": "Committing images, annotations, and indexes…",
        "dialog.xany.import_report": (
            "Imported {imported}, skipped {skipped}, failed {failed}; "
            "preserved {compatibility} compatibility shapes."
        ),
        "dialog.xany.cancelling": "Cancelling safely at the current sample boundary…",
        "dialog.xany.choose_target_parent": "Choose Export Parent",
        "dialog.xany.target_parent": "Parent Directory",
        "dialog.xany.invalid_folder_name": "Enter a new folder name without path separators.",
        "dialog.xany.scope": "Export Scope",
        "dialog.xany.scope_all": "All Active Images",
        "dialog.xany.scope_selected": "{count} Selected Images",
        "dialog.xany.export_hint": (
            "Export creates a new directory and never modifies the managed pool."
        ),
        "dialog.xany.start_export": "Export",
        "dialog.xany.export_preflight_summary": (
            "Export {images} images: {annotated} annotated and {empty} empty."
        ),
        "dialog.xany.exporting": "Generating and validating the standalone exchange directory…",
        "dialog.xany.export_report": (
            "Exported {images} images and {rectangles} rectangles, "
            "including {empty} empty annotations."
        ),
        "dialog.xany.cancel_title": "Cancel Current Task",
        "dialog.xany.cancel_body": (
            "Cancellation takes effect after the current sample commits safely. "
            "Completed items remain. Continue?"
        ),
        "dialog.xany.import_complete": (
            "Imported {imported}; missing annotations {missing}; invalid JSON {invalid}."
        ),
        "dialog.xany.export_complete": "X-AnyLabeling exchange directory exported.",
    },
}

for _locale, _messages in PROTOTYPE_CATALOGS.items():
    CATALOGS[_locale].update(_messages)


class LocaleService:
    """向所有活跃界面广播即时语言切换，领域数据不参与翻译。"""

    def __init__(self, locale: str = "zh_CN") -> None:
        self.locale = locale if locale in CATALOGS else "zh_CN"
        self._listeners: list[Callable[[], None]] = []

    def text(self, key: str) -> str:
        """读取当前语言文案；缺失键显示键名以便开发阶段发现遗漏。"""

        return CATALOGS[self.locale].get(key, key)

    def set_locale(self, locale: str) -> None:
        """仅刷新系统界面，不对项目内容执行任何改写。"""

        if locale not in CATALOGS:
            raise ValueError(f"不支持的界面语言: {locale}")
        self.locale = locale
        for listener in list(self._listeners):
            listener()

    def subscribe(self, listener: Callable[[], None]) -> None:
        """注册可重翻译窗口，避免各控件散落硬编码文案。"""

        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        """在临时对话框关闭时移除监听，避免已销毁 Qt 对象被再次刷新。"""

        if listener in self._listeners:
            self._listeners.remove(listener)


def tr(service: LocaleService, key: str) -> str:
    """为界面代码提供简短的集中翻译入口。"""

    return service.text(key)
