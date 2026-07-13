# 科普视频文案生成器

从 EPUB/TXT/Markdown 书籍中提取文章，提炼知识/科普内容，生成剪映图文成片视频脚本，并通过本地 SQLite 数据库（`.claude/science-video-tracker.db`）追踪已处理故事，避免重复阅读。支持可选上传到 Notion。

## 核心理念

**书籍是矿，知识是金。** 不复述文章，不搬原文——从书里提炼出能让人"原来如此""涨知识了""我要收藏"的知识点，用剪映图文成片的形式传播出去。

好的科普视频文案标准：看完让人觉得**"原来是这样！"**或**"今天又学到了"**——不是被灌了知识点，是被点燃了好奇心、被治愈了认知盲区、被颠覆了错误认知。

## 使用方法

```
/science-video <书籍路径> [--top N] [--style 风格] [--notion] [--notion-parent PAGE_ID] [--output 输出目录]
```

- `--top N`：筛选 N 篇文章，默认不限（全部待处理文章）
- `--style {风格}`：指定视频风格（知识科普/生活小妙招/冷知识/健康养生/趣味百科），默认自动匹配
- `--notion`：上传文案到 Notion（默认不上传）
- `--notion-parent {page_id}`：指定 Notion 父页面 ID，默认读取环境变量 `NOTION_PARENT_PAGE_ID`
- `--output`：指定输出目录，默认为 `.outputs/`

示例：
- `/science-video downloads/小王子.epub`
- `/science-video downloads/读者（2021年第1期）.epub --top 5`
- `/science-video downloads/被讨厌的勇气.epub --style 知识科普 --notion`
- `/science-video ~/Desktop/某本书.txt --output ~/Desktop/科普文案`

## 数据库工具

所有 SQLite 操作通过 `.claude/science_video_schema.py` 提供的 Python 函数完成。技能执行过程中，以 `python3 -c` 或 heredoc 方式调用这些函数，无需直接写 SQL。

关键函数：
- `add_book(name, source_path, source_type, issue, date) → book_id`
- `get_processed_titles(book_name) → set[str]` — 获取已处理标题集合
- `add_story(book_id, title, author, section, word_count) → story_id`
- `update_evaluation(story_id, score, tags, best_styles, notes)`
- `mark_selected(story_id)` / `mark_rejected(story_id)`
- `add_video_script(story_id, style, path, date)`
- `update_notion_upload(story_id, style, page_id, page_url)`
- `get_stats() → dict` — 全局统计
- `get_book_progress(book_name?) → list` — 书籍处理进度

调用方式示例：
```bash
python3 -c "
import sys; sys.path.insert(0, '.claude')
from science_video_schema import *
init_db()
titles = get_processed_titles('读者（2021年第1期）')
print(titles)
"
```

## 核心约束

**最重要**：每处理一个故事，必须立即通过 `science_video_schema.py` 写入 SQLite 数据库。无论该故事被选中生成脚本还是被筛掉，都要标记为"已处理"。这是防止重复阅读的底线机制。数据库文件位于 `.claude/science-video-tracker.db`。

---

## 工作流：五阶段

### 阶段一：发现（Discovery）

**目标**：提取书籍元数据和所有可读文章/章节列表，过滤已处理项。

**步骤**：

1. **解析参数**：从 `$ARGUMENTS` 中提取书籍路径、`--top N`、`--style`、`--notion`、`--notion-parent`、`--output`

2. **判断文件类型**：
   - `.epub` → 用 Python `zipfile` 解压（注意 UTF-8 编码修复），读取 `nav.xhtml` 获取目录，读取 `content.opf` 获取元数据
   - `.txt` → 直接读取，按章节标题拆分
   - `.md` → 直接读取，按 heading 拆分

