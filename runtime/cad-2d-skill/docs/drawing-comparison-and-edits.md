# 关联图纸、样图、预览和常用修改

使用当前已核验图纸的副本继续修订。先登记源文件哈希和明确选择器，再调用下表工具。
所有接口都使用已安装的 `cad_skill_core`；不要从工程脚本猜测函数名。

| 需要解决的问题 | 工具 | 详细格式 |
| --- | --- | --- |
| 同一尺寸在总装、视图、零件图中是否一致 | `check-drawing-set SET.json --json` | [关联清单](drawing-set.md) |
| 学习样图画法，同时明确哪些尺寸保留或采用 | `compare-reference CURRENT.dxf SAMPLE.dxf MAP.json --out NEW_DIR --json` | [样图映射](reference-comparison.md) |
| 一次绘制、多处局部预览和独立矢量审阅 PDF | `preview CURRENT.dxf --out NEW_DIR --pdf --json` | [字体、区域与缓存](preview-tools.md) |
| 删除标注、换局部轮廓、更新真实尺寸端点 | `revise CURRENT.dxf PLAN.json --out NEW_DIR --json` | [常用修改](edit-operations.md) |
| 查看当前工具真正支持的操作与字段 | `operations --json` | [原有修订操作](revision-tools.md) |

命令前缀为 `python -m cad_skill_core`。
原有 `inventory/revise/review/check-comments/doctor` 继续使用。

## 设计与核验规则

1. 尺寸修改后明确关联对象、图纸路径和单位。`editable` 只记录用户授权的范围；
   未授权图纸出现不一致时列为待同步，不能自行修改。文字恰好相同不能代替几何读数。
2. 画法与工程参数分开登记。仅作外观参考不授权改尺寸；用户已明确同意的几何采纳记录
   到 `authorization`，不用再重复询问。没有依据时继续处理独立、已确定的部分。
3. 统一使用共享预览，核对实际字体文件和字形；不能默默以替代字体画中文。
   字体映射只用于审阅副本。两图局部采用同尺度，剖面和文字遮罩须目视确认。
4. 删除和替换按显式选择器执行；改尺寸保留原生 DIMENSION。
   保存重开检查真实结果，并核对未授权变化。轮廓替换后再分段时记录最终子段映射。
   关联填充、布局与原生夹点行为仍按任务要求另行检查。

`checked/passed/rendered` 的范围以各报告为准；数值通过或 PNG/PDF 生成不等于
DWG 已交付、原生交互已验证或整个工程设计已获确认。最终交付只列实际文件与检查证据。
