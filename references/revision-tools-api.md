# 在已有 CAD 上执行可核验的局部修订

`inventory` 读取现有 DXF 的文件哈希、单位、模型空间句柄、稳定 ID 与关键几何；
`revise` 执行绑定该文件的 JSON 修改计划。每步保存、重开并检查，全部通过后才向新目录
发布 `drawing.dxf`、`revision-plan.json`、`revision-report.json`。源文件不被覆盖。
这套工具补充现有 CAD 原生编辑路径，不需要把已有图纸重建为 `DrawingSpec`。

新增 `delete_entities`、`replace_outline`、`dimension_remeasure`、`dimension_drive` 也由同一修订工作流执行，
其严格字段、分段后追踪和保存核验见 [常用修改](edit-operations.md)。
用 `python -m cad_skill_core operations --json` 查看当前支持的操作，不猜接口。

## 使用入口

在本仓库根目录安装到所选 Python 3.12 环境：`python -m pip install -e .`。
随后可从任意工作目录使用 `cad-skill`，或使用同一环境的模块入口：

```text
python -m cad_skill_core inventory BASELINE.dxf --json
python -m cad_skill_core revise BASELINE.dxf PLAN.json --out NEW_OUTPUT --dry-run --json
python -m cad_skill_core revise BASELINE.dxf PLAN.json --out NEW_OUTPUT --json
```

`--dry-run` 在临时副本上真实执行和核验，不发布输出。正式执行只接受新目录，或完全匹配
基准／计划／结果哈希的既有成功目录；后者返回 `reused: true`，不会再次累计移动。
失败时不发布半成品。修图前仍须判断基准与用户批注的版本关系，不能仅取时间最新的文件。

## 从批注到明确条件

工具不猜测自由文本的零件、方向或工程数值。先根据 CAD 和批注确认对象，再登记条件。
选择器只能是 `{"handle":"2F"}` 或 `{"id":"screw.top"}`；句柄来自当前文件，稳定 ID
来自实体 `CAD_SKILL_ID` XDATA。查不到、重复或有歧义会失败，不能用“最近对象”替代。

| 用户要求 | 操作及需要明确的条件 |
|---|---|
| 与左边平齐 | `align`：目标集合 `targets`、`anchor`，加 `reference` 与 `reference_anchor`，或明确 `coordinate`；可设 `offset` |
| 下移至接触下边缘 | `contact`：明确两个有限直边及其 `min_y/max_y` 等边界，偏移必须为零；工具检查真实边段重叠，不只比较包围框 |
| 底部不动，头部向上延伸 | `resize_fixed_edge`：`axis: "y"`、`fixed: "min"`、最终 `size`；只改变外侧移动边界，中间顶点和固定边保持 |
| 恢复原长 | `set_length`：明确 LINE、从实际旧版核定的 `length`、固定 `start` 或 `end`；不从截图猜长度 |
| 尺寸线外移 | `dimension_move`：原生尺寸 `target` 与位于新尺寸线的 `position: [x,y]`；测量原点和文字语义保持 |
| 尺寸文字联动移动 | `dimension_text_mode`：原生尺寸 `target`，`mode` 为 0、1 或 2；0 随文字移动尺寸线，1 加引线，2 自由文字位置；交互仍须原生 CAD 抽查 |
| 移动引线文字，箭头不动 | `leader_move`：原生 MULTILEADER 与文字新位置；箭头目标和已有折点保持 |
| 把分开的说明与引线连起来 | `link_callout`：明确 TEXT/MTEXT、连接的 `lines` 和 `arrow_target`；仅 LINE 链时必须提供 `arrow_name`、`arrow_size` |
| 交点处分段，剖面线除外 | `split_intersections`：显式目标或省略以检查全部可支持轮廓；必须放在计划最后，保留 HATCH 和原生标注 |

`anchor` 可为 `min_x/max_x/center_x/min_y/max_y/center_y`。
接触要求存在长度超过容差的边段重叠，不把一个孤立相切点视为边接触。
`resize_fixed_edge` 的 `size` 是沿该轴的最终跨度，不是增加量。
标注的 `position` 为 WCS XY 坐标。引线链接保留完整数量、直径、公差和文字样式；
不会把第一个数字替换为测量占位符。已有 LEADER 的箭头形状、比例和禁用状态可继承。