3. **EPUB 提取参考代码**（在 Bash 中以 `python3 << 'PYEOF'` 方式执行）：
```python
import zipfile, os, shutil, re
from html.parser import HTMLParser

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts = []
        self.skip = False
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip = True
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = False
    def handle_data(self, data):
        if not self.skip:
            t = data.strip()
            if t: self.texts.append(t)

def read_epub_structure(epub_path):
    """解压并读取 EPUB 结构和内容"""
    import tempfile
    tmp = tempfile.mkdtemp()
    with zipfile.ZipFile(epub_path, 'r') as zf:
        for info in zf.infolist():
            name = info.filename
            try: name = name.encode('cp437').decode('utf-8')
            except: pass
            target = os.path.join(tmp, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not info.is_dir():
                with zf.open(info) as s, open(target, 'wb') as d:
                    d.write(s.read())
    return tmp

def extract_text_from_xhtml(filepath):
    """从 XHTML 提取纯文本"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    extractor = TextExtractor()
    extractor.feed(content)
    return '\n'.join(extractor.texts)
```

4. **查询已处理故事**——通过 `get_processed_titles(book_name)` 获取该书已处理标题集合，过滤后得到待处理列表。

5. **注册书籍**——调用 `add_book(name, source_path, source_type, issue, date)` 获取 `book_id`（已存在则返回已有 id）。

6. **列出待处理故事**：输出编号、标题、作者、栏目（如有）、字数估计。如果指定了 `--top N`，只显示前 N 篇。每页显示 20 个。

7. **如果没有新故事**（全部已处理），告知用户并结束。

---

### 阶段二：评估（Evaluation）

**目标**：评估文章的知识科普视频转化潜力，对每个故事输出评分。

**评估维度**（每项 1-10 分，不显示给用户，仅作为思考依据）：

| 维度 | 说明 | 高分信号 | 低分信号 |
|---|---|---|---|
| **科普价值** ⭐ | 是否有能提炼成独立知识点的科学/生活内容 | 有明确的事实、数据、机制、原理解释 | 纯主观观点、无事实支撑、纯虚构 |
| **反常识性** | 内容是否挑战大众的常见认知 | "原来是这样"效应强，颠覆直觉 | 人人都知道、没有意外感 |
| **生活关联度** | 内容是否与普通人日常生活相关 | 健康、饮食、习惯、心理、家居等话题 | 过于抽象/学术/远离日常 |
| **画面感** | 内容是否可以通过视觉画面呈现 | 有具体场景、实验、对比、变化过程 | 纯概念/纯理论，无法具象化 |
| **情绪价值** | 看完后是否有正向情绪反应 | 好奇心被点燃、获得掌控感、被治愈 | 无情绪波动或引发焦虑/恐惧 |

> **科普价值是第一筛选标准**：不是每篇文章都有值得提炼的知识点。纯叙事、纯抒情、纯虚构、纯哲学思辨——直接淘汰。我们需要的是有事实支撑、能让人学到东西的内容。

**输出格式**——评估汇报以表格呈现：

```
| # | 标题 | 作者 | 栏目 | 字数 | 评分 | 核心知识点 | 标签 |
|----|------|------|------|------|------|-----------|------|
| 1 | 你的胃是第二个大脑 | 王博士 | 科普 | ~800 | ⭐⭐⭐⭐⭐ | 肠神经系统独立运作，影响情绪 | #人体 #脑科学 |
| 2 | 失眠的真相 | — | 生活 | ~500 | ⭐⭐⭐⭐ | 睡前蓝光抑制褪黑素分泌 | #健康 #睡眠 |
| 3 | 一封信 | 张三 | 文苑 | ~1200 | ⭐ | ❌ 纯叙事无知识含量 | #文学 |
```

评分用星级：⭐⭐⭐⭐⭐(9-10)  ⭐⭐⭐⭐(7-8)  ⭐⭐⭐(5-6)  ⭐⭐(3-4)  ⭐(1-2)

评估完成后，**立即写入数据库**——对每个评估过的故事调用 `add_story()` + `update_evaluation()`，状态为 `evaluated`。

