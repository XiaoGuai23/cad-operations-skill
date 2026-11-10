# 圆弧、闭合复合轮廓与显式尺寸同步

按需求区分以下能力，避免把文件级编辑称为 AutoCAD 实时约束：

| 需求 | 当前实现 | 边界 |
| --- | --- | --- |
| 统一描述圆与圆弧 | `circular` → CIRCLE / ARC | 仍为独立实体 |
| 直线和圆弧成为一个闭合对象 | `contour` → 闭合 LWPOLYLINE，圆弧用 bulge | 新建规约；不自动扫描、合并现有图纸对象 |
| 改尺寸数值后改图形 | `dimension_drive` | 独立 CIRCLE、ARC 或 LINE，显式执行 |
| 改图形后重测标注 | `dimension_remeasure` | 线性/对齐端点，或圆/圆弧半径、圆直径；显式执行 |
| 改圆角同时保持邻边相切 | `geometry.parametric` 的圆角矩形参数函数 | 轴对齐、四角等半径；不是通用约束求解器 |
| AutoCAD 内拖动、编辑约束值后实时联动 | 尚未集成或验收 | 需要原生关联/约束，不能用离线测试代替 |

## 圆与圆弧统一规约

```json
{"kind":"circular","layer":"OUTLINE","source_id":"hole",
 "center":[0,0],"radius":10,"start_angle":0,"sweep":360}
```

`center`、`radius`、角度必须有限；半径为正，`0 < sweep <= 360`。
角度单位为度，`sweep` 沿逆时针。整圈写 CIRCLE，部分圈写 ARC；旧 `circle` / `arc`
仍可使用。`to_circular()` 归一化已有声明式模型。修订的 `replace_outline` 也接受
`type: "CIRCULAR"`，字段为 `center/radius/start_angle/sweep`。它不会把多实体变成一个轮廓。

## 单一闭合复合轮廓

`ContourEntity(kind="contour")` 接受二选一输入：有序 `segments` 或带 bulge 的
`vertices`。点在 WCS XY 平面；首尾闭合，不重复末尾的首点。以下是两条直线和两段
半圆组成的一个闭合槽形对象：

```json
{
  "kind":"contour", "layer":"OUTLINE", "source_id":"slot.boundary",
  "require_tangent":true, "tolerance":0.0000001,
  "segments":[
    {"kind":"line","start":[0,-5],"end":[20,-5]},
    {"kind":"arc","center":[20,0],"radius":5,"start_angle":-90,"sweep":180},
    {"kind":"line","start":[20,5],"end":[0,5]},
    {"kind":"arc","center":[0,0],"radius":5,"start_angle":90,"sweep":180}
  ]
}
```

轮廓圆弧允许 `0 < abs(sweep) < 360`，负值表示顺时针，与 `circular` 的整圈规则不同。
`vertices` 每项形如 `{"point":[0,0],"bulge":0.41421356237309503}`；bulge 属于该点
到下一点的段，`0` 表示直线，`tan(sweep/4)` 表示圆弧（公式中的 sweep 用弧度）。

`compile_contour()` 检查有限值、端点连续、闭合、零长度、重叠、自交和非相邻接触；
`require_tangent=true` 还检查每个接点切向连续。验证通过后 DXF 写入一个闭合
LWPOLYLINE，保留真实圆弧，不用短直线拟合。容差使用绘图单位，应按图纸尺度设置。
非零 bulge 的弓高不超过容差，或其支撑圆的浮点精度不足时会拒绝；不会自动
把浅圆弧当直线。若原意是直线，应明确使用零 bulge 或 line。

圆孔通常仍保留独立 CIRCLE。此处没有零件分组/块管理，也没有“选中一组已有对象即可
合并”的修订操作。现有图纸需要此操作时，先核实原对象属性、标注和填充依赖，不能
删掉原对象再用新生成轮廓覆盖以假装保留关联。

## 有范围限制的圆角参数化

`cad_skill_core.geometry.parametric` 提供可组合的 Python API：

```python
from cad_skill_core.geometry.parametric import (
    RoundedRectangleParameters, rounded_rectangle,
    update_rounded_rectangle, derive_rounded_rectangle,
)

outline = rounded_rectangle(
    RoundedRectangleParameters(width=60.0, height=40.0, radius=5.0),
    source_id="plate.boundary",
)
changed = update_rounded_rectangle(outline, radius=8.0)
measured = derive_rounded_rectangle(changed)
assert abs(measured.radius - 8.0) < 1e-7
```

只支持轴对齐、四角相同半径、四条非零直边的圆角矩形，必须满足
`0 < radius < min(width,height)/2`。修改宽、高、半径或原点后，重新计算切点和相邻边，
再验证闭合及相切；从合法轮廓反测参数时拒绝不匹配的形状。原对象不就地修改。

这些函数返回声明式轮廓/参数，未与现有 DIMENSION 自动绑定，也不是 `revise` 的
新操作。可用于新图生成及受控规约更新；不要据此宣称任意圆角、任意机械零件已能
双向求解。带孔零件、不同半径、旋转矩形、胶囊、零半径和任意约束网需另行实现。

