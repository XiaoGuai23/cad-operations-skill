# CAD Operations Skill

面向 Codex 的 CAD 工作技能，包含机械图纸局部修订、可移动原生标注、交点分段、几何约束核验，以及电气主接线图、路面结构图和本地 CAD 操作指引。

## 能做什么

| 工作 | 工具与行为 |
| --- | --- |
| 按批注修改现有图纸 | 绑定基线文件哈希，只修改指定对象；保留原件与修订证据 |
| 平齐、接触、固定边延伸 | 用可检查的几何条件表达要求并核验保存结果 |
| 可移动标注 | 保留原生 DIMENSION / MULTILEADER，支持移动、文字模式调整和尺寸重算 |
| 交点分段 | 对支持的几何对象按指定范围分段，保留剖面线和标注语义 |
| 多轮批注 | 台账记录有效要求，后续修订自动重新核验 |
| 关联图纸与样图 | 检查明确的跨图参数；等比例对照，区分画法参考与获授权的尺寸变更 |
| 局部修改复核 | 比较前后图纸，检查邻域缝隙、重叠、剖面边界和文字碰撞候选 |
| 预览 | 复用一次绘制生成局部 PNG 和矢量审阅 PDF；核验字体、字形和遮罩 |
| 其他 CAD 工作 | 电气主接线图与路面结构图生成；AutoCAD、FreeCAD、SolidWorks 操作指引 |

## 安装

机械修订运行工具要求 **Python 3.12**。下列命令以 Windows PowerShell 为例；安装位置已有同名 skill 时，先备份或在另一目录比较更新。

```powershell
$skillPath = Join-Path $env:USERPROFILE '.codex/skills/cad-operations'
git clone https://github.com/XiaoGuai23/cad-operations-skill.git $skillPath
Set-Location -LiteralPath $skillPath
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -e './runtime/cad-2d-skill[dev]'
```

在 Codex 中刷新技能或重启会话。命令行示例中的 `python` 应使用上述虚拟环境的解释器；可以先运行 `.\.venv\Scripts\Activate.ps1`，或直接使用解释器完整路径。保留 `runtime/cad-2d-skill` 的完整目录，其中的 `knowledge/` 和 `templates/` 也参与运行。

根目录是主 skill；`companion-skills/cad-2d-geometry/` 是可选的机械几何专用入口，依赖本仓库主 skill 和同一运行环境。需要时将其复制为技能目录下的独立 `cad-2d-geometry` 文件夹。

## 快速试用

以下示例生成合成测试图，不使用任何真实工程图纸：

```powershell
python -m cad_skill_core operations --json
python examples/revision-tools/make_example.py output/revision-demo
python -m cad_skill_core inventory output/revision-demo/baseline.dxf --json
python -m cad_skill_core revise output/revision-demo/baseline.dxf output/revision-demo/plan.json --out output/revision-demo/revised --dry-run --json
python -m cad_skill_core revise output/revision-demo/baseline.dxf output/revision-demo/plan.json --out output/revision-demo/revised --json
```

执行修订前阅读[修订工具说明](references/revision-tools.md)和[字段/API 合约](references/revision-tools-api.md)。`--out` 使用尚不存在的新目录。

其他入口：

```powershell
python -m cad_skill_core doctor --cache output/cad-environment.json --json
python -m cad_skill_core preview drawing.dxf --out output/review --pdf --json
python scripts/make_main_sld.py examples/electrical_sld_spec.json --outdir output/sld --stem demo_sld
python scripts/generate_pavement_cad_ezdxf.py examples/pavement_structure_spec.json --out output/pavement
```

## 验证

```powershell
python tests/smoke_test.py
Push-Location runtime/cad-2d-skill
python -m pytest tests -q -m 'not autocad'
Pop-Location
```

运行工具测试使用合成输入；原生 AutoCAD 探测测试默认在上述命令中排除。GitHub Actions 执行相同的公开包检查。测试通过不等于工程设计验收，也不代表原生软件中的夹点行为已验证。

中文整图测试需要字体覆盖中文及 `⌀` 等工程符号。字体策略位于 `runtime/cad-2d-skill/knowledge/drawing-rules/font-policy.yaml`。GitHub Actions 在临时运行环境中下载并校验 [GNU Unifont 16.0.04](https://unifoundry.com/pub/unifont/unifont-16.0.04/font-builds/)，使用专用测试字体策略；它不会修改仓库默认策略或你电脑上的字体。Unifont 使用其自身的[字体许可证](https://www.unifoundry.com/unifont/)，不包含在本仓库文件中。

## 目录

- `SKILL.md`：按任务读取所需说明的入口。
- `references/`：修订设计方法、标注、分段、批注台账、关联图、预览和本地 CAD 指引。
- `runtime/cad-2d-skill/`：完整 Python 运行工具、模板、规则、合成示例和测试。
- `scripts/`、`examples/`、`tests/`：电气/路面生成器及快速验证。
- `companion-skills/cad-2d-geometry/`：可选机械几何技能。

## 支持范围

修订工具直接编辑 DXF modelspace。DWG 读写、原生出图和交互验证需要可用的 CAD 软件；仓库不包含商业 CAD 软件或字体。共享预览生成的是审阅 PDF。需要原生 DWG、打印精度或联动夹点行为时，仍须在目标 CAD 中单独核验。

运行工具内的受信任 AutoCAD 执行器目前针对 **AutoCAD 2023 / R24.2**；通过 `AUTOCAD_EXE` 和 `ACCORECONSOLE_EXE` 指定相应可执行文件。其他版本可参考[本地 CAD 指引](references/local-cad-environment.md)，不作为该执行器已验证的支持范围。

## 来源与许可证

原始 CAD Operations 技能来自 [2836048681/cad-operations-skill](https://github.com/2836048681/cad-operations-skill)，本仓库保留原有 [MIT 许可证](LICENSE)，并加入机械图纸修订与核验工具。运行工具中改编自 ezdxf 的代码及版权说明见 [THIRD_PARTY_NOTICES.md](runtime/cad-2d-skill/THIRD_PARTY_NOTICES.md)。