询问用户：哪些文章要生成视频文案？可以选编号（如 `1,5,7-9`），也可以说"全部高分的"或"评分 4 星以上的"。

---

### 阶段三：生成（Generation）

**目标**：为用户选中的每个文章，提炼核心知识，生成剪映图文成片视频脚本。

**对每个选中的文章**：

#### 3.1 重读原文

读取文章全文，提炼核心知识点——一句话说清这篇文章能让观众学到什么。

#### 3.2 搜索补充素材

**⚠️ 搜索降级策略（严格执行）**：

1. **优先级 1**：使用 WebSearch 工具搜索
2. **优先级 2**：WebSearch 失败后，降级使用 Tavily API（curl 调用）
3. **优先级 3**：Tavily 也失败，使用 Playwright MCP 抓取网页
4. **优先级 4**：所有搜索工具都失败，在文案 frontmatter 中标注 `unverified`

搜索内容：
- 文章涉及的关键数据、事实的最新验证
- 相关的科学研究或权威来源
- 可以让知识点更生动的具体案例、数字、对比
- 画面素材的搜索线索

搜索策略：
- 第一轮：掌握基本事实 — "{知识点关键词} 科学原理" / "{知识点} 真相"
- 第二轮：寻找差异化角度 — "{知识点} 冷知识" / "{知识点} 你不知道" / "{知识点} 误区"

**⚠️ 搜索结果必须存档**到输出目录下的 `attachments/选题{序号}/` 目录，格式参照 ingest-hot。

#### 3.3 事实核查

基于搜索到的素材，对文章中涉及的关键事实进行验证：

| 检查项 | 说明 | 处理方式 |
|--------|------|----------|
| 具体数据 | 百分比、金额、数量等数字 | 无法验证的改为模糊表述或删除 |
| 科学原理 | 文章描述的原理是否准确 | 错误内容直接删除或修正 |
| 因果逻辑 | 推断性结论是否有数据支撑 | 凭空臆断的结论删除 |
| 时效性 | 信息是否仍然有效 | 过时信息标注或删除 |

核查结果记录在文案 frontmatter 中：

```yaml
fact_check:
  verified_claims: ["已验证的声明1", "已验证的声明2"]
  unverified_claims: ["存疑的声明1（原因）"]
  sources: ["来源1 URL", "来源2 URL"]
```

#### 3.4 生成剪映图文成片文案

基于搜索素材，按以下规范生成可直接粘贴到剪映的文案。

**输出目录**：`{output_dir}/{YYYY-MM-DD}/{HHMM}/选题{序号}-{标题缩写}.md`

##### 文案写作规范

0. **⚠️ 内容安全红线（强制遵守）**：
   - ❌ 禁止对品牌/公司进行负面批评
   - ❌ 禁止对政府/政策进行批判
   - ❌ 禁止制造群体对立
   - ❌ 禁止过度负面情绪宣泄
   - ❌ 禁止传播未经证实的天价保健偏方
   - ✅ 替代方案：用"正确认知"替代"品牌坑人"；用"科学解释"替代"政策有问题"

1. **情绪线不能断**：
   - **正确情绪曲线（知识科普）**：反常识(开场)→ 更震惊(真相)→ 颠覆认知(收尾)
   - **正确情绪曲线（生活小妙招）**：痛点(开场)→ 方法(过程)→ 惊喜效果(收尾)
   - **正确情绪曲线（冷知识）**：好奇(开场)→ 更好奇(揭秘)→ 炸了(真相)
   - **错误情绪曲线**：震惊 → 焦虑 → 恐惧 → 负能量。**焦虑恐惧不是科普**
   - **检验方法**：读每一段台词，问自己"观众看到这段会有什么情绪？"如果是"想骂""焦虑""害怕"，必须重写
   - **⚠️ 反塌房铁律**：视频后半段禁止变成以下任何一种——
     - ❌ 教科书朗读（"根据XX研究表明……"）
     - ❌ 学术论文复述（"XX大学的XX教授在XX期刊上发表了……"）
     - ❌ 负面恐吓（"不注意这个就会得XX病"）
     - ❌ 广告植入（"推荐大家购买XX产品"）