## 尺寸驱动独立几何

以下片段放入 `cad-revision/v1` 计划的操作列表；句柄只是示例，须由当前图纸
`inventory` 获取，或用唯一稳定 ID：

```json
{"id":"drive-hole-dia","type":"dimension_drive","target":{"handle":"31"},
 "mode":"diameter","value":16,"geometry":[{"handle":"2A"}]}
```

| mode | 对象 | 修改范围 |
| --- | --- | --- |
| `diameter` | 直径 DIMENSION + 一个 CIRCLE | 固定圆心，半径变为 `value/2` |
| `radius` | 半径 DIMENSION + 一个 CIRCLE 或 ARC | 固定圆心，半径变为 `value`，圆弧起止角不变 |
| `linear` | 线性/对齐 DIMENSION + 一条 LINE | 增加 `fixed:"p1"` 或 `"p2"`，对应标注端点固定，沿原方向改变线长 |

执行前检查标注与所选几何的实测值、圆心/端点及圆弧跨度一致；线性尺寸的投影值
必须与整条线长一致。选择关系必须明确，不按距离猜测。驱动不调整相邻直线、其他
标注或填充，修改孤立圆弧半径可能破坏相切，因此需要整组参数关系时不能用本操作替代。

标注须使用实测文字（`<>` 或空默认值），十进制、无换算单位，且 `DIMLFAC=1`。
人工定位文字（dimtype 的 128 位）和人工文字覆盖均拒绝；
需要清除覆盖时先核对其原意。复杂关联、扩展字典、反应器、代理数据或共享显示块
按现有边界拒绝，不通过删除它们强行修改。

## 几何改变后显式重测

圆半径或圆弧半径改变、圆心未变时：

```json
{"id":"sync-radius","type":"dimension_remeasure","target":{"handle":"32"},
 "mode":"radius","geometry":[{"handle":"2B"}]}
```

圆直径重测改为 `mode:"diameter"`，几何必须是 CIRCLE。此模式不传 `value`、`p1`、
`p2`；它读取当前半径并重建标注。旧标注的圆心和标注射线仍须对应所选曲线，圆弧
附着方向必须落在当前跨度内。圆心已移动、目标关系含糊或圆弧方向不匹配时拒绝，
不推断新附着点。

线性/对齐标注继续使用原有明确端点形式：

```json
{"id":"sync-depth","type":"dimension_remeasure","target":{"handle":"33"},
 "p1":[0,0],"p2":[20,0]}
```

这种线性重测保留原有文字覆盖，不建立几何引用。人工覆盖数字可能仍显示旧数，需
检查原意。圆形重测则要求实测文字，不能把两种模式的文字策略混为一谈。

## 事务、核验与原生边界

统一经 `apply_edit` / `revise` 入口执行，不直接调用内部 `_drive` / `_remeasure`。
编辑先在文档副本演练，正式执行失败时恢复文档检查点（包括标注属性、显示块和新建
资源）。文件修订继续使用基线哈希、副本和保存重开核验；检查点不等于磁盘备份。
不允许并发修改同一内存文档。

圆形驱动/重测检查当前圆心、边界点、圆弧跨度和数值；有证据时还检查几何、稳定 ID、
元数据和显示缓存。生成的证据不由用户手填。后续操作改变同一对象时，最终检查必须
仍与最终意图一致；失败则处理冲突，不能删检查来发布。

`native_interaction_tested` 保持 false，直至在 AutoCAD 的独立图纸副本中实测。
已有受信任原生执行器仅验证 AutoCAD 2023 / R24.2，其他版本不能直接宣称兼容。
运行 `doctor` 检查当前环境；若签名或身份校验失败，保留原因并修复安装，不绕过检查。

AutoCAD 的 [关联标注](https://help.autodesk.com/cloudhelp/2016/ENU/AutoCAD-Core/files/GUID-8B7E8C96-C30D-409E-881E-7942871E80DA.htm)
随几何更新；[尺寸约束](https://help.autodesk.com/cloudhelp/2023/ENU/AutoCAD-Core/files/GUID-F7ED2587-8F25-4770-BD13-C7C95456E707.htm)
通过约束变量控制几何。仅设置 DIMASSOC=2 或修改普通标注文字不会给本工具创建原生
驱动关系。需要原生实时行为时另行实现并测试尺寸/几何约束、冲突处理、撤销重做、
保存重开、多文档隔离和实际夹点交互。

## 可执行示例

已安装运行环境后，在新的任务输出目录运行：

```powershell
python -X utf8 <skill目录>/examples/circular-dimensions/make_demo.py --out <新的输出目录>
```

示例生成原始图、尺寸驱动结果、几何改变但尚未重测的中间图、重测结果，以及 R10/R20
圆角轮廓；同时输出可供 `revise` 使用的计划和检查报告。目录已存在会拒绝，避免覆盖。
`03-geometry-edited-before-sync.dxf` 刻意保留旧标注，用于展示为何需要显式重测，不能
作为最终交付图。脚本不需要启动 AutoCAD，也不证明原生实时联动。
