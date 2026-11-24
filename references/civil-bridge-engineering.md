# 土木与桥梁工程结构计算、跨图校核及自动出图指南（`civil` 套件）

本模块（`cad_skill_core.civil`）实现了**零商业软件依赖**（仅使用 Python 3.12 标准数学栈与 `ezdxf`）的公路桥梁结构数据处理、规范校核及施工图/计算附图全闭环生成，遵循《公路桥涵设计通用规范》（`JTG D60-2015`）与《公路钢筋混凝土及预应力混凝土桥涵设计规范》（`JTG 3362-2018`）。

---

## 1. 是否需要下载外部软件？

- **中小跨径梁桥（简支 T 梁 / 空心板 / 小箱梁 / 双柱盖梁 / 桩柱桥台）全套数据处理与出图：完全不需要下载任何外部商业软件！**
  - 内置引擎原生完成曼宁水文孔径计算、任意多边形截面几何特性精确积分（格林公式）、杠杆原理法与刚性横梁偏心压力法横向分布影响线、公路-Ⅰ/Ⅱ级车道荷载内插与冲击系数、10 等分单元（`L, Z1, B1..KZ..R`）弯矩/剪力包络与极限状态组合、预应力钢束估算与摩擦/锚具回缩损失表，以及基于欧拉-伯努利杆系刚度矩阵的 **1D 连续悬臂盖梁有限元求解器**。
- **复杂超静定特大桥（斜拉桥 / 悬索桥 / 大跨连续刚构十年收缩徐变施工阶段分析）**：
  - 如已有 **桥梁博士（Dr.Bridge）** 或 **Midas Civil** 导出的计算表格（`.csv` / `.tsv` / Markdown），无需重装软件，可直接调用 `bridge-calc --mode table-audit` 或 `cad_civil_bridge_calc(mode="table_audit")` 自动导入表格并核查是否存在**计算值超限却误标“满足/是”**或**量级单位笔误（`cm` vs `mm`）**。

---

## 2. 内置结构计算与跨图校核能力（`cad_civil_bridge_calc`）

| 模式 (`mode`) | 核心计算/校核内容 |
| :--- | :--- |
| `full` | 一键串联水文孔径 → T 梁截面特性 → 横向分布 → 10 单元内力组合包络 → 预应力估算与损失 → 盖梁 1D 有限元 → 跨图一致性校核 |
| `hydrology` | 水力半径 $R=A/\chi$、曼宁流速 $V=\frac{1}{n}R^{2/3}i^{1/2}$、设计流量 $Q$、通航净宽 $L_j$ 及跨径推荐 |
| `polygon_section` / `t_girder_section` | 任意多边形精确面积 $A$、形心、惯性矩 $I_x, I_y$、上下核心距 $K_s, K_x$、截面效率指标 $\rho=(K_s+K_x)/h \ge 0.5$ 及受压翼缘有效宽 $b'_f$ |
| `lateral_distribution` | 支点杠杆原理法 $m_{oq}$ 与跨中偏心压力法 $\eta_{ik} = \frac{1}{n} \pm \frac{a_i a_k}{\sum a_j^2} \to m_{cq}$ 竖标与最不利布载轮位 |
| `superstructure_envelopes` | 基频 $f$、冲击系数 $1+\mu$、车道荷载 $P_k, q_k$（剪力自动乘 `1.2`）及 11 个控制截面承载力/正常使用组合包络 |
| `prestress` | 正常使用抗裂与极限抗弯所需 `Φs15.2` 钢绞线根数、沿程管道摩擦损失 $\sigma_{l1}(x)$、锚具变形损失 $\sigma_{l2}(x)$、松弛与徐变损失及有效预应力表 |
| `cap_beam_1d` | 纯 Python 欧拉-伯努利 2-DOF/节点刚度矩阵求解双柱/多柱悬臂盖梁支反力、负弯矩峰值与剪力图 |
| `consistency_audit` / `table_audit` | 跨图校核单幅/全幅桥宽与护栏/分隔带闭合性、主梁间距与墩台支座垫石间距对齐、铺装层 `cm/mm` 10 倍笔误，以及外部电算表超限误判拦截 |

---

## 3. 五类土木与桥梁审计级 CAD 图纸（`cad_generate_civil_bridge`）

全部采用 `ARCHTICK` 建筑斜线标注箭头、显式毫米级工程线宽、圆柱桩 S 形折断符、三道斜线地面线、2% 单向横坡箭头、三角标高符 `▽`、圆圈墩台号与预应力 `[N1]` 方框引线：

1. `bridge_elevation`：**桥型总体布置图**（多跨简支梁、防撞护栏、桥台、双柱墩、钻孔桩、河床地形线、设计洪水位标高、圆圈墩台号、`cm` 跨径链）。
2. `t_girder_section`：**上部结构标准横断面及 T 梁一般构造图**（单幅全宽横断面 + 双层铺装 + 2% 横坡 + 跨中马蹄形截面与支点加厚腹板截面大样 + 截面特性校核表）。
3. `tendon_layout`：**T 梁预应力钢束构造及布置图**（跨中波纹管断面 + 梁端锚固断面 + 半跨平弯/竖弯抛物线纵断面线形 + `[N1]~[N5]` 编号框）。
4. `pier_and_abutment`：**双柱式桥墩 / 桩柱式桥台一般构造及支座布置图**（盖梁/台帽、防震挡块、支座垫石、系梁、耳背墙、地面线、圆柱桩 S 形折断符，以及 `Z1` 固定端 / `Z2` 活动端支座平面布置图）。
5. `mechanics_diagrams`：**主梁荷载横向影响线及 10 单元内力组合包络图**（偏心压力法影响线与最不利轮载箭头、`Pk+qk` 车道荷载与弯矩影响线、`L..KZ..R` 10 等分单元弯矩/剪力包络图）。

---

## 4. CLI 快速调用示例

```powershell
$env:PYTHONPATH="D:\CAD-Workspace\cad-operations-skill\runtime\cad-2d-skill\src"

# 1. 一键运行全桥水文、截面、横向分布、内力包络、预应力、盖梁有限元与一致性校核
python -m cad_skill_core.cli bridge-calc --mode full --out output/bridge_calc_report.json

# 2. 生成全套桥梁 CAD 工程图纸（DXF + 高清 PNG + 100 分制审图报告）
python -m cad_skill_core.cli generate-civil bridge_elevation --out output/bridge_sheets/elev
python -m cad_skill_core.cli generate-civil t_girder_section --out output/bridge_sheets/girder
python -m cad_skill_core.cli generate-civil tendon_layout --out output/bridge_sheets/tendon
python -m cad_skill_core.cli generate-civil pier_and_abutment --out output/bridge_sheets/pier
python -m cad_skill_core.cli generate-civil mechanics_diagrams --out output/bridge_sheets/mech
```