2. **差异化是第二原则**：
   - 同一个知识点可能很多人讲过，必须找到独特的切入角度
   - 策略：反直觉切入 / 普通人关联 / 隐藏细节 / 数字具象化 / 跨时空对比

3. **结构适配剪映分镜**：
   - 用空行分段，每段对应一个镜头画面
   - 每段 1-2 句话，15-40 字
   - 总字数 600-750 字（对应 2-3 分钟视频）
   - **段落数 10-12 段**，每一段都要有存在的理由
   - **⚠️ 无填充段规则**：删掉该段后视频仍然通顺 = 这段是废话，必须删
   - 首段至少 15 字以上

4. **开头钩子**：用反常识数据炸弹/悬念/让人"等等，真的吗？"的事实开场。绝不能是"今天给大家科普一下XX"。

5. **口语化表达**：短句为主，读起来自然流畅，像朋友告诉你一个很酷的事实。

6. **具象化改写**（核心）：

| 类型 | 问题 | 改写方向 |
|------|------|---------|
| 隐喻/比喻 | 检索到字面含义图片 | 替换为字面描述的具体行为 |
| 抽象概念 | 无对应视觉素材 | 替换为具体的人/物/场景 |
| 专业术语 | 剪映无法匹配 | 替换为日常语言+具体场景 |
| 纯观点句 | 无任何可检索实体 | 添加具体案例/场景承载观点 |

7. **结尾技巧**：
   - ❌ "关注我/点赞收藏" / "你怎么看评论区告诉我" / "今天就科普到这里"
   - ✅ 用一个比开场更颠覆的事实收尾，让人忍不住再看一遍或转发

##### 文件格式

```markdown
---
type: video-script
title: "{视频标题（吸引眼球，≤20字）}"
topic: "{知识点标签}"
source: "{书名} - {文章标题}"
style: "{视频风格}"
created: YYYY-MM-DD HH:MM
fact_check:
  verified_claims: ["已验证的声明1"]
  unverified_claims: ["存疑的声明1（原因）"]
  sources: ["来源1 URL"]
---

## 文案表格

| 序号 | 大纲 | 画面描述 | 拍摄手法 | 台词 | 素材搜索 |
|------|------|----------|----------|------|----------|
| 1 | [本段主题概要] | [主体+动作+环境] | [景别] [角度] [运镜] | [15-40字台词] | 【YouTube】精准词 \| 兜底词\n【Pexels】英文词1+英文词2 |
| 2 | ... | ... | ... | ... | ... |
| N | 结尾留白 | [呼应开头的画面] | [远景] [平拍] [固定] | （无台词，纯画面留白） | ... |

## 台词纯文本（可直接复制到剪映）

{每段台词，段落间用空行分隔，无编号无标记}

## 信息来源
- 书籍来源：{书名} - {文章标题} - {作者}
- 补充搜索：{来源1}
- 补充搜索：{来源2}
```

##### 素材搜索列格式

每个画面必须包含【YouTube】和【Pexels】两个来源标签：

```
【YouTube】精准搜索词 | 兜底搜索词 | 宽泛搜索词
【Pexels】english+keywords | alternative+search
【表情包】情感词+场景词（仅在适合时添加）
```

**搜索词构建规则**：
1. 必须包含选题具体关键词，严禁使用泛词
2. YouTube 用中文，Pexels 用英文
3. 每个画面至少 6 个搜索词组（YouTube 3个 + Pexels 3个）
4. 表情包仅在吐槽/情绪转折/纯观点段使用

#### 3.5 文案自检（12 项质量检查，强制执行）

生成每个文案后，必须逐项检查，不通过则当场修复：

