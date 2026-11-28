# 最小调用示例（示意输出，非正式案例结果）

## 输入

- `examples/paper.tex`：一段小稿，含 `\cite`、`\ref`、重复 `\label`、缺 caption 的图、缺图、子章节层级
- `examples/refs.bib`：两条参考文献（其中一条未被引用）

## 命令

```bash
# 平台形态：出 fixed.tex / fixes.md / report.md / result.json
python run.py --input examples --work work --out out

# 只做静态检查与修复，不尝试编译
python run.py --input examples --work work --out out --no-compile
```

## 示意输出（示例，取自真实运行）

```json
{"ok": true, "status": "success", "problems": 6, "fatal": 5,
 "fixes": 5, "residual": 2, "compiled": false, "gaps": 2}
```

产物：

- `out/fixed.tex`（351 字节）：按 F01–F06 规则修好的可编辑源码
- `out/fixes.md`：逐条修复记录（命中哪个 F 码、改了什么）
- `out/report.md`：原稿问题清单 + 修复记录 + 修复稿残留 + 编译状态
- `out/result.json`：目标产物 / 事实与来源映射 / 版本与边界 三字段 + `规则命中`

本机没有 `latexmk`/`pdflatex`，所以 `compiled=false`、`gaps` 里记 `no_tex_distribution`；这不影响 `status`——状态看的是 `fixed.tex` 是否写出、autofix 能修的码（T01/T02/T06）与结构性语法错（T07/T08/T11）在修复稿上是否清干净。示例稿修完只剩 T03/T05 两条 info。

示例仅演示接口，不是组织方案例结果。
