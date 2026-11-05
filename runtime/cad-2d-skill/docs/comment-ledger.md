# 批注台账与跨轮次检查

使用场景：连续多轮批注、要求保留历史修改，或需要逐条给出目标、条件和对照图。
台账是任务文件，不写入全局 skill，也不自动推断工程尺寸。初次绑定应核对当前 CAD
及批注来源，用 `inventory` 获取文件哈希、单位和对象选择器。

## 命令

```text
python -m cad_skill_core check-comments BEFORE.dxf AFTER.dxf LEDGER.json --out NEW_REVIEW --json
python -m cad_skill_core revise BASELINE.dxf PLAN.json --ledger LEDGER.json --out NEW_REVISION --json
```

`check-comments` 只读 CAD，逐条核对并输出 HTML、JSON、同尺度图片和下一轮台账。
`revise --ledger` 在发布前重查全部有效条件，任何机器条件失败都会阻止新修订发布。
`--dry-run` 也检查台账，但不生成文件。输出目录沿用新建/严格匹配复用规则。

从本工具生成的 `drawing.dxf` 继续修订，若旁边有带台账的 `revision-report.json`，
会自动读取同目录的 `comment-ledger.json`。漏写 `--ledger` 不会跳过旧要求。
该图纸或台账已被外部改动时先核对并重新建立基准，不能仅更改哈希来强行通过。
把 DWG/DXF 单独复制到其他位置时，应同时带上相应台账并显式指定 `--ledger`。

## 格式

下面是测试用例的结构；数值不是实际图纸设计要求，哈希与句柄必须来自当前文件。

```json
{
  "schema": "cad-comment-ledger/v1",
  "drawing_sha256": "替换为当前DXF的SHA256",
  "units": "mm",
  "tolerance": 0.000001,
  "padding": 5,
  "comments": [
    {
      "id": "C1",
      "text": "保留用户批注原文",
      "source": {"message": "批注来源", "page": 1},
      "targets": [{"handle": "8A"}],
      "checks": [
        {"type": "set_length", "target": {"handle": "8A"}, "fixed": "start", "length": 10},
        {"type": "property", "target": {"handle": "8A"}, "attribute": "start", "value": [0, 0, 0]}
      ],
      "manual_checks": ["查看局部线宽与样图是否一致"]
    }
  ]
}
```

- 每条必须有唯一 ID、原文、来源、目标，以及机器检查或人工检查。最多 100 条，更多时分批。
- `checks` 可复用已有 `align`、`contact`、`resize_fixed_edge`、`set_length` 和原生标注
  的检查字段，详见 [修订操作接口](revision-tools.md)。检查不会执行对应编辑。
- `present` / `absent` 使用 `target` 选择器核对对象存在/消失；`present` 可加 `entity_type`。
  删除文字和引线时分别列出需要消失的目标，不能只检查文字。
- `property` 核对一个具体属性和值，支持起终点、圆心、半径、文字、插入点、图层、
  文字位置、起止角、线型、线宽、颜色。固定边/端点要求必须列出相应不变条件；
  仅长度检查不代表位置也固定。
- `targets` 用于定位和生成对照图；实际验收由 `checks` 决定，不把包围框内所有对象当作修改目标。
- 可选 `bounds: [xmin, ymin, xmax, ymax]` 是 CAD 绘图坐标；PDF 页面坐标必须先完成版本对应与坐标转换。
- 容差、裁切扩展量按绘图单位确定。`history` 由工具保留版本哈希关系，不是人工通过证明。

## 下一轮与取代关系

新修订目录里的 `comment-ledger.json` 已绑定新 DXF 的哈希。追加新批注即可继续使用。
已有记录不能静默删除或改写；要求发生变化时追加新 ID，用 `supersedes: ["C1"]`
明确取代旧条目。旧记录仍留在台账，报告显示为 `superseded`。

分段或转换使句柄改变时不能猜对象对应。根据实际分段映射/新对象核对目标，再追加
说明原因的新记录并显式取代失效的旧选择器。未经核对的缺失/歧义目标不会判通过。
工具会比较目标的对象类型及已有 `CAD_SKILL_ID`，类型或已知稳定 ID 改变时阻止误验收。

局部 `replace_outline` 后再分段有专门的已核验映射：完全一致的替换条件可绑定工具生成的
`edit_evidence`，按当前输出哈希随台账续存，核对真实子段覆盖、属性和稳定 ID。
它不依据 ID 前缀猜对象。旧批注原文保留，后续缩短或改坏子段仍会失败。
操作字段和边界见 [常用局部修改](edit-operations.md)。
没有稳定 ID 的同类型句柄复用无法仅凭句柄证明身份；报告会保留这一边界，跨格式或
重建后的对象应重新确认对应关系。不要把句柄当作跨版本永久身份。

## 结果与证据

- `checks_passed`：所列机器条件成立；不是整张图纸最终验收。
- `needs_review`：机器条件成立，但列出的人工/原生交互检查仍待处理。
- `failed`：至少一个有效机器条件未满足，报告指出批注 ID；独立检查仍会保存失败证据。
- `superseded`：由后续明确条目取代，不再作为有效条件执行。

每条有效批注有独立对照区域和局部诊断候选，未修改的历史要求也会重新检查。
无法确定图像范围时报告缺失原因，需指定 `bounds`；不伪造图像证据。
`check-comments` 有失败条件时退出码 1，输入错误为 2；0 也可能仍有人工检查待完成。
原生标注夹点、几何关联、字体与最终打印效果仍需要相应实际验证。

## 圆与圆弧核对

局部检查支持 XY 平面的 LINE、CIRCLE、ARC、零宽 LWPOLYLINE（含 bulge），
以及由直线/圆弧构成的 HATCH 路径。使用解析交点和覆盖区间，检查重复圆弧、相切/端点
接触脱离、填充与圆弧轮廓脱离及近似文字包围框干涉；等效分段不应产生误报。
椭圆、样条、非平面/非默认法向及有宽度对象仍明确列为未覆盖。HATCH 的顺逆时针
存储按 [ezdxf ArcEdge 文档](https://ezdxf.readthedocs.io/en/stable/dxfentities/hatch.html) 解释。

可运行随技能附带的 [合成案例脚本](../examples/comment-ledger/make_demo.py)，传入一个新的
输出目录，查看旧条件阻止回退、明确取代后的第二轮结果以及圆形填充脱离的对照图。