| # | 检查项 | 检查方法 | 不通过则 |
|---|--------|----------|----------|
| 0 | **内容安全** | 是否存在品牌负面/政府批判/群体对立/过度负面？ | 用正向角度重写 |
| 1 | **素材搜索格式** | 每行是否同时包含【YouTube】和【Pexels】？每个标签下是否≥3套搜索词？ | 补充缺失标签和搜索词 |
| 2 | **段落数** | 台词纯文本段落数是否在 10-12 段范围内？ | 合并或拆分段落 |
| 3 | **总字数** | 台词纯文本总字数是否在 600-750 字？ | 删减或补充 |
| 4 | **反塌房** | 后半段（第 7 段起）是否变成教科书/论文/恐吓/广告？ | 用更大的反转或更震撼的事实替换 |
| 5 | **反cliché** | 是否包含"关注我""点赞收藏""你怎么看评论区告诉我""今天就到这里"？ | 用具体事实/细节收尾 |
| 6 | **正向情绪曲线** | 情绪是否持续升级？方向是否正向？ | 删掉断点段或负面段 |
| 7 | **3 秒钩子** | 第 1 段能否在 3 秒内抓住注意力？ | 重写第 1 段 |
| 8 | **差异化** | 核心角度搜索确认是否已被别人讲过？ | 换角度或放弃选题 |
| 9 | **事实核查** | frontmatter 中 verified/unverified 是否完整？ | 补充核查结果 |
| 10 | **具象化** | 是否存在抽象概念/隐喻/专业术语未替换？ | 替换为具体场景 |
| 11 | **情绪价值** | 看完后的主要情绪是什么？焦虑/恐惧则重写 | 用正向好奇心驱动替代恐惧 |

#### 3.6 保存文件并更新数据库

将文案保存到 `{output_dir}/{YYYY-MM-DD}/{HHMM}/选题{序号}-{标题缩写}.md`

**生成后，更新数据库**——调用 `mark_selected(story_id)`，再调用 `add_video_script(story_id, style, path, date)`。

---

### 阶段四：上传 Notion（可选，`--notion` 时执行）

文案生成后，如果指定了 `--notion`，将每个选题上传到 Notion。

**⚠️ 使用 stdlib（urllib），不依赖 requests 库。**

#### 前置条件

- `NOTION_TOKEN` 环境变量已设置
- `NOTION_PARENT_PAGE_ID` 环境变量已设置（或通过 `--notion-parent` 指定）
- 父页面已与 Notion Integration 共享

#### 上传流程

对每个选题，**生成并执行 Python 脚本**，通过 Notion REST API 创建 Page。

**脚本模板**（保存为 `{output_dir}/{date}/{time}/upload_notion.py`）：

