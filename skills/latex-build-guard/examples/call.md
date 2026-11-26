# 最小调用示例（示意输出，非正式案例结果）

## 输入

- `examples/paper.tex`：一段含公式、表格、`\cite` 的小稿
- `examples/refs.bib`：两条参考文献
- `/input/case.json`：`case_id` 与 `output_contract`

## 命令

```bash
# 只查不编译
python run.py --input examples --out out

# 查完并尝试编译（本机无 TeX 时自动跳过，状态写 skipped）
python run.py --input examples --out out --build
```

## 示意输出（示例）

```json
{"status":"success","issues":3,"fixed":2,
 "checks":{"引用可解析":true,"公式编号连续":true,"表格列数一致":true}}
```

产物：`result.json`（目标产物/事实与来源映射/版本与边界）、`report.md`（逐条问题与修复）。

示例仅演示接口，不是组织方案例结果。
