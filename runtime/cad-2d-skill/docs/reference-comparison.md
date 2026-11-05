# 样图参照：分开检查画法与工程数值

```text
python -m cad_skill_core compare-reference CURRENT.dxf SAMPLE.dxf MAP.json --out NEW_OUTPUT --json
```

输入两个明确的 DXF，先通过 `inventory` 核定真实句柄、单位和 SHA256。
工具只读源文件，生成 `reference-report.json`、`reference-review.html` 和配对 PNG。
两张局部图扩展至相同宽高、以相同绘图单位比例显示，不做几何缩放或修改。
区域使用 CAD 的 XY 坐标，不是 PDF 像素。既有输出目录不会被覆盖。

映射格式如下；哈希、句柄、区域和数值须替换为任务中实际核定的值：

```json
{
  "schema": "cad-reference-map/v1",
  "units": "mm",
  "current_sha256": "CURRENT_SHA256",
  "reference_sha256": "SAMPLE_SHA256",
  "regions": [
    {"id":"slot", "mode":"depiction",
     "current_bounds":[0,0,40,80], "reference_bounds":[100,200,140,280]}
  ],
  "parameters": [
    {"id":"slot-depth", "current":{"handle":"2F"}, "reference":{"handle":"3A"},
     "measure":"length", "mode":"preserve_current", "current_expected":20, "tolerance":0.001}
  ]
}
```

- `regions` 至少一项，`mode` 只能为 `depiction`；图像相似性不等于几何要求通过。
- `parameters` 可为空；每项 `id` 唯一，选择器必须准确对应一个模型空间实体。
- `measure` 支持原生尺寸 `dimension`、直线 `length`、圆或弧的 `radius/diameter`。
  读数来自真实几何，不取标注的显示文字、缓存值或 DIMLFAC。支持边界同 `check-drawing-set`。
- `preserve_current` 必须明确 `current_expected`；样图值不同也不自动采用。
- `adopt_reference` 移除 `current_expected`，采用样图实测值作为应有值。
  只有带非空 `authorization` 字符串（记录用户已明确同意的内容或出处）才核验是否达标。
  无授权时返回 `needs_authorization`，工具仍不改图。不得为通过检查而编造授权记录。
- 本图、样图的 `$INSUNITS` 必须一致且与映射相符；未知单位不得推断为毫米。
- 可选 `font_maps` 形如 `{"current":{"Standard":"C:/Windows/Fonts/simsun.ttc"},
  "reference":{"Standard":"C:/Windows/Fonts/simsun.ttc"}}`。实际路径须存在，字体与字形要核验；
  它只影响审阅副本的显示，不改源 CAD 样式。

状态：`checked` 仅表示所有显式数值条件通过；`needs_update` 表示数值未同步；
`needs_authorization` 表示待确认几何采纳范围；仅比较画法时为 `needs_visual_review`。
所有报告的 `visual_review: required` 都保留，图像需实际查看。
CLI 返回 0 仅用于 `checked`，1 表示待处理结果，2 表示输入/来源无效。
字体无法确认、哈希或单位不匹配、选择器有歧义等会拒绝发布，不能视作通过。

Python 接口：

```python
from cad_skill_core.revision.reference import compare_reference
report = compare_reference(current_path, reference_path, mapping_path, output_dir)
```

输出是审阅证据，不证明原生 CAD 夹点操作、DWG 导出或完整工程设计正确。
