# 示例夹具 A：病稿（示例数据，虚构条目，仅用于演示接口）

本目录是一组**合成的**输入夹具，用来演示 Skill 对"病稿"的诊断与改写，不是组织方固定案例，也不冒充真实文献。

- `draft.md`：四部分齐全但每部分都有典型毛病——背景是空转套话、相关工作逐篇罗列、空白段无引用、贡献条目缺图表指向且有一条"首次"式绝对化表述
- `refs.bib`：5 条虚构条目，其中 1 条未被引用、2 条缺来源（期刊/会议）

调用：

```bash
python run.py --input cases/caseA_broken --work work --out output --case-id caseA
```

期望：`status=success`（C01/C02/C03/C05/C07 在改稿上全过），`改稿残留` 只剩要作者补材料的
C04/C06/C08/C09/C10/C11。逐条依据见 `output/revised.md` 的 §A–§G。