```python
#!/usr/bin/env python3
"""Upload video script to Notion via REST API (stdlib only)."""
import os, json, sys, time, re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# ─── Load env from .env file ────────────────────────────────────────────────
def find_env_file():
    current_dir = os.path.abspath(os.getcwd())
    for _ in range(5):
        env_path = os.path.join(current_dir, ".env")
        if os.path.exists(env_path):
            return env_path
        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            break
        current_dir = parent_dir
    return None

env_path = find_env_file()
if env_path:
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "")

def notion_request(method, url_path, body=None):
    url = "https://api.notion.com" + url_path
    headers = {
        "Authorization": "Bearer " + NOTION_TOKEN,
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    data = json.dumps(body).encode("utf-8") if body else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        return e.code, err_body

def create_page(title, children=None):
    body = {
        "parent": {"page_id": PARENT_PAGE_ID},
        "properties": {"title": {"title": [{"text": {"content": title}}]}}
    }
    if children:
        body["children"] = children
    status, data = notion_request("POST", "/v1/pages", body)
    if status == 200 and isinstance(data, dict):
        return data["id"], data.get("url", "")
    else:
        print("  Page creation failed: {} {}".format(status, str(data)[:300]), file=sys.stderr)
        return None, None

def add_blocks(page_id, blocks):
    chunk_size = 100
    for i in range(0, len(blocks), chunk_size):
        chunk = blocks[i:i+chunk_size]
        status, data = notion_request(
            "PATCH", "/v1/blocks/" + page_id + "/children", {"children": chunk})
        if status != 200:
            print("  Block add failed: {} {}".format(status, str(data)[:200]), file=sys.stderr)
            return False
        if i + chunk_size < len(blocks):
            time.sleep(0.4)
    return True

# ─── Block Builders ──────────────────────────────────────────────────────────

def heading_block(text, level=2):
    t = {"rich_text": [{"text": {"content": text[:2000]}}]}
    if level == 1: return {"object": "block", "type": "heading_1", "heading_1": t}
    elif level == 3: return {"object": "block", "type": "heading_3", "heading_3": t}
    else: return {"object": "block", "type": "heading_2", "heading_2": t}

def para_block(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": text[:2000]}}]}}

def divider_block():
    return {"object": "block", "type": "divider", "divider": {}}

def callout_block(text, emoji="📌"):
    return {"object": "block", "type": "callout", "callout": {"rich_text": [{"text": {"content": text[:2000]}}], "icon": {"type": "emoji", "emoji": emoji}}}

def numbered_item(text):
    return {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"text": {"content": text[:2000]}}]}}

def bullet_item(text):
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": text[:2000]}}]}}

def table_row_block(cells):
    return {"type": "table_row", "table_row": {"cells": [[{"type": "text", "text": {"content": c[:2000]}}] for c in cells]}}

def table_block(headers, rows, table_width=None):
    if table_width is None:
        table_width = len(headers)
    children = [table_row_block(headers)] + [table_row_block(r) for r in rows]
    return {"object": "block", "type": "table", "table": {
        "table_width": table_width, "has_row_header": False,
        "has_column_header": True, "children": children
    }}

# ─── Parse MD File ──────────────────────────────────────────────────────────

def parse_frontmatter(content):
    m = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not m:
        return {}
    fm_text = m.group(1)
    result = {}
    current_key = None
    current_list = None
    in_list = False
    for line in fm_text.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('- ') and in_list:
            val = stripped[2:].strip().strip('"').strip("'")
            if current_list is not None:
                current_list.append(val)
            continue
        if ':' in stripped and not stripped.startswith('- '):
            if current_key and in_list and current_list is not None:
                result[current_key] = current_list
                in_list = False
                current_list = None
            k, v = stripped.split(':', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            current_key = k
            if v:
                result[k] = v
            else:
                in_list = True
                current_list = []
    if current_key and in_list and current_list is not None:
        result[current_key] = current_list
    return result

def extract_pure_text(content):
    m = re.search(r'## 台词纯文本.*?\n\n(.*?)(?=\n---\n|\n## 信息来源|\Z)', content, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""

def extract_sources_section(content):
    m = re.search(r'## 信息来源\n(.*?)(?=\n---\n|\n## |\Z)', content, re.DOTALL)
    if m:
        lines = [l.strip() for l in m.group(1).strip().split('\n') if l.strip().startswith('- ')]
        return [l[2:] for l in lines]
    return []

def extract_script_table_rows(content):
    m = re.search(r'\| 序号 \|.*?\n\|[-| ]+\n((?:\|.*\n)+)', content)
    if not m:
        return []
    rows = []
    for line in m.group(1).strip().split('\n'):
        if line.strip().startswith('|'):
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if cells and cells[0].isdigit():
                rows.append(cells)
    return rows

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    md_file = sys.argv[1] if len(sys.argv) > 1 else ""
    if not NOTION_TOKEN:
        print("ERROR: NOTION_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)
    if not md_file or not os.path.exists(md_file):
        print("ERROR: Markdown file not found: {}".format(md_file), file=sys.stderr)
        sys.exit(1)

    with open(md_file, "r") as f:
        content = f.read()

    fm = parse_frontmatter(content)
    title = fm.get("title", os.path.basename(md_file).replace(".md", ""))
    pure_text = extract_pure_text(content)
    sources = extract_sources_section(content)
    table_rows = extract_script_table_rows(content)
    style = fm.get("style", "")
    topic = fm.get("topic", "")
    created = fm.get("created", "")
    source = fm.get("source", "")
    verified = fm.get("verified_claims", [])
    unverified = fm.get("unverified_claims", [])
    if not isinstance(verified, list): verified = []
    if not isinstance(unverified, list): unverified = []

    blocks = []
    blocks.append(callout_block("风格: " + style + " | 话题: " + topic + " | 创建: " + created, "📋"))
    blocks.append(para_block("来源: " + source))
    blocks.append(divider_block())
    blocks.append(heading_block("文案表格", 2))
    if table_rows:
        headers = ["序号", "大纲", "画面描述", "拍摄手法", "台词", "素材搜索"]
        blocks.append(table_block(headers, table_rows, table_width=6))
    blocks.append(divider_block())
    blocks.append(heading_block("台词纯文本（可直接复制到剪映）", 2))
    if pure_text:
        paragraphs = [p.strip() for p in pure_text.split('\n\n') if p.strip()]
        for p in paragraphs:
            blocks.append(numbered_item(p))
    blocks.append(divider_block())
    blocks.append(heading_block("信息来源", 2))
    for s in sources:
        blocks.append(bullet_item(s))
    blocks.append(divider_block())
    blocks.append(heading_block("事实核查", 2))
    if verified:
        blocks.append(callout_block("已验证:\n" + "\n".join(["- " + v for v in verified[:10]]), "✅"))
    if unverified:
        blocks.append(callout_block("存疑:\n" + "\n".join(["- " + u for u in unverified[:10]]), "⚠️"))

    page_id, page_url = create_page(title, blocks)
    if page_id:
        print("SUCCESS:" + page_id + ":" + page_url)
    else:
        print("FAILED", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
```

