# 共享预览、字体核验与审阅 PDF

```text
python -m cad_skill_core preview SOURCE.dxf --out NEW_DIR --pdf --json
python -m cad_skill_core preview SOURCE.dxf --out NEW_DIR --box 0 0 80 60 --box 100 0 180 60 --font-map FONTS.json --cache CACHE_DIR --json
```

`--box` 可重复，使用绘图 XY 坐标；不指定时自动取有内容的全图范围。
每次先将图纸绘制到一份记录，再重放生成所有局部图，避免每个区域重复绘制整图。
PNG 固定 1120×840，所有区域采用相同的像素/绘图单位比例；区域宽高不同时留白调整。
最多 100 个区域，边界须有限、正面积，绝对坐标不超过 1e12，最短边至少 1e-9 绘图单位。

输出包括 `preview-report.json`、各区域 PNG；`--pdf` 额外生成独立矢量审阅 PDF。
PDF 每页对应一个区域，使用相同记录中的矢量，不把 PNG 嵌入后冒充矢量 PDF。
裁切边界上的部分曲线会按不超过 0.1 输出像素的精度近似；它是审阅产物，不能替代
原生 CAD 出图、工程几何或打印精度验收。源 DXF、图层、文字样式与元数据均保持不变。

## 字体和遮罩

`FONTS.json` 是 DXF 样式名到实际字体文件的映射，例如：

```json
{"Standard":"C:/Windows/Fonts/simsun.ttc","DIM":"C:/Windows/Fonts/simsun.ttc"}
```

样式名须存在，路径须指向实际 TTF/OTF/TTC 文件。未指定映射时按原有字体精确查找；
SHX、缺失字体、缺字或无法确认的内嵌字体会明确失败，不静默替换成 Arial。
映射只用于内存中的审阅副本，不修改 CAD。内嵌 MTEXT 字体仍按它自己的族、粗体/斜体
检查；普通样式映射不会删除这些覆盖。报告记录原字体、实际文件路径、SHA256、实际后端
路径和字形覆盖。白色 MTEXT 遮罩保留为白色，不因单色处理变成黑块。

## 缓存和结果

`--cache` 是独立缓存目录。只有源文件哈希、区域、导出参数、渲染器版本、字体文件
内容及全部产物哈希一致才复用。旧缓存损坏、被修改或有链接跳转时重新绘制。
不递归删除或覆盖异常缓存；新输出目录必须不存在。报告的 `cache_hit` 和
`drawing_pass_count` 分别说明是否复用、实际绘制次数（新绘制 1，复用 0）。

成功状态为 `rendered`，仅表示这些审阅图已生成并做了上述检查；
`manual_visual_review_required: true`、`native_interaction_tested: false`、
`native_dwg_tested: false` 保留。还须实际查看 PNG/PDF；不能据此声称 DWG 或夹点测试通过。

## Python 与现有核对工具

```python
from cad_skill_core.revision.preview import preview_file, render_regions
report = preview_file(source, output_dir, regions=None, font_map=None, pdf=False, cache=None)
report = render_regions(doc, [{"id":"slot", "bounds":[0,0,80,60]}], output, "current", font_map=None)
```

`render_regions` 也接受纯边界列表，返回按输入顺序排列的文件名 `images`、比例 `views`、
`font_evidence` 等；`label` 只能为 1–64 个 ASCII 字母、数字、下划线或连字符。
它用于调用方暂存目录；文件级 `preview_file` 负责原子发布。字体或输入检查失败抛
`RevisionError`，文件级工具不发布半成品。

现有 `review_files(..., font_map=...)` 和 `_render` 已使用同一渲染器，并在 review 报告中
增加 `render_evidence`；原有几何候选状态不变。`compare-reference` 通过相同宽高的配对
边界保持本图/样图比例一致，具体映射见 [样图对照](reference-comparison.md)。
