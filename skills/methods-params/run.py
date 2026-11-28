#!/usr/bin/env python3
"""run.py —— 方法与实验章节：从配置/日志/代码/说明抽取要素，与正文对齐，报缺口并追问。

模块按"输入 → 判定 → 产出"切，不按执行顺序切：
    discover()          找到材料并判类型（配置/日志/代码/说明/正文）
    extract_items()     按规则表抽六类要素，每条带 file:line 与来源类型
    extract_steps()     步骤表：操作 + 输入/输出/判断条件，逐条回指材料行
    align()             与正文对齐：一致 / 正文漏写 / 正文与材料不符 / 无来源数值
    find_conflicts()    同一参数在多份材料里取值不同 → 冲突，不静默取一个
    derive_limitations() 由"检测到的事实"生成失效场景与适用范围，不用套话
    build_questions()   每个缺口变成一条可执行追问（问谁、问什么、影响哪节）
    render_*()          章节成稿 + 参数表 + 复现清单 + 追问清单 + 结果 JSON

设计依据：题面要求"从代码、配置、日志、数据说明抽取要素—对齐正文—发现缺口并追问"，
六类要素（步骤/实验设置/参数/数据集/对照/复现与限制）逐类盘点，抽不到就记缺口，
不从段落倒推数值。零模型请求、零外部依赖，全部本地正则与字符串比较。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

VERSION = "2.0.0"

SIX = ["步骤", "实验设置", "参数", "数据集", "对照", "复现与限制"]

# 来源类型：题面要求来源写成"配置文件、日志或作者确认"，这里做映射。
KIND_SOURCE = {"配置": "配置文件", "日志": "日志", "代码": "代码",
               "说明": "说明", "正文": "正文", "其他": "其他"}

# 单位：按名称给默认单位，材料里写了别的单位不覆盖，只在有明确映射时填。
UNIT = {"学习率": "无量纲", "批大小": "样本/步", "训练轮数": "轮", "随机种子": "无量纲",
        "权重衰减": "无量纲", "预热步数": "步", "丢弃率": "比例", "序列长度": "token",
        "梯度裁剪": "范数", "重复次数": "次", "评测频率": "步", "数据集规模": "条",
        "子集规模": "条", "指标值": "按指标", "硬件": "台/张", "软件版本": "版本号"}

# 同一名称最多保留几条不同写法：统计口径是"一个设置"，多种措辞不该重复成行
MAX_PER_NAME = {"统计方式": 1, "运行环境": 1}

# 正文对齐时，参数名在正文里的常见写法（用于发现"正文数值与材料不符"）
ALIAS = {
    "学习率": ["学习率", "learning rate", "lr"],
    "批大小": ["批大小", "batch size", "batch_size", "bs"],
    "训练轮数": ["训练轮数", "epochs", "epoch"],
    "随机种子": ["随机种子", "seed"],
    "权重衰减": ["权重衰减", "weight decay", "wd"],
    "预热步数": ["warmup", "预热"],
    "丢弃率": ["dropout", "丢弃率"],
    "序列长度": ["序列长度", "max_len", "max_length", "seq_len"],
    "梯度裁剪": ["梯度裁剪", "grad_clip", "clip_grad"],
    "重复次数": ["重复", "runs", "repeat"],
}


def _r(要素, 名称, re_, 对象="-", 单位="", line=None, 后缀=None):
    """一条抽取规则。v 命名组为取值；o 命名组为作用对象；d 组拼成 `<数量>x<型号>`。"""
    return {"要素": 要素, "名称": 名称, "对象": 对象,
            "单位": 单位 or UNIT.get(名称, ""),
            "re": re.compile(re_, re.M),
            "line": re.compile(line, re.M) if line else None,
            "后缀": 后缀}


# ---------------------------------------------------------------------------
# 要素规则表：只认显式写法（`lr: 0.001`、`baseline: BERT-base`、`python train.py`）。
# 抽不到就记缺口，不倒推数值——题面明令禁止"为段落顺畅补数值"。
# ---------------------------------------------------------------------------
RULES = [
    # ---- 参数：名称 / 取值 / 单位 / 作用对象 / 来源
    _r("参数", "学习率", r"(?im)^[ \t\-*]*(?:lr|learning[_-]?rate|optim\.lr|base[_-]?lr)[ \t]*[:=][ \t]*(?P<v>[0-9.eE+\-]+)", "优化器"),
    _r("参数", "学习率", r"(?i)学习率[ \t]*(?:为|是|设为|设置为|取|[:：=])[ \t]*(?P<v>[0-9.]+(?:[eE][+-]?\d+)?)", "优化器"),
    _r("参数", "批大小", r"(?im)^[ \t\-*]*(?:batch[_-]?size|bs)[ \t]*[:=][ \t]*(?P<v>\d+)", "数据加载"),
    _r("参数", "批大小", r"(?i)批(?:大小|尺寸)[ \t]*(?:为|是|设为|[:：=])[ \t]*(?P<v>\d+)", "数据加载"),
    _r("参数", "训练轮数", r"(?im)^[ \t\-*]*(?:epochs|max[_-]?epochs|num[_-]?epochs|n[_-]?epochs)[ \t]*[:=][ \t]*(?P<v>\d+)", "训练"),
    _r("参数", "训练轮数", r"(?i)(?<![:\d.])(?P<v>\d+)[ \t]*个?[ \t]*epochs?\b", "训练"),
    _r("参数", "随机种子", r"(?im)^[ \t\-*]*(?:seed|random[_-]?seed|manual[_-]?seed|random_state)[ \t]*[:=][ \t]*(?P<v>\d+)", "全局"),
    _r("参数", "随机种子", r"(?i)manual_seed\([ \t]*(?P<v>\d+)", "全局"),
    _r("参数", "随机种子", r"(?i)随机种子[ \t]*(?:为|是|设为|[:：=])[ \t]*(?P<v>\d+)", "全局"),
    _r("参数", "权重衰减", r"(?im)^[ \t\-*]*(?:weight[_-]?decay|wd)[ \t]*[:=][ \t]*(?P<v>[0-9.eE+\-]+)", "优化器"),
    _r("参数", "权重衰减", r"(?i)权重衰减[ \t]*(?:为|是|[:：=])[ \t]*(?P<v>[0-9.]+)", "优化器"),
    _r("参数", "优化器", r"(?im)^[ \t\-*]*(?:optimizer|optim\.type|optimizer[_-]?name)[ \t]*[:=][ \t]*(?P<v>[\w.\-]+)", "训练"),
    _r("参数", "优化器", r"(?i)优化器[ \t]*(?:为|是|采用|[:：=])[ \t]*(?P<v>[A-Za-z][\w.\-]*)", "训练"),
    _r("参数", "学习率调度", r"(?im)^[ \t\-*]*(?:lr[_-]?scheduler|scheduler|schedule)[ \t]*[:=][ \t]*(?P<v>[\w.\-]+)", "优化器"),
    _r("参数", "预热步数", r"(?im)^[ \t\-*]*(?P<o>warmup[_-]?(?:steps|ratio)|warmup)[ \t]*[:=][ \t]*(?P<v>[0-9.]+)", "调度器"),
    _r("参数", "丢弃率", r"(?im)^[ \t\-*]*(?:dropout|dropout[_-]?rate)[ \t]*[:=][ \t]*(?P<v>[0-9.]+)", "模型"),
    _r("参数", "序列长度", r"(?im)^[ \t\-*]*(?:max[_-]?len|max[_-]?length|seq[_-]?len|sequence[_-]?length)[ \t]*[:=][ \t]*(?P<v>\d+)", "数据"),
    _r("参数", "梯度裁剪", r"(?im)^[ \t\-*]*(?:grad[_-]?clip(?:ping)?|clip[_-]?grad[_-]?norm|max[_-]?grad[_-]?norm)[ \t]*[:=][ \t]*(?P<v>[0-9.]+)", "训练"),

    # ---- 实验设置：硬件 / 软件版本 / 重复次数 / 精度 / 评测频率 / 分布式
    _r("实验设置", "硬件", r"(?im)^[ \t\-*]*(?:hardware|machine|gpu[_-]?type|accelerator|device)[ \t]*[:=][ \t]*(?P<v>[^\n#]+)", "设备"),
    _r("实验设置", "硬件", r"(?i)(?P<v>\d+)[ \t]*[张块台][ \t]*(?P<d>[A-Za-z]\d{2,4}[A-Za-z0-9\-]*)", "设备", 后缀="x"),
    _r("实验设置", "软件版本", r"(?im)^[ \t\-*]*(?P<o>torch|pytorch|tensorflow|python|numpy|transformers|cuda|cudnn)[ \t]*(?:==|[:=])[ \t]*(?P<v>[\w.+\-]+)", "环境"),
    _r("实验设置", "软件版本", r"(?i)(?P<o>torch|pytorch|tensorflow|python|numpy|transformers|cuda)[ \t]+(?P<v>\d+\.\d+(?:\.\d+)?)", "环境"),
    _r("实验设置", "重复次数", r"(?im)^[ \t\-*]*(?:num[_-]?runs|repeats?|n[_-]?repeat)[ \t]*[:=][ \t]*(?P<v>\d+)", "统计"),
    _r("实验设置", "重复次数", r"(?i)(?P<v>\d+)[ \t]*次(?:独立)?(?:重复|运行)", "统计"),
    _r("实验设置", "精度", r"(?im)^[ \t\-*]*(?P<o>fp16|bf16|amp|mixed[_-]?precision)[ \t]*[:=][ \t]*(?P<v>true|false|1|0|yes|no)", "训练"),
    _r("实验设置", "评测频率", r"(?im)^[ \t\-*]*(?:eval[_-]?(?:every|interval|steps|freq))[ \t]*[:=][ \t]*(?P<v>\d+)", "评测"),
    _r("实验设置", "分布式策略", r"(?im)^[ \t\-*]*(?:distributed|strategy|ddp|deepspeed|parallel)[ \t]*[:=][ \t]*(?P<v>[\w.\-]+)", "训练"),

    # ---- 数据集：名称 / 版本 / 规模 / 预处理 / 划分比例 / 随机方式 / 子集
    _r("数据集", "数据集名", r"(?im)^[ \t\-*]*(?:dataset|data[_-]?name|corpus|benchmark)[ \t]*[:=][ \t]*(?P<v>[\w.\-/]+)", "数据"),
    _r("数据集", "数据集名", r"(?i)数据集[ \t]*(?:为|是|采用|名称[：:]?|[:：])[ \t]*(?P<v>[A-Za-z][\w.\-/]*)", "数据"),
    _r("数据集", "数据集版本", r"(?im)^[ \t\-*]*(?:data[_-]?)?version[ \t]*[:=][ \t]*(?P<v>[\w.\-]+)", "数据"),
    _r("数据集", "数据集版本", r"(?i)(?<!基线)(?<!代码)版本[ \t]*(?:为|是|[:：])[ \t]*(?P<v>v?[\d.]+)", "数据"),
    _r("数据集", "数据集版本", r"(?i)(?P<o>[A-Za-z][\w\-]{2,})[ \t]+v(?P<v>\d+(?:\.\d+)+)", "数据",
       line=r"数据|dataset|语料|版本"),
    _r("数据集", "数据集规模", r"(?im)^[ \t\-*]*(?:size|n[_-]?samples|num[_-]?samples|samples|num[_-]?examples)[ \t]*[:=][ \t]*(?P<v>[\d,]+)", "数据"),
    _r("数据集", "数据集规模", r"(?i)(?P<v>[\d,]{3,})[ \t]*(?:条|篇)[ \t]*(?:样本|数据|记录)?", "数据",
       line=r"数据|样本|语料|规模"),
    _r("数据集", "预处理", r"(?im)^[ \t\-*]*(?:preprocess(?:ing)?|tokeni[sz](?:er|ation)?|cleaning|normalization)[ \t]*[:=][ \t]*(?P<v>[^\n#]+)", "数据"),
    _r("数据集", "预处理", r"(?i)预处理[ \t]*(?:为|是|[:：])[ \t]*(?P<v>[^\n，。;；]+)", "数据"),
    _r("数据集", "划分比例", r"(?im)^[ \t\-*]*(?:split|split[_-]?ratio|ratio|splits)[ \t]*[:=][ \t]*(?P<v>[\d:/.]+)", "划分"),
    _r("数据集", "划分比例", r"(?i)(?:按[ \t]*([\d]+[:：/][\d:：/.]+)[ \t]*划分|划分(?:比例)?[ \t]*(?:为|是|[:：])[ \t]*([\d]+[:：/][\d:：/.]+))", "划分"),
    _r("数据集", "划分随机方式", r"(?im)^[ \t\-*]*(?:shuffle|stratified|stratify|split[_-]?method|random[_-]?split|sampling)[ \t]*[:=][ \t]*(?P<v>[\w.\-]+)", "划分"),
    _r("数据集", "划分随机方式", r"(?i)(?P<v>分层抽样|随机打乱|shuffle|stratified)", "划分",
       line=r"划分|split|数据"),
    _r("数据集", "子集规模", r"(?im)^[ \t\-*]*(?P<o>train|val|validation|test|dev)(?:[_-]?(?:set|split|size))?[ \t]*[:=][ \t]*(?P<v>[\d,]+)", "划分"),
    _r("数据集", "子集用途", r"(?i)(?P<o>训练|验证|测试|开发)集[ \t]*(?:仅|只)?(?:用于|用来|为|：|:)[ \t]*(?P<v>[^\n，。;；]+)", "划分"),

    # ---- 对照：基线对象 / 基线版本 / 对照条件 / 评价指标 / 指标值 / 统计方式
    _r("对照", "基线对象", r"(?im)^[ \t\-*]*(?:baseline|baseline[_-]?model|compare[_-]?to|compared[_-]?model)[ \t]*[:=][ \t]*(?P<v>[\w.\-/ ]+?)[ \t]*$", "基线"),
    _r("对照", "基线对象", r"(?i)(?:对照)?基线(?:模型|方法|系统)?[ \t]*(?:为|是|采用|选用|[:：=])[ \t]*(?P<v>[A-Za-z][\w.\-/]*)", "基线"),
    _r("对照", "基线对象", r"(?i)(?:与|和)[ \t]*(?P<v>[A-Za-z][\w.\-]{1,30})[ \t]*(?:进行)?(?:对比|比较)", "基线"),
    _r("对照", "基线版本", r"(?im)^[ \t\-*]*baseline[_-]?version[ \t]*[:=][ \t]*(?P<v>[\w.\-]+)", "基线"),
    _r("对照", "基线版本", r"(?i)基线(?:模型)?版本[ \t]*(?:为|是|[:：])[ \t]*(?P<v>v?[\w.\-]+)", "基线"),
    _r("对照", "对照条件", r"(?i)(?P<v>同(?:等)?预算|相同预算|同(?:等)?算力|同(?:等)?(?:训练)?(?:步数|token)|same[ \t]+budget|equal[ \t]+budget|same[ \t]+compute|同(?:一)?(?:数据(?:集)?划分|划分|超参(?:数)?|配置))", "条件"),
    _r("对照", "评价指标", r"(?im)^[ \t\-*]*(?:metrics?|evaluation[_-]?metric|eval[_-]?metric)[ \t]*[:=][ \t]*(?P<v>[\w, 、/\-]+)", "指标"),
    _r("对照", "评价指标", r"(?i)(?:评价指标|评测指标|评估指标|指标)[ \t]*(?:为|是|采用|[:：=])[ \t]*(?P<v>[^\n，。;；]+)", "指标"),
    _r("对照", "指标值", r"(?i)(?P<o>(?:val|valid|test|dev|eval)[_-]?(?:acc(?:uracy)?|f1|em|auc|bleu|rouge[\w\-]*|loss|precision|recall|ppl))[ \t]*[=:][ \t]*(?P<v>[0-9.]+)", "指标"),
    _r("对照", "统计方式", r"(?i)(?:(?P<v>\d+[ \t]*次(?:独立)?(?:重复|运行)?[ \t]*(?:取)?(?:平均|均值))|(?P<w>mean[ \t]*[±+\-][ \t]*std)|(?P<x>标准差|置信区间|显著性检验|t 检验|p[ \t]*[<>=][ \t]*0?\.\d+))", "统计"),

    # ---- 复现：代码版本 / 命令 / 数据获取
    _r("复现", "代码版本", r"(?im)^[ \t\-*]*(?:code[_-]?version|repo[_-]?version|git[_-]?(?:commit|rev|sha)|commit|revision|tag|release)[ \t]*[:=]?[ \t]*(?P<v>[0-9a-f]{7,40}|v?\d+\.\d+[\w.]*)", "代码"),
    _r("复现", "训练命令", r"(?im)^[ \t]*[$>][ \t]*(?P<v>\S.*)$", "入口"),
    _r("复现", "训练命令", r"(?im)^[ \t\-*]*(?P<v>(?:python3?|torchrun|accelerate|deepspeed|bash|sh|make)[ \t][^\n#]+)$", "入口"),
    _r("复现", "数据获取", r"(?P<v>https?://[^\s)（)，。\"']+)", "数据", line=r"数据|dataset|语料|下载|download|获取|corpus|huggingface"),
    _r("复现", "数据获取", r"(?i)(?P<v>(?:wget|git[ \t]+clone)[ \t]+\S+)", "数据"),
    _r("复现", "数据获取", r"(?im)^[ \t\-*]*(?:data[_-]?(?:path|root|dir)|dataset[_-]?path)[ \t]*[:=][ \t]*(?P<v>[^\n#]+)", "数据"),
]

DEP_FILES = ("requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml", "pipfile")

# 题面与元数据不是材料：task.md 里的 64/900/16384 是平台预算，case.json 只用于
# case_id 与 output_contract，都不能进抽取桶或正文桶。
SKIP_FILES = {"task.md", "case.json"}

# 中文"键：值"写法：README/材料说明里最常见的形态，逐键映射到六要素
CN_KEYS = [
    ("代码版本", "复现", "代码版本", "代码"),
    ("训练命令", "复现", "训练命令", "入口"),
    ("启动命令", "复现", "训练命令", "入口"),
    ("数据下载", "复现", "数据获取", "数据"),
    ("数据获取", "复现", "数据获取", "数据"),
    ("运行环境", "实验设置", "运行环境", "环境"),
    ("报告方式", "对照", "统计方式", "统计"),
    ("基线版本", "对照", "基线版本", "基线"),
    ("基线", "对照", "基线对象", "基线"),
    ("硬件", "实验设置", "硬件", "设备"),
    ("重复次数", "实验设置", "重复次数", "统计"),
    ("随机种子", "参数", "随机种子", "全局"),
    ("数据集版本", "数据集", "数据集版本", "数据"),
    ("数据集", "数据集", "数据集名", "数据"),
    ("划分比例", "数据集", "划分比例", "划分"),
]
RULES += [_r(要素, 名称, r"(?im)^[ \t\-*]*" + re.escape(k) + r"[ \t]*[：:][ \t]*(?P<v>[^\n]+)", 对象)
          for k, 要素, 名称, 对象 in CN_KEYS]

# 复现缺项要问什么（追问清单里用，避免"请补代码版本"这种没有动作的话）
REPRO_TIP = {"代码版本": "代码版本号或 git commit", "环境": "Python/框架/CUDA 版本",
             "训练命令": "启动命令（含配置文件路径）", "数据获取": "数据下载地址或本地路径",
             "随机种子": "seed 取值"}

# 步骤里"输入/输出/判断条件"的显式标注（材料若这样写，直接取原文，不做推断）
STEP_MARK = {
    "输入": re.compile(r"(?:输入|输入项|input)[ \t]*[:：=][ \t]*([^；;，,。）)」\n]+)", re.I),
    "输出": re.compile(r"(?:输出|产物|output)[ \t]*[:：=][ \t]*([^；;，,。）)」\n]+)", re.I),
    "判断条件": re.compile(r"(?:判断条件|判断|条件|停止条件|if)[ \t]*[:：=][ \t]*([^；;，,。）)」\n]+)", re.I),
}
ORDERED = re.compile(r"^[ \t]*(?:\(?\d+[).、]|[-*][ \t]*\[[ x]\])[ \t]*(.+)$")
STEP_HINT = re.compile(r"(?i)\b(start|begin|run|train\w*|finetun\w*|eval\w*|test\w*|save|export|"
                       r"load|preprocess\w*|tokeni[sz]\w*|done|finish\w*|launch)\b|"
                       r"准备|读取|加载|预处理|训练|微调|评估|评测|测试|导出|保存|完成|开始")
CLOCK = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?::\d{2})?")
KV = re.compile(r"(?<![\w.])([A-Za-z_][\w.]*)[ \t]*[=:][ \t]*([0-9][0-9.]*(?:[eE][+-]?\d+)?)")
NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\w.])")


# ---------------------------------------------------------------------------
# 读材料：只认 UTF-8 文本；类型按扩展名与文件名判，不靠目录名猜。
# ---------------------------------------------------------------------------
def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def classify(path: Path) -> str:
    name, ext = path.name.lower(), path.suffix.lower()
    if ext in {".yaml", ".yml"} or "config" in name:
        return "配置"
    if ext in {".log"} or "log" in name:
        return "日志"
    if ext == ".py":
        return "代码"
    if any(k in name for k in ("requirement", "environment", "readme", "usage", "install",
                               "material", "说明", "note", "guide")):
        return "说明"
    if ext in {".md", ".tex", ".txt"}:
        return "正文"
    if ext == ".json":
        return "配置"
    return "其他"


def discover(root: Path) -> dict[str, list[Path]]:
    buckets: dict[str, list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.name.lower() in SKIP_FILES:
            continue
        if p.resolve() == Path(__file__).resolve():
            continue
        buckets.setdefault(classify(p), []).append(p)
    return buckets


def line_of(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


def line_text(text: str, pos: int) -> str:
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    return text[a:] if b < 0 else text[a:b]


# ---------------------------------------------------------------------------
# 抽取：逐行扫规则表，命中即记一条，证据精确到 file:line，并记下命中原行。
# ---------------------------------------------------------------------------
def extract_items(files: list[Path], buckets_by_path: dict[str, str]) -> list[dict]:
    found: list[dict] = []
    for path in files:
        kind = buckets_by_path[str(path)]
        text = read(path)
        for rule in RULES:
            for m in rule["re"].finditer(text):
                gd = m.groupdict()
                val = gd.get("v")
                if not val:
                    for g in m.groups():
                        if g:
                            val = g
                            break
                if not val:
                    continue
                val = val.strip().rstrip("。；;，,")
                if not val or len(val) > 80:
                    continue
                ln = line_of(text, m.start())
                src_line = line_text(text, m.start())
                if rule["line"] and not rule["line"].search(src_line):
                    continue
                obj = (gd.get("o") or rule["对象"]).strip()
                if rule["名称"] == "软件版本":
                    obj = {"pytorch": "torch"}.get(obj.lower(), obj.lower())
                if rule["后缀"] and gd.get("d"):
                    val = f"{val}{rule['后缀']}{gd['d']}"
                if rule["名称"] == "硬件":
                    val = re.sub(r"\s*[×x]\s*", "x", val)
                found.append({
                    "要素": rule["要素"], "名称": rule["名称"], "对象": obj,
                    "值": val, "单位": rule["单位"],
                    "来源类型": KIND_SOURCE.get(kind, kind),
                    "证据": f"{path.name}:{ln}", "证据行": src_line.strip()[:160],
                    "文件": path.name,
                })
    return found


def dedup_items(found: list[dict]) -> list[dict]:
    """同一 (要素, 名称, 对象, 值) 只留首条；同值不同来源记进「另见」，不压缩证据面。"""
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for f in found:
        key = (f["要素"], f["名称"], f["对象"], f["值"])
        if key in seen:
            refs = seen[key].setdefault("另见", [])
            if len(refs) < 5 and f["证据"] not in refs:
                refs.append(f["证据"])
            continue
        seen[key] = f
        out.append(f)

    # 同一 (要素, 名称, 对象) 下被更长取值包含的取值，是同一事实的片段，不重复成行
    norm = lambda s: re.sub(r"\s+", "", s).lower()
    groups: dict[tuple, list[dict]] = {}
    for f in out:
        groups.setdefault((f["要素"], f["名称"], f["对象"]), []).append(f)
    keep: list[dict] = []
    for (_, 名称, _), rows in groups.items():
        alive = [r for r in rows
                 if not any(norm(r["值"]) != norm(o["值"]) and norm(r["值"]) in norm(o["值"]) for o in rows)]
        cap = MAX_PER_NAME.get(名称)
        if cap and len(alive) > cap:
            alive.sort(key=lambda r: -len(r["值"]))
            for d in alive[cap:]:
                for r in alive[:cap]:
                    refs = r.setdefault("另见", [])
                    if len(refs) < 5 and d["证据"] not in refs:
                        refs.append(d["证据"])
            alive = alive[:cap]
        keep.extend(alive)
    order = {id(f): i for i, f in enumerate(out)}
    keep.sort(key=lambda f: order[id(f)])
    return keep


def normalize_objects(items: list[dict]) -> None:
    """数据集版本/子集等未给对象时，用已抽到的数据集名补，便于按对象分组比冲突。"""
    ds = next((i["值"] for i in items if i["名称"] == "数据集名"), None)
    for i in items:
        if ds and i["名称"] in {"数据集版本", "数据集规模", "划分比例", "划分随机方式",
                               "预处理", "子集规模", "子集用途"} and i["对象"] in {"数据", "划分", "-"}:
            i["对象"] = ds


# ---------------------------------------------------------------------------
# 步骤：先取材料里的有序步骤；没有再取代码公开函数；再没有取带时间的动作行。
# 每步尽量填 输入/操作/输出/判断条件，填不出写"待作者确认"，不倒推。
# ---------------------------------------------------------------------------
def _enrich(操作: str, 原文: str) -> dict:
    rec = {"操作": 操作, "输入": "", "输出": "", "判断条件": ""}
    for k, rx in STEP_MARK.items():
        m = rx.search(原文)
        if m:
            rec[k] = m.group(1).strip()
    return rec


def extract_steps(code: list[Path], notes: list[Path], logs: list[Path]) -> tuple[list[dict], str]:
    steps: list[dict] = []
    # 1) 材料里显式编号的步骤（最接近"有序、可执行的步骤序列"）
    for p in notes + logs:
        for i, line in enumerate(read(p).splitlines(), 1):
            m = ORDERED.match(line)
            if not m:
                continue
            body = m.group(1).strip()
            if not STEP_HINT.search(body):
                continue
            rec = _enrich(body[:160], body)
            rec["证据"] = f"{p.name}:{i}"
            steps.append(rec)
    src = "材料中编号步骤" if steps else ""
    # 2) 代码公开函数：签名给输入，return/save 给输出，if 给判断条件
    if not steps:
        for p in code:
            lines = read(p).splitlines()
            for i, line in enumerate(lines, 1):
                m = re.match(r"\s*def\s+(\w+)\s*\(([^)]*)\)", line)
                if not m or m.group(1).startswith("_"):
                    continue
                rec = {"操作": f"`{m.group(1)}({m.group(2).strip()})`",
                       "输入": m.group(2).strip() or "无显式参数",
                       "输出": "", "判断条件": "", "证据": f"{p.name}:{i}"}
                indent = len(line) - len(line.lstrip())
                for j in range(i, min(i + 40, len(lines))):
                    body = lines[j]
                    if body.strip() and (len(body) - len(body.lstrip())) <= indent:
                        break
                    if not rec["输出"]:
                        mo = re.search(r"\breturn\s+(\S.{0,60})", body) or \
                             re.search(r"(?:save|write|export|dump)\w*\(([^)]{0,60})\)", body)
                        if mo:
                            rec["输出"] = mo.group(1).strip()
                    if not rec["判断条件"]:
                        mi = re.search(r"\b(?:if|while)\s+(.{0,60}?)\s*:", body)
                        if mi:
                            rec["判断条件"] = mi.group(1).strip()
                steps.append(rec)
        src = "代码公开函数" if steps else src
    # 3) 日志/说明里带时间的动作行
    if not steps:
        for p in logs + notes:
            for i, line in enumerate(read(p).splitlines(), 1):
                t = line.strip().lstrip("-*").strip()
                m = CLOCK.search(t)
                if not (m and STEP_HINT.search(m.group(2))):
                    continue
                rec = _enrich(m.group(2)[:160], m.group(2))
                kv = KV.findall(m.group(2))
                if kv and not rec["输出"]:
                    rec["输出"] = "，".join(f"{k}={v}" for k, v in kv)
                rec["证据"] = f"{p.name}:{i}"
                steps.append(rec)
        src = "日志/说明的带时间动作行" if steps else src
    return steps[:20], (src or "无")


# ---------------------------------------------------------------------------
# 对齐：正文写没写到？数值与材料一致吗？哪些数值材料里根本没有？
# ---------------------------------------------------------------------------
def written(value: str, text: str) -> bool:
    if value in text:
        return True
    parts = [p for p in re.split(r"[\s×x\-_]+", value) if p]
    return len(parts) > 1 and all(p in text for p in parts)


def num_eq(a: str, b: str) -> bool:
    try:
        x, y = float(a), float(b)
    except ValueError:
        return a == b
    return abs(x - y) <= 1e-12 * max(1.0, abs(x), abs(y))


def align(items: list[dict], manuscript: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """返回 (正文已写, 正文漏写, 正文与材料不符, 无来源数值)。"""
    ok, missing, mismatch_val = [], [], []
    if not manuscript:
        return ok, missing, mismatch_val, []
    for f in items:
        bad = _near_value(manuscript, f) if f["名称"] in ALIAS else None
        hit = written(f["值"], manuscript) if f["名称"] in ("硬件", "软件版本") else f["值"] in manuscript
        if bad:
            # 名称紧邻的数值与材料不一致（如 lr 0.01 vs 3e-4）优先按"不符"报，避免漏判
            mismatch_val.append({"要素": f["要素"], "名称": f["名称"], "材料值": f["值"],
                                 "正文值": bad[0], "证据": f"正文:{bad[1]}",
                                 "材料证据": f["证据"], "说明": "正文数值与材料不一致"})
            continue
        (ok if hit else missing).append(f)

    # 正文里出现、但所有材料里都没有的数值 → 疑似为段落顺畅补出来的
    known = {f["值"] for f in items}
    stamps = [(m.start(), m.end()) for m in STAMP.finditer(manuscript)]
    unsourced = []
    for m in NUM.finditer(manuscript):
        v = m.group(1)
        if v in known or len(v) <= 1:
            continue
        if any(a <= m.start() < b for a, b in stamps):
            continue
        s = m.start()
        if line_text(manuscript, s).lstrip().startswith("#"):
            continue  # 章节号 3.1 / 标题序号不是实验数值
        if s >= 2 and manuscript[s - 1] in "-_" and manuscript[s - 2].isalnum():
            continue
        if any(num_eq(v, x) for x in known if re.fullmatch(r"[0-9.eE+\-]+", x)):
            continue
        if any(num_eq(v, x) for x in (d["正文值"] for d in mismatch_val)):
            continue
        ln = line_of(manuscript, s)
        unsourced.append({"值": v, "证据": f"正文:{ln}",
                          "上下文": line_text(manuscript, s).strip()[:120],
                          "说明": "材料中未找到该数值的来源"})
    return ok, missing, mismatch_val, unsourced


def _near_value(text: str, item: dict) -> tuple[str, int] | None:
    """在正文里找"参数名紧邻的数值"，若与材料取值不同则返回 (正文值, 行号)。

    只认 `名称 + 分隔符/为/是/=/： + 数值` 这种紧邻写法（`学习率 0.01`、`lr=0.01`），
    不在句子里搜"附近的数字"——那样会把 `epoch 12, batch 64` 里的 64 误判成轮数。
    宁可漏判（漏判的数值仍会进"无来源数值"），不误报。
    """
    aliases = ALIAS.get(item["名称"])
    if not aliases:
        return None
    pat = re.compile(
        r"(?:%s)[ \t]*(?:设为|设置为|取为|为|是|取|=|:|：)?[ \t]*(?P<x>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        % "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)), re.I)
    for m in pat.finditer(text):
        v = m.group("x")
        if len(v) <= 1 or num_eq(v, item["值"]):
            continue
        return v, line_of(text, m.start())
    return None


# ---------------------------------------------------------------------------
# 冲突：同一 (要素, 名称, 对象) 在多份材料里取值不同 → 不静默取一个，交作者裁定。
# ---------------------------------------------------------------------------
def find_conflicts(items: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for i in items:
        groups.setdefault((i["要素"], i["名称"], i["对象"]), []).append(i)
    out = []
    for (要素, 名称, 对象), rows in groups.items():
        vals = {r["值"] for r in rows}
        if len(vals) < 2:
            continue
        out.append({
            "要素": 要素, "名称": 名称, "对象": 对象,
            "取值": [{"值": r["值"], "来源": r["证据"], "来源类型": r["来源类型"]} for r in rows],
            "建议": "配置文件优先；若无配置文件则以日志为准，并请作者确认",
        })
    return out


# ---------------------------------------------------------------------------
# 限制条件：由"检测到的事实"推失效场景，不写套话。
# ---------------------------------------------------------------------------
def derive_limitations(items: list[dict], steps: list[dict], manuscript: str,
                       unsourced: list[dict], badvals: list[dict], conflicts: list[dict],
                       schema_note: str) -> list[dict]:
    have = {}
    for i in items:
        have.setdefault(i["名称"], []).append(i)
    lim: list[dict] = []

    def add(场景, 依据, 适用, 处置):
        lim.append({"失效场景": 场景, "触发依据": 依据, "适用范围": 适用, "处置": 处置})

    runs = have.get("重复次数")
    if not runs:
        add("未报告重复次数：单次运行的结果没有方差信息，不能据此宣称改进显著",
            "材料中无 num_runs / 重复次数", "所有比较类结论", "写入章节并在追问清单请作者补")
    elif all(int(re.sub(r"\D", "", r["值"]) or 0) <= 1 for r in runs):
        add("重复次数为 1：无法估计随机波动，差异可能来自初始化",
            "；".join(f"{r['证据']}" for r in runs), "所有比较类结论", "写入章节并请作者确认是否需要多次运行")
    if "随机种子" not in have:
        add("未给随机种子：重跑无法复现同一组初始化与数据顺序",
            "材料中无 seed / 随机种子", "复现同一数值", "写入章节并请作者补种子")
    if "划分比例" not in have:
        add("未给数据划分比例：无法复现同一子集切分，跨实验读数不可比",
            "材料中无 split / 划分", "复现同一评测子集", "写入章节并请作者补划分")
    if "数据集名" in have and "数据集版本" not in have:
        add("数据集版本不明：vN 与 vM 混用会改变样本集合，读数不可直接比较",
            "已抽到数据集名但无版本", "跨版本结果比较", "写入章节并请作者补版本")
    if "评价指标" not in have and "指标值" not in have:
        add("未声明评价指标：无法判断主结果用哪个指标，比较没有共同刻度",
            "材料中无 metric / 指标", "主结果表述", "写入章节并请作者补指标定义")
    stats = have.get("统计方式")
    if not stats:
        add("未给统计方式：单点数值比较，无均值/方差/显著性，结论只能作描述",
            "材料中无 mean±std / 多次平均", "任何'优于/劣于'的表述", "写入章节并请作者补统计口径")
    conds = [r["值"] for r in have.get("对照条件", [])]
    if not any("预算" in c or "算力" in c or "budget" in c.lower() for c in conds):
        add("对照未声明同预算：基线与本方法的算力/数据预算未知时，比较不成立",
            "材料中无同预算 / same budget 声明", "与基线的可比性", "写入章节并请作者确认预算是否对齐")
    if "硬件" in have:
        hw = have["硬件"][0]["值"]
        m = re.match(r"^(\d+)", hw)
        if m and int(m.group(1)) <= 1:
            add("单卡实验：吞吐、显存与 batch 结论不能外推到多卡",
                have["硬件"][0]["证据"], "效率类结论", "写入章节")
    if not have.get("代码版本") and not have.get("训练命令"):
        add("缺代码版本与训练命令：读者拿不到当时的实现与启动方式",
            "材料中无 commit / 命令", "可复现性", "写进复现清单缺项并追问作者")
    elif not have.get("训练命令"):
        add("缺训练命令：读者无法一步复做（参数表在，但入口缺失）",
            "材料中无命令行", "可复现性", "写进复现清单缺项并追问作者")
    if unsourced:
        add(f"章节中有 {len(unsourced)} 处数值暂不可回溯：发表前需逐条核到材料",
            "；".join(u["证据"] for u in unsourced[:5]), "数值可信度", "列入追问清单")
    if badvals:
        add(f"正文与材料有 {len(badvals)} 处数值不一致：以材料为准改稿或请作者确认",
            "；".join(f"{b['名称']} 正文 {b['正文值']} / 材料 {b['材料值']}" for b in badvals[:5]),
            "正文数值", "列入修改差异与追问清单")
    if conflicts:
        add(f"材料内部有 {len(conflicts)} 处参数冲突：同一参数不同来源取值不同",
            "；".join(f"{c['名称']}" for c in conflicts[:5]), "参数取值", "请作者裁定后再定稿")
    if schema_note and "未提供" in schema_note:
        add("未提供 schema.json：键名匹配可能漏项，覆盖度受内置规则限制",
            schema_note, "抽取覆盖度", "由模型先读材料出 schema.json 后重跑")
    if not steps:
        add("未抽到可执行步骤：材料里没有编号步骤、代码函数或带时间的动作行",
            "步骤为空", "复现操作序列", "请作者补步骤或补代码/日志")
    return lim


# ---------------------------------------------------------------------------
# 追问清单：每个缺口一条可执行问题（问什么、凭什么问、影响哪节、是否阻塞）
# ---------------------------------------------------------------------------
def build_questions(items, missing, unsourced, badvals, conflicts, lim, repro, covered) -> list[dict]:
    qs: list[dict] = []

    def add(类型, 对象, 证据, 问题, 影响, 阻塞):
        qs.append({"编号": f"Q{len(qs) + 1:02d}", "类型": 类型, "对象": 对象,
                   "证据": 证据, "问题": 问题, "影响章节": 影响, "阻塞": 阻塞})

    for sec in SIX:
        if sec in covered:
            continue
        add("要素缺失", sec, "材料中未抽到该类要素",
            f"请提供「{sec}」的材料（配置键、日志行或一段描述），否则该节只能写待作者确认",
            sec, "是")
    for c in conflicts:
        add("配置冲突", f"{c['名称']}（{c['对象']}）",
            "；".join(f"{v['值']}@{v['来源']}" for v in c["取值"]),
            f"{c['名称']}同时有 " + "、".join(f"{v['值']}（{v['来源']}）" for v in c["取值"])
            + "，正文应写哪一个？", c["要素"], "是")
    for b in badvals:
        add("参数无来源/不符", f"{b['名称']}", f"{b['证据']} 正文 {b['正文值']}；材料 {b['材料值']}",
            f"正文写 {b['正文值']}，材料为 {b['材料值']}（{b['材料证据']}），以哪个为准？",
            b["要素"], "是")
    for u in unsourced:
        add("参数无来源", f"数值 {u['值']}", f"{u['证据']}「{u.get('上下文', '')}」",
            f"正文数值 {u['值']} 在材料中找不到来源，请给出出处或删除", "参数", "是")
    for m in missing:
        add("正文漏写", _label(m), m["证据"],
            f"材料里有 {_label(m)}（{m['证据']}），正文未写，是否允许按材料补入？",
            m["要素"], "否")
    names = {i["名称"] for i in items}
    if "数据集名" in names and "数据集版本" not in names:
        add("数据集版本不明", "数据集版本", "材料中无 version", "数据集用的是哪个版本/日期快照？",
            "数据集", "是")
    if not items:
        add("材料不足", "全部要素", "材料为空", "请提供配置、日志或代码，否则无法抽取", "全文", "是")
    for r in repro.get("缺项", []):
        add("复现缺项", r, "复现清单缺项",
            f"复现清单缺「{r}」：请提供{REPRO_TIP.get(r, r)}，否则读者按章节复现会断在这一步",
            "复现与限制", "否")
    for l in lim:
        if "材料中无" in l["触发依据"]:
            obj = l["触发依据"].replace("材料中无", "").strip(" ，,")
            add("材料缺失", obj, l["触发依据"],
                f"缺「{obj}」：请提供对应材料，否则「{l['失效场景'][:16]}…」会留在限制条件里",
                l["适用范围"], "否")
    # 去重（同类型+同对象只留一条）
    seen, out = set(), []
    for q in qs:
        k = (q["类型"], q["对象"])
        if k in seen:
            continue
        seen.add(k)
        out.append(q)
    return out


# ---------------------------------------------------------------------------
# 渲染：章节成稿按六类要素排列，缺的地方写"待作者确认"，并单列修改差异。
# ---------------------------------------------------------------------------
def _cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


MULTI_NAMES = {"软件版本", "硬件", "子集规模", "子集用途", "数据集版本", "数据集规模", "预处理",
               "划分比例", "划分随机方式", "精度", "预热步数", "指标值", "统计方式", "对照条件",
               "基线对象", "基线版本", "运行环境"}


def _label(item: dict) -> str:
    """要素条目的可读名：需要区分对象的名称写 `名称（对象）=值`，其余只写 `名称=值`。"""
    obj = item.get("对象")
    name = item["名称"] if (not obj or item["名称"] not in MULTI_NAMES) else f"{item['名称']}（{obj}）"
    return f"{name}={item['值']}"


def render_chapter(case_id, items, steps, step_src, missing, badvals, unsourced,
                   conflicts, lim, repro, questions, buckets, manuscript) -> str:
    by = lambda sec: [i for i in items if i["要素"] == sec]
    missing_keys = {(m["名称"], m["值"]) for m in missing}
    bad_names = {b["名称"] for b in badvals}
    L: list[str] = [f"# 方法与实验章节（{case_id}）", ""]
    kinds = "、".join(f"{k} {len(v)}" for k, v in sorted(buckets.items()) if v)
    L += [f"> 生成自 {sum(len(v) for v in buckets.values())} 份材料（{kinds}）；"
          f"证据单位 file:line；本稿只写材料里有的内容，未新增材料外事实。", ""]

    # 0 修改差异：相对原稿改了什么，逐条可回指
    L += ["## 0 修改差异（相对原稿）", ""]
    if not manuscript:
        L += ["无原稿输入：本次为首次成稿，六节全部按材料生成，不涉及改稿。", ""]
    else:
        rows = []
        for m in missing:
            rows.append(("补入", m["要素"], _label(m), m["证据"]))
        for b in badvals:
            rows.append(("改值", b["要素"], f"{b['名称']}：正文 {b['正文值']} → 材料 {b['材料值']}",
                         f"{b['材料证据']}；{b['证据']}"))
        for u in unsourced:
            rows.append(("标注待确认", "参数", f"数值 {u['值']} 无来源", u["证据"]))
        for c in conflicts:
            rows.append(("标注冲突", c["要素"], f"{c['名称']} 有 {len(c['取值'])} 个取值",
                         "；".join(v["来源"] for v in c["取值"])))
        if rows:
            L += ["| 类型 | 章节 | 内容 | 依据 |", "|-|-|-|-|"]
            L += [f"| {a} | {b} | {_cell(c)} | {_cell(d)} |" for a, b, c, d in rows[:40]]
            if len(rows) > 40:
                L.append(f"| … | | 其余 {len(rows) - 40} 条同类改动见 params.tsv | |")
        else:
            L.append("正文与材料一致，无需改动。")
        L.append("")

    # 1 步骤
    L += ["## 1 步骤", ""]
    if steps:
        L += ["| # | 操作 | 输入 | 输出 | 判断条件 | 来源 |", "|-|-|-|-|-|-|"]
        for i, s in enumerate(steps, 1):
            L.append(f"| {i} | {_cell(s['操作'])} | {_cell(s['输入'] or '待作者确认')} | "
                     f"{_cell(s['输出'] or '待作者确认')} | {_cell(s['判断条件'] or '无显式判断条件')} | "
                     f"{s['证据']} |")
        L.append(f"\n步骤来源：{step_src}（材料原文，未做语义归纳）。")
    else:
        L.append("待作者确认：材料中未识别到可执行步骤（无编号步骤、无公开函数、无带时间的动作行）。")
    L.append("")

    # 2 实验设置
    L += ["## 2 实验设置", ""]
    hard = by("实验设置")
    if hard:
        got = {i["名称"]: i for i in hard}
        sent = []
        if "硬件" in got:
            sent.append(f"实验在 {got['硬件']['值']} 上完成")
        if "软件版本" in got and "运行环境" not in got:
            vers = [i for i in hard if i["名称"] == "软件版本"]
            sent.append("软件版本为 " + "、".join(f"{v['对象']} {v['值']}" for v in vers))
        if "重复次数" in got:
            sent.append(f"每个配置重复 {got['重复次数']['值']} 次")
        if "精度" in got:
            sent.append(f"训练精度为 {got['精度']['对象']}={got['精度']['值']}")
        if "分布式策略" in got:
            sent.append(f"并行策略为 {got['分布式策略']['值']}")
        if "评测频率" in got:
            sent.append(f"每 {got['评测频率']['值']} 步评测一次")
        if "运行环境" in got:
            sent.append(f"运行环境为 {got['运行环境']['值']}")
        L.append("；".join(sent) + "。")
        L.append("")
        for i in hard:
            L.append(f"- {i['名称']}（{i['对象']}）：{i['值']} —— {i['证据']}")
    else:
        L.append("待作者确认：材料中未见硬件、软件版本、重复次数等实验设置。")
    L.append("")

    # 3 参数
    L += ["## 3 参数", ""]
    prm = by("参数")
    if prm:
        L += ["| 参数 | 取值 | 单位 | 作用对象 | 来源类型 | 正文 | 来源 |", "|-|-|-|-|-|-|-|"]
        for i in prm:
            st = "无正文"
            if manuscript:
                st = ("不符" if i["名称"] in bad_names else
                      "漏写" if (i["名称"], i["值"]) in missing_keys else "已写到")
            note = "冲突" if any(c["名称"] == i["名称"] and c["对象"] == i["对象"] for c in conflicts) else ""
            L.append(f"| {i['名称']} | {_cell(i['值'])} | {i['单位'] or '—'} | {_cell(i['对象'])} | "
                     f"{i['来源类型']} | {st}{('／' + note) if note else ''} | {i['证据']} |")
        L.append("")
        L.append("正文一栏：已写到=原稿已有；漏写=材料有而原稿没有，已按材料补入；"
                 "不符=原稿数值与材料不一致（见修改差异）；无正文=本次未给原稿。")
    else:
        L.append("待作者确认：未从配置、日志或代码取到参数。")
    L.append("")

    # 4 数据集与划分
    L += ["## 4 数据集与划分", ""]
    ds = by("数据集")
    if ds:
        for name in ("数据集名", "数据集版本", "数据集规模", "预处理", "划分比例",
                     "划分随机方式", "子集规模", "子集用途"):
            rows = [i for i in ds if i["名称"] == name]
            if not rows:
                L.append(f"- {name}：待作者确认")
                continue
            for i in rows:
                obj = "" if i["对象"] in {"数据", "划分", "-"} else f"（{i['对象']}）"
                unit = f" {i['单位']}" if i["单位"] and i["单位"] not in ("按指标",) else ""
                L.append(f"- {name}{obj}：{i['值']}{unit} —— {i['证据']}")
    else:
        L.append("待作者确认：未找到数据集名称、版本、规模或划分。")
    L.append("")

    # 5 对照实验
    L += ["## 5 对照实验", ""]
    cmp_ = by("对照")
    if cmp_:
        for name in ("基线对象", "基线版本", "对照条件", "评价指标", "指标值", "统计方式"):
            rows = [i for i in cmp_ if i["名称"] == name]
            if not rows:
                L.append(f"- {name}：待作者确认（材料中未见）")
                continue
            for i in rows:
                L.append(f"- {name}：{i['值']} —— {i['证据']}")
        conds = {i["值"] for i in cmp_ if i["名称"] == "对照条件"}
        if not any("预算" in c or "算力" in c for c in conds):
            L.append("- 可比性：材料未声明同预算，正文只能写\"同设定下比较\"，不得写\"更优\"")
    else:
        L.append("待作者确认：材料中未见基线对象、对照条件、评价指标或统计方式。")
    L.append("")

    # 6 复现与限制
    L += ["## 6 复现与限制", "", "### 6.1 复现清单", ""]
    L += ["| 项目 | 取值 | 来源 |", "|-|-|-|"]
    for r in repro["items"]:
        L.append(f"| {r['项目']} | {_cell(r['值'])} | {r['证据']} |")
    for r in repro["缺项"]:
        L.append(f"| {r} | 待作者确认 | 材料中未找到 |")
    L += ["", "### 6.2 限制条件", ""]
    if lim:
        L += ["| 失效场景 | 触发依据 | 适用范围 |", "|-|-|-|"]
        L += [f"| {_cell(l['失效场景'])} | {_cell(l['触发依据'])[:90]} | {_cell(l['适用范围'])} |" for l in lim]
    else:
        L.append("材料齐备，未推出失效场景；仍需作者确认适用范围。")
    L.append("")

    # 7 待作者确认
    L += ["## 7 待作者确认", ""]
    if questions:
        L += ["| 编号 | 类型 | 问题 | 影响章节 | 阻塞 |", "|-|-|-|-|-|"]
        for q in questions[:25]:
            L.append(f"| {q['编号']} | {q['类型']} | {_cell(q['问题'])} | {q['影响章节']} | {q['阻塞']} |")
        if len(questions) > 25:
            L.append(f"| … | | 其余 {len(questions) - 25} 条见 questions.md | | |")
    else:
        L.append("无：材料与正文未发现需人工确认的项。")
    L += ["", "---", "本稿由 run.py 生成，可继续编辑；带「待作者确认」的项未被补写成事实。", ""]
    return "\n".join(L)


def render_params(items, manuscript, missing_keys, conflicts, bad_names=()) -> str:
    head = "要素\t名称\t作用对象\t取值\t单位\t来源类型\t正文\t证据\t备注"
    rows = []
    ck = {(c["名称"], c["对象"]): c for c in conflicts}
    for i in items:
        st = "无正文"
        if manuscript:
            st = ("不符" if i["名称"] in bad_names else
                  "漏写" if (i["名称"], i["值"]) in missing_keys else "已写到")
        note = ""
        c = ck.get((i["名称"], i["对象"]))
        if c:
            note = "冲突：" + "；".join(f"{v['值']}@{v['来源']}" for v in c["取值"])
        rows.append("\t".join([i["要素"], i["名称"], i["对象"], i["值"], i["单位"] or "—",
                               i["来源类型"], st, i["证据"], note]))
    return head + "\n" + "\n".join(rows) + "\n"


def build_repro(items, buckets) -> dict:
    got = {i["名称"]: i for i in items if i["要素"] == "复现"}
    out = []
    for name in ("代码版本", "训练命令", "数据获取"):
        if name in got:
            out.append({"项目": name, "值": got[name]["值"], "证据": got[name]["证据"]})
    env = [i for i in items if i["名称"] in ("软件版本", "硬件", "运行环境")]
    if env:
        out.append({"项目": "环境",
                    "值": "；".join(f"{i['对象']} {i['值']}" if i["名称"] == "软件版本" else i["值"]
                                   for i in env),
                    "证据": "；".join(i["证据"] for i in env)})
    seed = [i for i in items if i["名称"] == "随机种子"]
    if seed:
        out.append({"项目": "随机种子", "值": seed[0]["值"], "证据": seed[0]["证据"]})
    dep = [i for i in items if i["名称"] == "依赖清单"]
    if dep:
        out.append({"项目": "依赖清单", "值": dep[0]["值"], "证据": dep[0]["证据"]})
    need = ["代码版本", "环境", "训练命令", "数据获取", "随机种子"]
    have = {r["项目"] for r in out}
    return {"items": out, "缺项": [n for n in need if n not in have]}


def render_repro(case_id, repro) -> str:
    L = [f"# 复现清单（{case_id}）", "",
         "逐项给取值与来源；缺项单列，不补写材料里没有的命令或路径。", "",
         "| 项目 | 取值 | 来源 |", "|-|-|-|"]
    L += [f"| {r['项目']} | {_cell(r['值'])} | {r['证据']} |" for r in repro["items"]]
    for r in repro["缺项"]:
        L.append(f"| {r} | 待作者确认 | 材料中未找到 |")
    L += ["", "## 缺项说明", ""]
    if repro["缺项"]:
        L += [f"- {r}：材料中未见，读者按本清单复现时会在这一步断掉" for r in repro["缺项"]]
    else:
        L.append("- 无：代码版本、环境、命令、数据获取、随机种子均可回指材料。")
    L.append("")
    return "\n".join(L)


def render_questions(case_id, questions) -> str:
    L = [f"# 追问清单（{case_id}）", "",
         "每条缺口对应一个具体问题；`阻塞` 为「是」表示定稿前必须先回答。", ""]
    if not questions:
        L.append("无：材料与正文未发现需人工确认的项。")
        return "\n".join(L) + "\n"
    L += ["| 编号 | 类型 | 对象 | 证据 | 问题 | 影响章节 | 阻塞 |", "|-|-|-|-|-|-|-|"]
    for q in questions:
        L.append(f"| {q['编号']} | {q['类型']} | {_cell(q['对象'])} | {_cell(q['证据'])[:80]} | "
                 f"{_cell(q['问题'])} | {q['影响章节']} | {q['阻塞']} |")
    L += ["", "用法：直接按「证据」列回到材料核对，按「问题」列问作者；"
              "回答后修改材料重跑，本清单会随之收敛。", ""]
    return "\n".join(L)


def render_missing_input(root: Path, buckets: dict) -> str:
    seen = sorted({p.suffix.lower() for v in buckets.values() for p in v} - {""})
    return "\n".join([
        "# 缺料说明：没有找到可抽要素的材料", "",
        f"- 输入目录：`{root}`",
        f"- 实际扫到的扩展名：{seen or '（无文件）'}",
        "- 找过：`.yaml/.yml`（配置）、`.log`（日志）、`.py`（代码）、`.md/.tex/.txt`（正文与说明）、`.json`（配置）",
        "- 至少要一样：配置、日志、代码或材料说明；正文（manuscript）可选，仅用于对齐",
        "- 缺什么才能出产物：没有材料就抽不到参数与设置，`methods_section.md`、`params.tsv`、"
        "`repro.md`、`questions.md` 无从生成",
        "- 若是 PDF/DOCX 等二进制材料：本脚本不解析二进制，请先提取为 UTF-8 文本再重跑",
        "- 补齐后重跑：`python run.py --input <目录> --work <w> --out <o>`",
    ]) + "\n"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="output")
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--mode", default="skill", choices=["skill", "baseline"],
                    help="skill=本 Skill；baseline=纯模板填充（仅用于对照测量，不是交付形态）")
    a = ap.parse_args(argv)

    t0 = time.perf_counter()
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(a.input) if a.input else Path(".")

    case_id, contract, case_obj = a.case_id, [], {}
    cj = root / "case.json"
    if cj.exists():
        try:
            case_obj = json.loads(read(cj))
            case_id = case_id or case_obj.get("case_id")
            oc = case_obj.get("output_contract")
            contract = list(oc) if isinstance(oc, (dict, list)) else []
        except Exception:
            pass
    case_id = case_id or root.name or "case"

    buckets = discover(root)
    kind_of = {str(p): k for k, v in buckets.items() for p in v}
    cfg = buckets.get("配置", []) + buckets.get("日志", []) + buckets.get("说明", [])
    code = buckets.get("代码", [])
    ms_files = buckets.get("正文", [])
    manuscript = "\n".join(read(p) for p in ms_files) if ms_files else ""

    if a.mode == "baseline":
        return write_baseline(case_id, out, buckets, contract, t0)

    if not cfg and not code:
        result = {
            "case_id": case_id, "skill_version": VERSION, "status": "blocked",
            "输入": {"目录": str(root), "case_id": case_id,
                   "文件": [{"路径": str(p.relative_to(root)), "类型": k,
                             "字节": p.stat().st_size} for k, v in buckets.items() for p in v]},
            "六要素覆盖": {"覆盖": [], "缺失": list(SIX)},
            "统计": {"材料文件": sum(len(v) for v in buckets.values()), "抽取参数": 0,
                   "正文一致": 0, "正文未写": 0, "无来源数值": 0},
            "evidence": {"unit": "file:line", "items": []},
            "uncertainty": ["无输入：找不到配置/日志/代码/说明"],
            "gaps": ["未找到配置/日志/代码/说明材料，无法抽取要素"],
            "冲突": [], "对照": [], "限制条件": [], "追问清单": [],
            "复现清单": {"项目": [], "缺项": ["代码版本", "环境", "训练命令", "数据获取", "随机种子"]},
            "deliverables": ["missing_input.md"],
            "output_contract": contract,
            "目标产物": None, "事实与来源映射": None, "版本与边界": None,
            "elapsed_s": round(time.perf_counter() - t0, 3),
        }
        for k in contract:
            result.setdefault(str(k), None)
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        (out / "missing_input.md").write_text(render_missing_input(root, buckets), encoding="utf-8")
        print(json.dumps({"ok": False, "status": "blocked"}, ensure_ascii=False))
        return 1

    items = extract_items(cfg + code, kind_of)
    normalize_objects(items)
    items = dedup_items(items)

    # 依赖清单：按文件名判定，证据记到行 1（不猜内容）
    for k, v in buckets.items():
        for p in v:
            if p.name.lower() in DEP_FILES:
                items.append({"要素": "复现", "名称": "依赖清单", "对象": "环境", "值": p.name,
                              "单位": "文件", "来源类型": KIND_SOURCE.get(k, k),
                              "证据": f"{p.name}:1", "证据行": p.name, "文件": p.name})

    # schema.json（模型读材料产出的键清单）：逐条校验证据，对不上就不采信
    schema_note, rejected = "未提供 schema.json，仅用内置规则抽取，可能漏键", []
    schema_path = work / "schema.json"
    if not schema_path.exists():
        schema_path = root / "schema.json"
    if schema_path.exists():
        try:
            sch = json.loads(read(schema_path))
            keys = sch.get("keys") or []
            kept = 0
            for it in keys:
                if not isinstance(it, dict) or not it.get("证据"):
                    continue
                ev = str(it.get("证据"))
                fname, _, ln = ev.rpartition(":")
                target = next((p for p in root.rglob(fname) if p.is_file()), None)
                ok_ev = False
                if target is not None and ln.isdigit():
                    ok_ev = 1 <= int(ln) <= max(1, len(read(target).splitlines()))
                if not ok_ev:
                    rejected.append(ev)
                    continue
                items.append({"要素": str(it.get("要素", "参数")), "名称": str(it.get("名称", "")),
                              "对象": str(it.get("对象", "-")), "值": str(it.get("值", "")),
                              "单位": str(it.get("单位", "")), "来源类型": "作者确认",
                              "证据": ev, "证据行": "", "文件": fname})
                kept += 1
            schema_note = (f"已并入模型键清单 {kept} 条" if kept else "schema.json 无可采信条目")
            if rejected:
                schema_note += f"；{len(rejected)} 条证据对不上材料已退回（{', '.join(rejected[:3])}）"
        except Exception as exc:
            schema_note = f"schema.json 解析失败：{exc}"
    if rejected:
        items = [i for i in items if i["证据"] not in set(rejected)]

    steps, step_src = extract_steps(code, buckets.get("说明", []), buckets.get("日志", []))
    ok, missing, badvals, unsourced = align(items, manuscript)
    conflicts = find_conflicts(items)
    repro = build_repro(items, buckets)
    lim = derive_limitations(items, steps, manuscript, unsourced, badvals, conflicts, schema_note)

    # 要素覆盖只认"带证据的实物条目"：复现与限制要有复现项，不靠渲染了整整一节就算覆盖
    covered = []
    if steps:
        covered.append("步骤")
    for sec in ("实验设置", "参数", "数据集", "对照"):
        if any(i["要素"] == sec for i in items):
            covered.append(sec)
    if repro["items"]:
        covered.append("复现与限制")

    gaps = []
    if not manuscript:
        gaps.append("未提供正文（manuscript），本次只做材料抽取，不做正文对齐")
    for g in missing:
        gaps.append(f"正文未写 {_label(g)}（{g['证据']}）")
    for b in badvals:
        gaps.append(f"正文数值与材料不符：{b['名称']} 正文 {b['正文值']}（{b['证据']}），材料 {b['材料值']}（{b['材料证据']}）")
    for u in unsourced:
        gaps.append(f"正文数值 {u['值']} 在材料中无来源（{u['证据']}）")
    for c in conflicts:
        gaps.append(f"材料冲突：{c['名称']}（{c['对象']}）有 {len(c['取值'])} 个取值 —— "
                    + "；".join(f"{v['值']}@{v['来源']}" for v in c["取值"]))
    if "数据集名" in {i["名称"] for i in items} and "数据集版本" not in {i["名称"] for i in items}:
        gaps.append("数据集版本不明：已抽到数据集名但没有版本（版本含混会让复现失败）")
    for r in repro["缺项"]:
        gaps.append(f"复现清单缺 {r}（材料中未见，{REPRO_TIP.get(r, r)}）")
    for s in SIX:
        if s not in covered:
            gaps.append(f"六类要素缺：{s}（材料中未抽到该类要素，章节已写待作者确认）")

    blocking = [g for g in gaps
                if g.startswith(("正文数值", "正文未写", "材料冲突", "数据集版本不明", "六类要素缺"))]
    status = "success" if not blocking else "partial"
    questions = build_questions(items, missing, unsourced, badvals, conflicts, lim, repro, covered)

    chapter = render_chapter(case_id, items, steps, step_src, missing, badvals, unsourced,
                            conflicts, lim, repro, questions, buckets, manuscript)
    params_tsv = render_params(items, manuscript, {(m["名称"], m["值"]) for m in missing},
                               conflicts, {b["名称"] for b in badvals})
    (out / "methods_section.md").write_text(chapter, encoding="utf-8")
    (out / "params.tsv").write_text(params_tsv, encoding="utf-8")
    (out / "repro.md").write_text(render_repro(case_id, repro), encoding="utf-8")
    (out / "questions.md").write_text(render_questions(case_id, questions), encoding="utf-8")

    evidence_items = [{"要素": i["要素"], "名称": i["名称"], "对象": i["对象"], "值": i["值"],
                       "来源类型": i["来源类型"], "来源": i["证据"],
                       "证据行": i.get("证据行", ""),
                       **({"另见": i["另见"]} if i.get("另见") else {})} for i in items]
    uncertainty = ([f"正文数值 {u['值']} 无来源（{u['证据']}）" for u in unsourced]
                   + [f"正文与材料不符：{b['名称']} 正文 {b['正文值']} / 材料 {b['材料值']}" for b in badvals]
                   + ([schema_note] if schema_note else [])
                   + ([f"schema.json 证据退回 {len(rejected)} 条：{', '.join(rejected[:5])}"] if rejected else []))

    result = {
        "case_id": case_id,
        "skill_version": VERSION,
        "status": status,
        "输入": {"目录": str(root), "case_id": case_id,
               "文件": [{"路径": str(p.relative_to(root)), "类型": k,
                         "字节": p.stat().st_size,
                         "行数": len(read(p).splitlines())} for k, v in buckets.items() for p in v]},
        "六要素覆盖": {"覆盖": covered, "缺失": [s for s in SIX if s not in covered],
                    "证据数": {**{s: sum(1 for i in items if i["要素"] == s) for s in SIX},
                             "步骤": len(steps), "复现与限制": len(repro["items"])}},
        "统计": {"材料文件": sum(len(v) for v in buckets.values()),
               "抽取条目": len(items),
               "抽取参数": sum(1 for i in items if i["要素"] == "参数"),
               "步骤": len(steps), "对照条目": sum(1 for i in items if i["要素"] == "对照"),
               "复现条目": len(repro["items"]), "冲突": len(conflicts),
               "正文一致": len(ok), "正文未写": len(missing), "无来源数值": len(unsourced),
               "正文与材料不符": len(badvals), "限制条件": len(lim), "追问": len(questions)},
        "evidence": {"unit": "file:line", "条数": len(evidence_items), "items": evidence_items[:80]},
        "uncertainty": uncertainty,
        "gaps": gaps,
        "冲突": conflicts,
        "对照": [i for i in items if i["要素"] == "对照"],
        "复现清单": {"项目": repro["items"], "缺项": repro["缺项"]},
        "限制条件": lim,
        "追问清单": questions,
        "修改差异": [{"类型": r[0], "章节": r[1], "内容": r[2], "依据": r[3]}
                    for r in ([("补入", m["要素"], f"{m['名称']}={m['值']}", m["证据"]) for m in missing]
                              + [("改值", b["要素"], f"{b['名称']}：正文 {b['正文值']} → 材料 {b['材料值']}",
                                  f"{b['材料证据']}；{b['证据']}") for b in badvals]
                              + [("标注待确认", "参数", f"数值 {u['值']} 无来源", u["证据"]) for u in unsourced]
                              + [("标注冲突", c["要素"], f"{c['名称']} 有 {len(c['取值'])} 个取值",
                                  "；".join(v["来源"] for v in c["取值"])) for c in conflicts])]
                    or [{"类型": "首次成稿", "章节": "全文",
                         "内容": f"六节按材料生成，共 {len(items)} 条带来源条目", "依据": "—"}],
        "规则命中": {"规则表": {k: sum(1 for r in RULES if r["要素"] == k)
                           for k in ("参数", "实验设置", "数据集", "对照", "复现")},
                   "步骤来源": step_src,
                   "步骤": [{"操作": s["操作"], "输入": s["输入"], "输出": s["输出"],
                            "判断条件": s["判断条件"], "来源": s["证据"]} for s in steps],
                   "状态判据": "success：六类要素都有带证据条目、无正文数值问题、无正文漏写、"
                           "无材料冲突、数据集版本不缺失；partial：上述任一未满足"},
        "deliverables": ["methods_section.md", "params.tsv", "repro.md", "questions.md"],
        "output_contract": contract,
        "elapsed_s": round(time.perf_counter() - t0, 3),
    }
    _fixed = {
        "目标产物": {"path": "methods_section.md",
                   "字符数": len(chapter), "行数": len(chapter.splitlines()),
                   "章节": ["0 修改差异", "1 步骤", "2 实验设置", "3 参数", "4 数据集与划分",
                          "5 对照实验", "6 复现与限制", "7 待作者确认"],
                   "附带产物": ["params.tsv", "repro.md", "questions.md"],
                   "段落数": len([ln for ln in chapter.splitlines() if ln.strip()])},
        "事实与来源映射": {"证据单位": "file:line", "条数": len(evidence_items),
                        "逐条": evidence_items[:80],
                        "按要素": {s: sum(1 for i in items if i["要素"] == s) for s in SIX},
                        "无来源数值": [{"值": u["值"], "位置": u["证据"]} for u in unsourced],
                        "正文与材料不符": [{"名称": b["名称"], "正文值": b["正文值"], "材料值": b["材料值"],
                                    "位置": b["证据"], "材料证据": b["材料证据"]} for b in badvals]},
        "版本与边界": {"修改差异": result["修改差异"],
                     "人工确认项": [{"编号": q["编号"], "问题": q["问题"], "阻塞": q["阻塞"]}
                                 for q in questions],
                     "材料缺口": gaps,
                     "异常": ([f"材料冲突 {len(conflicts)} 处"] if conflicts else [])
                             + ([f"无来源数值 {len(unsourced)} 处"] if unsourced else [])
                             + ([schema_note] if "未提供" in schema_note or rejected else [])
                             + (["数据集版本不明"] if any("数据集版本不明" in g for g in gaps) else []),
                     "六要素缺失": [s for s in SIX if s not in covered]},
    }
    for _k, _v in _fixed.items():
        result.setdefault(_k, _v)
    for k in contract:
        result.setdefault(str(k), _fixed.get(str(k)))
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"ok": True, "status": status, "items": len(items),
                      "params": result["统计"]["抽取参数"], "steps": len(steps),
                      "refs": result["六要素覆盖"]["证据数"],
                      "missing": len(missing), "unsourced": len(unsourced),
                      "conflicts": len(conflicts), "gaps": len(gaps),
                      "questions": len(questions)}, ensure_ascii=False))
    return 0


def write_baseline(case_id, out: Path, buckets, contract, t0: float) -> int:
    """纯模板填充：只有六节标题，不从材料抽取、不给来源、不追问。

    这个模式只用于 scripts/compare.py 做同输入对照，证明增益来自抽取与对齐，
    不是交付形态；它的产物里明确标注 baseline。
    """
    L = [f"# 方法与实验章节（{case_id}）", "", "> baseline：模板填充，未从材料抽取，无来源、无追问。", ""]
    for sec, body in (("步骤", "按实验流程依次执行。"),
                      ("实验设置", "实验在若干 GPU 上完成。"),
                      ("参数", "使用常用超参数设置。"),
                      ("数据集与划分", "数据按常见比例划分。"),
                      ("对照实验", "与基线方法比较。"),
                      ("复现与限制", "按常规流程即可复现。")):
        L += [f"## {sec}", "", body, ""]
    chapter = "\n".join(L)
    (out / "methods_section.md").write_text(chapter, encoding="utf-8")
    (out / "params.tsv").write_text("要素\t名称\t作用对象\t取值\t单位\t来源类型\t正文\t证据\t备注\n", encoding="utf-8")
    (out / "repro.md").write_text(f"# 复现清单（{case_id}）\n\n按常规流程复现。\n", encoding="utf-8")
    (out / "questions.md").write_text(f"# 追问清单（{case_id}）\n\n无。\n", encoding="utf-8")
    result = {
        "case_id": case_id, "skill_version": f"{VERSION}-baseline", "status": "partial",
        "输入": {"目录": str(out), "case_id": case_id, "文件": []},
        "六要素覆盖": {"覆盖": [], "缺失": list(SIX),
                    "证据数": {s: 0 for s in SIX}},
        "统计": {"材料文件": sum(len(v) for v in buckets.values()), "抽取条目": 0, "抽取参数": 0,
               "步骤": 0, "对照条目": 0, "复现条目": 0, "冲突": 0, "正文一致": 0,
               "正文未写": 0, "无来源数值": 0, "正文与材料不符": 0, "限制条件": 0, "追问": 0},
        "evidence": {"unit": "file:line", "条数": 0, "items": []},
        "uncertainty": ["baseline：未做抽取与对齐，所有结论不可回溯"],
        "gaps": ["baseline：模板填充，未抽取任何要素"],
        "冲突": [], "对照": [], "复现清单": {"项目": [], "缺项": []}, "限制条件": [],
        "追问清单": [], "修改差异": [], "deliverables": ["methods_section.md"],
        "output_contract": contract,
        "目标产物": {"path": "methods_section.md", "字符数": len(chapter), "行数": len(chapter.splitlines()),
                   "章节": ["步骤", "实验设置", "参数", "数据集与划分", "对照实验", "复现与限制"]},
        "事实与来源映射": {"证据单位": "file:line", "条数": 0, "逐条": []},
        "版本与边界": {"修改差异": [], "人工确认项": [], "材料缺口": ["baseline 不做缺口分析"],
                    "异常": [], "六要素缺失": list(SIX)},
        "elapsed_s": round(time.perf_counter() - t0, 3),
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "status": "partial", "mode": "baseline"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