#### Agent 执行步骤

1. **生成脚本**：将模板保存为 `studio/{日期}/{时分}/upload_notion.py`
2. **执行脚本**：对每个选题运行 `python3 upload_notion.py "选题{N}-xxx.md"`
3. **记录结果**：获取 page_id 和 page_url，调用 `update_notion_upload(story_id, style, page_id, page_url)`
4. **更新文案 frontmatter**：追加 Notion 信息

#### 错误处理

| 场景 | 处理 |
|------|------|
| `NOTION_TOKEN` 未设置 | 跳过上传，提示用户设置环境变量，本地文案不受影响 |
| API 返回 401 | Token 无效，提示重新生成 |
| API 返回 403 | 页面未共享，提示检查 Notion 设置 |
| API 返回 404 | 父页面不存在，提示检查 PAGE_ID |
| 上传失败 | 记录错误，提示手动上传，提供文案文件路径 |

---

### 阶段五：记录（Record）

**目标**：确保所有故事都已标记在 SQLite 中。

1. 将评估过但未选中的故事，调用 `mark_rejected(story_id)` 设为 `rejected`
2. 调用 `get_stats()` 获取最新统计
3. 输出本次处理总结：
   ```
   === 科普视频文案已生成 ===

   📖 处理书籍：{书名}
   📄 评估故事：Y 篇
   ✅ 生成脚本：Z 个
   ❌ 筛选排除：W 篇
   📁 输出目录：{实际路径}
   📤 Notion上传：{N}个（仅 --notion 时显示）

   选题1：{视频标题}（{风格}，约 {时长}）
     → 文案：{路径}
     → 事实核查：已验证 {N} 条声明，存疑 {N} 条
   ...
   ```

---

## 风格对照

