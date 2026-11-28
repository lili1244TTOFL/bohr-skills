# 最小调用示例（示例，非正式案例结果）

## 输入

- `examples/draft.md`：四段病稿，含空转套话、逐篇罗列的相关工作、无引用的空白段、无证据的贡献
- `examples/refs.bib`：4 条，其中 1 条未被引用、1 条缺 year

## 命令

```bash
python run.py --input examples --work work --out output --case-id intro-A
```

## 示意输出（示例，取自这次运行，不是组织方固定案例结果）

```json
{"ok": true, "status": "success", "problems": 6,
 "codes": ["C02","C04","C05","C06","C07","C08"],
 "revised": "output/revised.md", "bytes": 2979,
 "n_groups": 1, "n_group_sentences": 3, "n_despin": 1,
 "n_cites_added": 1, "n_evidence_added": 0, "n_pending": 2}
```

产物：

- `output/outline.md`：四段骨架（442 字节）
- `output/revised.md`：改稿（2 979 字节）——套话分句已删（该句引用抬成「相关研究 [@…]」保留）、相关工作按原文维度分组并写明"N 组"、缺引论断补引、贡献补证据指向，逐条依据写在文末 §A–§F
- `output/citations.json`：引用双向差集（155 字节）
- `output/result.json`：三字段 + `改稿残留` + `规则命中` + `output_contract`

`status=success` 的判定看的是 `revised.md` 正文：C01/C02/C03/C05/C07 在改稿上全过。`result.json.checks` 里的 6 条是**输入稿的诊断**；其中 C04（bib 里 `zhao2022` 未被引）、C06（第 1 条贡献原文没有 Fig./Table/Section 可挂）、C08（`wang2021` 缺 title）属于"要作者补材料"，只进 `gaps` 与 `改稿残留`，工具不编编号、不编条目字段。

这份示例稿是"逐篇罗列"型病稿，原文没有"基于 X"式维度词，所以 `n_groups=1`（按原文动词「提出」归一组），`revised.md` 的 §D 第 1 条会写明"需作者补「基于/采用 X」式表述"——这是有意留白，不是分组失败。

示例仅演示接口，不是组织方固定案例结果。
