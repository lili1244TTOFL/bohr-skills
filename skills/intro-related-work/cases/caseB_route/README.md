# 示例夹具 B：规范稿（示例数据，虚构条目，仅用于演示接口）

这组夹具是"改好的稿"该有的样子，用来对照 A 组：

- `draft.md`：四部分齐全；相关工作按原文维度词（「基于平行语料」「基于伪标签」）分组比较，每组写了结论、适用条件、局限；空白由相关工作记录的局限推出；贡献逐条挂 `Section 3` / `Table 2` / `Fig. 4`
- `refs.bib`：4 条虚构条目，author/year/title/journal 齐全，全部被引用

调用：

```bash
python run.py --input cases/caseB_route --work work --out output
```

期望：`status=success`；`空白推导` 至少 1 条判"成立"（`stats.n_gap_derived>=1`）；
`改稿残留` 为空或只剩需人工核对项。对这组夹具跑 `revised.md`，能直接看到"分组 → 比较 → 空白 →
贡献"四段承接，且每段论断都带引用。