| 风格 | 开场 | 情绪曲线 | 后半段写法 | 结尾 | 差异化建议 |
|---|---|---|---|---|---|
| `知识科普`（默认） | 反常识数据炸弹 | 震惊→更震惊→颠覆认知 | 用比开场更颠覆的事实收尾 | 留一个让人意外的事实 | 找到别人没注意到的数据细节 |
| `生活小妙招` | 痛点场景开场 | 痛→方法→惊喜效果 | 展示操作前后的对比效果 | 省钱/省时间的量化结果 | 找到最反直觉的生活技巧 |
| `冷知识` | "你知道XX吗？"式钩子 | 好奇→更好奇→炸了 | 每层揭秘比上一层更意外 | 真相让人忍不住分享 | 找到冷门但高关联的知识 |
| `健康养生` | 常见误区开场 | 误区→真相→正确做法 | 给出可操作的具体建议 | 一个简单易行的改变 | 用数据打破最常见的健康迷思 |
| `趣味百科` | 奇特现象开场 | 好奇→原理→原来如此 | 用拟人化/场景化解释原理 | 让人"下次看到就会想到" | 把枯燥百科变成有趣故事 |

---

## 评分参考校准

**9-10 分（必做）**：
- 有明确的知识点/科学事实，反常识性强，画面感好，看完让人"涨知识了"
- 例：身体冷知识、心理学实验、生活误区辟谣、自然现象科学解释

**7-8 分（推荐做）**：
- 有可提炼的知识点，某方面突出（反常识性/画面感/生活关联）
- 例：健康饮食建议、历史冷知识、科技原理科普

**5-6 分（可选做）**：
- 有潜在知识点，但不够锋利或过于平淡
- 例：泛泛的"多喝水有益健康"、人人都知道的生活常识

**3-4 分（通常跳过）**：
- 偏叙事无知识含量、偏抒情、偏虚构
- 例：散文、小说章节、个人日记

**1-2 分（直接跳过）**：
- 无知识含量、纯虚构、纯技术文档、引发焦虑恐惧
- 例：恐怖小说、产品说明书、纯技术参数

---

## 数据库表结构

SQLite 数据库 `.claude/science-video-tracker.db`，通过 `.claude/science_video_schema.py` 操作。

```
books
├── id (PK)
├── name (UNIQUE)     — 书名（无扩展名）
├── source_path       — 书籍文件路径
├── source_type       — 书籍类型
├── issue             — 期号或版本
└── processed_date    — YYYY-MM-DD

stories
├── id (PK)
├── book_id (FK → books.id)
├── title             — 文章标题
├── author            — 作者（可空）
├── section           — 栏目（可空）
├── word_count        — 字数
├── status            — 'evaluated' | 'selected' | 'rejected'
├── eval_score        — 1-10
├── eval_tags         — JSON 数组字符串
├── eval_best_styles  — JSON 数组字符串
├── eval_notes        — 备注
└── UNIQUE(book_id, title)

video_scripts
├── id (PK)
├── story_id (FK → stories.id)
├── style             — 视频风格
├── path              — 输出文件路径
├── generated_date    — YYYY-MM-DD
├── notion_page_id    — Notion 页面 ID（可空）
├── notion_page_url   — Notion 页面 URL（可空）
├── notion_uploaded   — 0 或 1
└── UNIQUE(story_id, style)
```

**操作规范**：
- 始终通过 `science_video_schema.py` 的函数操作数据库
- 每次 `python3 -c` 调用前先 `sys.path.insert(0, '.claude')`

---

## 其他要求

- **所有面向用户的输出用中文**
- **知识内容不得捏造**——所有知识点必须来自书籍原文或经过搜索验证，不能凭空编造
- **段落间用空行分隔**
- **每个生成阶段结束必须报告进度**
- **输出文件命名**：`{output_dir}/{YYYY-MM-DD}/{HHMM}/选题{N}-{标题缩写}.md`
- **视频标题自拟**——标题本身应该是一个有吸引力的知识点概括，不要直接用原文标题