## 计划与续作

```json
{
  "schema": "cad-revision/v1",
  "baseline_sha256": "填写 inventory 返回的实际 SHA-256",
  "units": "mm",
  "tolerance": 0.0000001,
  "operations": [
    {
      "id": "comment-1",
      "comment": "底部不动，头部延伸至已核定高度",
      "type": "resize_fixed_edge",
      "targets": [{"id": "screw.head"}],
      "axis": "y",
      "fixed": "min",
      "size": 9
    }
  ],
  "checks": []
}
```

上例仅展示字段；`size`、对象 ID 和容差都必须按实际任务确定。
单位只接受 `unitless/in/ft/mm/cm/m`，须与 `$INSUNITS` 一致，工具不自动换算。
每项 `id` 唯一，`comment/source` 可记录依据。后续要求确实取代同计划前项时才填写
`supersedes: ["较早操作 ID"]`；否则原要求继续核验。`checks` 用于额外的最终条件，
字段是可检查的几何或标注操作，不带 `id/comment` 元信息。
分段会生成新句柄和子 ID；后续轮次用报告中的 `mapping/source_ids` 重新定位，
不能继续套用已删除父实体的句柄。最终 `checks` 一般不能引用被分段删除的实体；
与本次 `replace_outline` 完全相同的检查可绑定已核验的最终子段证据，详见常用修改文档。

完整可执行样例在 [examples/revision-tools](../examples/revision-tools/README.md)。
它生成输入副本和匹配哈希的计划，覆盖全部操作，不使用用户交付图纸。

## 支持边界与验收

- 编辑范围是 DXF 模型空间。DWG 输入会拒绝；既有 DWG 优先用原生 CAD 编辑，确需 DXF
  中间副本时另做转换前后的原生对象、布局、样式及关联数据核验。
- 对齐支持平面 LINE、零宽且无弧段 LWPOLYLINE、ARC、CIRCLE；接触和固定边延伸要求
  真实直线边界；恢复长度只支持 LINE。非平面、非标准挤出、厚度或不支持的宽度／弧段拒绝。
- 分段支持可表达的平面直线、圆弧、圆和零宽多段线（含弧段），处理交叉、T 接点、相切和
  重合端点。保留属性、记录源到子段映射，核对空间覆盖与残余交点。
  默认扫描发现不支持的轮廓返回不完整结果，文件工作流不会把它发布为成功；显式选中不支持
  对象则在修改前拒绝。`include_annotation_intersections: true` 可只读原生标注显示几何作切点参考。
- 原生标注编辑目前面向 XY 平面的线性／对齐 DIMENSION 和 MTEXT 内容的 MULTILEADER。
  不支持的尺寸类型、斜伸线、手动放置尺寸文字、断开的引线链等会拒绝；不要以拆散或普通块替代。
  链接时若源文字或引线携带无法保留的自定义 XDATA、扩展字典、反应器等附加记录，会先拒绝转换，
  不会删除记录后报告成功。最终检查同样拒绝未知字段和缺失的位置参数。
- 修改核验检查对象类型、测量／文字／箭头目标、选定几何关系、分段覆盖和未授权模型空间变化。
  它不自动修复相邻填充，不证明几何关联、不证明全部布局或第三方对象可无损往返，
  也不替代工程设计验证。报告 `native_interaction_tested: false` 如实保留。
- 原生 DIMENSION 的匿名显示块属于尺寸内部结构，不等于把整条标注打成普通 INSERT。
  有夹点移动要求时，在独立副本中抽查实际操作；最终 CAD 重开检查与 PDF 局部外观检查仍须完成。
- 本命令只发布 DXF 和证据 JSON。DWG／PDF 用现有 AutoCAD 导出边界从此次结果生成并核对，
  不能用旧 PDF 配新 CAD，不能把 `status: passed` 等同于所有请求格式已交付。
