# 常用局部修改操作

这些操作使用原有 `cad-revision/v1` 计划，由 `revise` 在副本上执行、保存重开核验。
先用 `inventory` 获取真实源文件 SHA256、单位和句柄；用 `operations --json` 查看支持范围。
选择器只能是 `{"handle":"2F"}` 或 `{"id":"stable.id"}`，必须唯一。

## 删除明确选中的实体

```json
{"id":"remove-dimension", "type":"delete_entities", "targets":[{"handle":"2F"}]}
```

支持 LINE、ARC、CIRCLE、LWPOLYLINE、POINT、TEXT、MTEXT、原生 DIMENSION 和 MULTILEADER。
删除 MULTILEADER 时其自带文字、箭头和引线作为同一原生对象移除。
原生尺寸连同私有匿名显示块一起删除，邻近轮廓不变；显示块被未选对象共享或含自定义
记录时先拒绝。普通文字和独立引线需要分别明确选中，不把截图框内所有对象当作删除目标。
不支持的实体或无法保留的关联数据明确拒绝；不拆散对象来绕过检查。

## 替换局部轮廓

```json
{
  "id":"replace-slot-edge", "type":"replace_outline", "targets":[{"handle":"30"}],
  "replacements":[
    {"id":"slot.edge.new", "source":{"handle":"30"}, "type":"LINE", "start":[0,0], "end":[20,0]}
  ]
}
```

`replacements` 每项需要新的唯一稳定 `id`、所选源 `source`、明确 `type` 和几何：

| 类型 | 几何字段 |
| --- | --- |
| LINE | `start:[x,y]`, `end:[x,y]` |
| ARC | `center:[x,y]`, `radius`, `start_angle`, `end_angle`（度） |
| CIRCLE | `center:[x,y]`, `radius` |
| CIRCULAR | `center:[x,y]`, `radius`, `start_angle`, `sweep`（度，0 < sweep ≤ 360；360 为整圆） |

源限可表示的平面 LINE/ARC/CIRCLE；新轮廓规约还可用 CIRCULAR；所有数字须有限，长度/半径超过容差，
圆弧不得为零跨度或完整圆；`CIRCULAR` 的 sweep 为 360 时落盘为 CIRCLE，小于 360 时落盘为 ARC。每个源至少对应一个新对象，新 ID 不得复用已有 ID。
保留图层、颜色、线型、线宽和可安全复制的 XDATA；无法复制的属性、句柄关联、扩展
字典、反应器或代理数据先拒绝，不静默丢弃。工具不会自动推断受影响的剖面填充。

报告的 `mapping` 是原始替换映射，`final_mapping` 是后续分段后的最终对象映射。
以真实曲线覆盖核验，不能用线段总长相同代替位置正确。

## 显式尺寸驱动图形

```json
{"id": "drive-hole-dia", "type": "dimension_drive", "target": {"handle": "31"},
 "mode": "diameter", "value": 16, "geometry": [{"handle": "2A"}]}
```

`mode` 为 `diameter`（直径标注 + 一个 CIRCLE，半径改为 value/2，圆心不动）、
`radius`（半径标注 + 一个 CIRCLE 或 ARC，半径改为 value，圆心不动）或
`linear`（线性/对齐标注 + 一条端点即标注两端点的 LINE，长度改为 value，
`fixed` 指定不动的 `p1` 或 `p2`）。执行前要求标注当前实测值与被驱动图形一致、
图形圆心/端点与标注原点重合、标注文字为实测文字 `<>`（人工覆盖文字先拒绝）；
执行后保存重开核验标注实测值、图形半径/线长与原生显示缓存。反向是
`dimension_remeasure`：改图形后重测标注。联动是显式操作加核验，不是在 DXF 里
造原生关联；AutoCAD 里的夹点联动须另行实测。详见
[圆弧、复合轮廓与显式尺寸同步](circular-and-dimension-link.md)。

该操作不会调整相邻直线、其他标注或填充。圆弧半径改变后，应复核端点及相切关系；
不能把独立圆弧驱动当作整组约束求解。

## 重新测量原生尺寸

```json
{"id":"sync-depth", "type":"dimension_remeasure", "target":{"handle":"31"}, "p1":[0,0], "p2":[20,0]}
```

上述端点形式支持 XY 平面原生线性/对齐 DIMENSION，十进制且未启用换算单位。线性尺寸保留角度，
对齐尺寸根据新端点更新方向。重建其原生显示内容，保留文字、前后缀、文字覆盖、样式、
公差和文字移动模式。显示文字如果是人工覆盖数字会原样保留；需要改文字时另行核定，
不要把数量/公差中的第一个数字替换为测量值。

新端点必须对应已核定的测量对象，本操作不建立几何关联。斜伸线、手工定位尺寸文字、
非支持类型或复杂附加数据仍按原标注工具边界拒绝。保存重开后检查实际测量量、端点、
语义和原生显示缓存；后续改变显示文字为旧尺寸不能通过缓存核验。

半径或直径标注增加从当前几何重测的形式：

```json
{"id":"sync-hole","type":"dimension_remeasure","target":{"handle":"32"},
 "mode":"diameter","geometry":[{"handle":"2A"}]}
```

`diameter` 只接受 CIRCLE；`radius` 接受 CIRCLE 或 ARC。必须选择一个明确几何，
不可同时传 `p1/p2`。允许半径变化，圆心仍须与旧标注一致；圆弧附着方向须位于当前
跨度内。圆心移动或附着不符时拒绝。此形式要求 `<>` 或空默认实测文字，与上述线性
重测保留文字覆盖的策略不同。保存重开核验数值、圆心/附着点、跨度和身份。

新增的 `contour` 是绘图规约的闭合多段线输入，不是 `replace_outline` 新类型；
圆角矩形函数也不是 `revise` 操作。用法及边界见
[圆弧、复合轮廓与显式尺寸同步](circular-and-dimension-link.md)。

## 批注、分段和续作

上述操作均可作为 `checks` 或批注台账中的条件。替换之后接 `split_intersections` 时，
分段必须是计划最后一项。工具组合已验证的源到子段映射，核验新轮廓覆盖、属性和稳定 ID。
与已执行替换完全相同的原始检查条件会绑定这份证据，不能根据 ID 前缀猜子对象。

台账按结果文件哈希保存 `edit_evidence`，下一轮继续核验；子段缩短、改错属性或句柄对应
错误 ID 都会失败。该字段由工具生成，用户不应手工编造为“通过”证据。原批注保持原文，
确有新要求取代时仍用 `supersedes`。

原有平齐、接触、固定边延伸、标注移动等操作见 [修订工具](revision-tools.md)。
每轮发布 DXF 与证据 JSON；预览用 [共享导出](preview-tools.md)。原生 DWG、夹点交互和
整图工程验收须另行验证。
