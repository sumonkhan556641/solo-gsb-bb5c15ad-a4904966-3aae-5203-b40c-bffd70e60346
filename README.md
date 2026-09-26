# 字幕校正、位置规划、专名统一、阅读负荷、智能断行、章节规划、择优保留、主题片段检索、双语配对、删段接轴与镜头切点避让工具

基于 **Python + FastAPI** 的轻量 Web 工具，包含十一个功能页面：

1. **字幕时间漂移校正**：粘贴 SRT 字幕，填入多组人工确认的「原时间 / 正确时间」
   锚点，按分段线性映射校正字幕时间，在网页预览并下载校正后的 SRT。
2. **字幕位置规划**（<http://localhost:8000/placement>）：粘贴 SRT，填写画面上方 /
   下方被遮挡（台标、弹幕条、滚动新闻等）的时间段与相邻字幕上下换位代价，自动为
   每条字幕选择总遮挡与换位代价最小的位置，并导出带 `line` 位置设置的 WebVTT。
3. **阅读负荷热区**（<http://localhost:8000/reading-load>）：粘贴 SRT，输入阅读
   速率阈值（字/秒）与热区最短持续时长（毫秒），按起止事件精确扫描时间轴，找出
   重叠字幕合计速率严格超标的热区，页面高亮并下载同一份结果 JSON。
4. **字幕专名统一**（<http://localhost:8000/terms>）：粘贴 SRT，按行输入专名的
   标准写法与别名，只扫描字幕正文各物理行，按最长别名优先、等长按输入顺序的规则
   从左到右一次替换，页面显示逐处替换清单并下载仅改正文命中片段的修订 SRT。
5. **字幕智能断行**（<http://localhost:8000/wrap>）：粘贴 SRT，输入每行最大显示
   列数与每条最多行数，对每条正文折叠原有换行 / 连续空白后，用动态规划在限制内
   最小化各行剩余列数的平方和（同分断点靠前），并遵守中英文禁则；排不下的字幕
   保留原正文并返回输入顺序号与原因，页面预览并下载同一份 SRT。
6. **字幕章节规划**（<http://localhost:8000/chapters>）：粘贴 SRT，填写章节数 K
   与优选停顿毫秒数 P，按输入顺序把字幕切成 K 个非空连续章节（仅在前条结束不晚于
   后条开始处允许切分），以整段总时长 / K 为目标最小化各章时长偏差平方和与各切分
   处停顿缺口平方和（同分切分点靠前），页面展示并下载同一份结果 JSON。
7. **字幕主题短片段检索**（<http://localhost:8000/topic-clip>）：粘贴 SRT，填写
   1～8 组关键词与各词所需出现的字幕条数（正整数），按 Unicode casefold 折叠比较
   （同一字幕每词至多计一次），沿输入顺序寻找覆盖各词所需条数的最短连续字幕窗口；
   片段起点取窗口首条开始、终点取窗口内最晚结束，全局按时长最短、起点最早、窗口
   起始输入序号最早选解，整体不足时返回无解与逐词缺少条数，页面展示并下载同一份
   结果 JSON。
8. **双语字幕配对**（<http://localhost:8000/bilingual-align>）：分别粘贴左右两份
   SRT（如中文 / 英文），按输入顺序做单调动态规划配对：允许 1:1、1:2、2:1 配对与
   单侧跳过，配对代价为两组时间跨度的起止差绝对值之和（合并每多一条加 500 毫秒、
   跳过每条加 3000 毫秒），同分按操作序列优先 1:1、1:2、2:1、跳过左、跳过右；
   页面展示每组序号、原文、跨度与代价及两侧未匹配条目，并下载 UTF-8 CSV。
9. **重叠字幕择优保留**（<http://localhost:8000/selection>）：粘贴 SRT，按字幕输入
   顺序（从 1 开始）设置正整数优先级（未设置为 1）、必须保留的序号与非负的最小间隔
   毫秒数；原时间轴不移动，仅保留相邻条目满足「前条结束 + 最小间隔 ≤ 后条开始」的
   子序列，在必须保留约束下最大化优先级总和，同分先取保留条数多、再取输入序号序列
   字典序小；必须保留条目彼此不相容时返回冲突序号。页面展示保留 / 舍弃条目与总优先级，
   并下载沿用原序号、原时间与原正文的保留 SRT。
10. **删段后字幕接轴**（<http://localhost:8000/cut-rejoin>）：粘贴 SRT，填写若干
    要从视频中删除的时间区间（毫秒精度）；先校验并合并重叠或相接的区间，再按保留
    时间轴裁切每条字幕——跨删除区间的字幕拆成多个片段、完全落入删除区间的字幕整条
    丢弃，所有保留片段按累计删除时长前移（片段时长不变），输出从 1 连续编号的 SRT
    与原序号到新序号的对应表，页面展示合并后区间、逐条裁切状态并下载接轴 SRT。
11. **镜头切点避让**（<http://localhost:8000/cut-avoid>）：粘贴 SRT，输入非负整数
    毫秒的镜头切点（可多个，自动去重升序，可留空）、0～2000 毫秒的每条字幕起点前后
    **最大挪动量**与非负的相邻字幕**最小间隔**；按输入顺序保留每条字幕的时长与正文，
    只在原起点前后最大挪动量内按整毫秒整体平移，且新起点不小于零。切点可与字幕开始 /
    结束边界重合但不得落在字幕内部，相邻字幕须满足「前条结束 + 最小间隔 ≤ 后条开始」；
    对全部字幕最小化起点挪动绝对值之和，同分取新起点序列字典序最小者，无解时指出最先
    无法继续安排的字幕输入序号。页面展示逐条原时间、新时间与偏移（前移负 / 后移正），
    并下载仅改时间轴的无损 SRT。

## 功能与校正规则（时间漂移）

- 粘贴标准 SRT，字幕**序号与文本原样保留**，仅校正开始/结束时间。
- **无损导出**：直接在原始 SRT 文本上逐条替换时间轴行的起止时间码，
  序号、字幕正文、箭头前后空格、`X1:`/`X2:`/`Y1:`/`Y2:`/`Position:` 等行尾
  定位参数、空行、原有换行（CRLF/LF 原样保留，不统一改写）以及文件开头的
  UTF-8 BOM 均保持不变；原文件无 BOM 时结果也不添加 BOM。
- 至少 **2 组**锚点（建议在片头、片中、片尾各取一组，越多越精确）。
- **相邻锚点之间做分段线性插值**；早于第一个锚点或晚于最后一个锚点的字幕，
  沿用最近一个区段（首段 / 末段）的斜率外推。
- 锚点**不自动排序**：原时间列必须严格递增，正确时间列也必须严格递增；
  任一侧出现相等或倒退时，拒绝生成并返回具体原因（指出是第几组锚点）。
- 时间格式 `HH:MM:SS,mmm`（毫秒分隔符逗号、句点均可，毫秒允许 1–3 位）。
- 校正后早于 `00:00:00,000` 的时间会截断为 0，并给出警告。
- 结果可在页面预览、逐条对比，并一键下载 `corrected.srt`：页面预览与下载
  使用接口返回的同一份 `corrected_srt`，下载文件开头**最多一个 BOM**
  （仅当原 SRT 带 BOM 时保留），为 UTF-8 编码。
- SRT 无法解析时接口仍返回 `422` 错误，并在 `detail.message` 中说明原因。

## 功能与规划规则（字幕位置）

- 输入 SRT（复用同一套字幕解析器）、画面**上方 / 下方各自的遮挡时间段**
  （`start` / `end`，`HH:MM:SS,mmm`，结束必须严格晚于开始）以及
  **相邻字幕上下换位代价** `switch_cost_ms`（非负整数，单位毫秒）。
- **同侧重叠遮挡时段先求并集**（首尾相接也合并），再按并集时段与每条字幕
  时间区间的交集累计该侧遮挡时长（毫秒）。
- 以 **总遮挡时长 + 相邻字幕上下换位代价最小** 为目标做动态规划；
  总代价完全相同时**按字幕顺序逐条优先选择下方**（避免字幕无必要地出现在上方）。
- 换位代价越大，规划越倾向让整段字幕保持同侧、减少上下跳动；填 `0` 表示
  换位零成本，每条字幕都选遮挡更短的一侧（同分时下方）。
- 导出 **WebVTT**（`WEBVTT` 文件头、时间码用句点 `.`），并在每条 cue 的
  设置中写入行位置：上方 `line:10%`、下方 `line:90%`，可直接用于支持 WebVTT
  `line` 设置的播放器。
- 返回逐条的上 / 下选择、两侧遮挡时长、所选位置的遮挡时长，以及
  **所选位置仍被遮挡的字幕列表** `still_occluded_cues`（两侧同时遮挡时出现），
  页面中高亮提示并可一键下载 `placement.vtt`。
- 遮挡时间段起止无效（格式错误或结束不晚于开始）、换位代价不是非负整数、
  SRT 无法解析时，接口均返回 `422`，并在 `detail.message` 中给出中文原因。

## 功能与分析规则（阅读负荷热区）

- 输入 SRT（复用同一套字幕解析器）、**正数阅读速率阈值** `rate_threshold`
  （字/秒，如 `5`、`4.5`）与**非负整数** `min_duration_ms`（热区最短持续毫秒数）。
- 每条字幕的阅读速率 = **去掉所有 Unicode 空白字符**（换行、制表符、全角空格
  `　` 等）后的字符数 ÷ 显示秒数（结束 − 开始）。
- 按每个字幕的开始 / 结束事件扫描**半开时间轴** `[start, end)`：区间内各活跃
  字幕的速率相加得到该区间合计速率；首尾相接（一条结束恰为另一条开始）**不算
  重叠**。全程使用毫秒整数与精确分数比较，**不做整秒采样**，毫秒级峰值也能检出。
- 找出合计速率**严格超过**阈值（等于阈值不算）的时间段，**首尾相接的超阈值
  分段自动合并**为热区，再保留连续时长达到 `min_duration_ms` 的热区（先合并、
  再按总时长过滤）。
- 逐段返回热区的起止时间（`HH:MM:SS,mmm`）、持续毫秒数、**峰值速率**（该热区
  内合计速率的最大值，字/秒）以及**参与字幕的输入顺序号**（按粘贴 SRT 中字幕
  出现位置从 1 开始编号，与字幕自身的 SRT 序号无关；多分段合并后升序去重）。
- 页面以时间轴色块、热区明细表展示结果，并在逐条字幕表中**高亮参与热区的字幕**；
  「下载结果 JSON」保存的就是接口返回的**同一份 JSON**（`reading-load.json`）。
- 阈值不是正数（含 `0`、负数、`NaN` / `Infinity`、布尔值、字符串）、最短时长
  不是非负整数、SRT 无法解析时，接口均返回 `422`（`detail.field` 指出字段，
  `detail.message` 给出中文原因；SRT 解析错误的 `detail.type` 为 `srt`）。

## 功能与统一规则（字幕专名统一）

- 输入 SRT（复用同一套字幕解析器）与若干条专名规则；每条规则包含一个
  **标准写法** `standard`（替换目标）与若干**别名** `aliases`（每行一个）。
- 规则**逐条输入**，按输入顺序参与等长同分裁决：同一位置多个别名等长时，
  取排在前面的规则；**空别名**（空串或仅含空白）与**同一别名指向不同标准
  写法**均拒绝生成，接口返回 `422` 并在 `detail.message` 中指出第几条规则
  或冲突别名（`detail.type` 为 `terms`）。同一别名在多条规则中指向同一个
  标准写法是允许的。
- **只扫描字幕正文的各物理行**：序号行与时间轴行（含 `X1:` / `Position:`
  等排版参数）不参与匹配；扫描**区分大小写**（`iPhone`、`IPHONE` 是不同
  别名）。
- 每一行**从左到右只对原文匹配一次**：同一位置优先匹配字符数**最长**的
  别名；长度相同按**规则输入顺序**取靠前者；命中后整段别名连同替换进去的
  标准写法一起跳过——**命中的替换文本不再参与匹配**（标准写法自身包含
  别名时也不会二次替换，更不会死循环）。
- 逐处返回命中的**字幕输入序号** `cue_order`（按粘贴 SRT 中字幕出现位置
  从 1 开始编号，与字幕自身的 SRT 序号无关）、该字幕正文内的**物理行号**
  `line_no`（从 1 开始）、**行内位置** `column`（该物理行中从 0 开始的
  字符位置）、命中的**原文** `source` 与**标准写法** `standard`。
- **无损导出**：直接在原始 SRT 物理行上替换命中片段，字幕序号、时间轴、
  `X1:`/`X2:`/`Y1:`/`Y2:`/`Position:` 等排版参数、块间空行、原有换行
  （CRLF/LF/CR 原样保留，不统一改写）以及文件开头的 UTF-8 BOM 均保持
  不变；**没有任何命中时修订 SRT 与输入逐字符一致**（原样返回）。
- 页面以替换清单表格（输入序号 / 正文行 / 行内位置 / 原文 → 标准写法）
  展示结果，并可一键下载 `revised.srt`：下载内容就是接口返回的同一份
  `revised_srt`（UTF-8，原 BOM / 换行风格保留）。规则非法或 SRT 无法
  解析时，接口返回 `422`。

## 功能与断行规则（字幕智能断行）

- 输入 SRT（复用同一套字幕解析器）、正整数 **每行最大显示列数**
  `max_columns` 与正整数 **每条字幕最多行数** `max_lines`。
- 对每条正文先做归一化：**原有换行与连续空白**（含制表符、全角空格等
  Unicode 空白）统一折叠为**单个半角空格**，并去掉首尾空白。
- **可断位置**：空格（断行后该空格不进入任何一行），或汉字 / 中文标点与
  任意字符的相邻处；**英文单词不拆开**（连续非汉字、非中文标点的字符，
  含单词内部标点，整体作为不可拆片段）。
- **显示列宽**：东亚宽（East Asian Width `W`）/ 全角（`F`）字符计 **2 列**
  （汉字、全角标点、全角字母等），其余可见字符计 **1 列**，规范组合类非 0
  的**组合字符（combining mark）计 0 列**（附加到前一个字符上），空格计 1 列。
- **禁则**：禁止**行首为闭标点**（如 `）。」』”，、；：！？` 等）、
  **行尾为开标点**（如 `（「『“《〈【` 等）。
- **动态规划**：在「行数不超过上限、每行宽度不超过最大列数」的所有可行断法
  中，最小化**各行剩余列数（最大列数 − 本行宽度）的平方和**；总代价相同时
  按**断点序列字典序靠前者**（第一个断点尽量靠前，再依次比较后续断点）。
- **排不下的字幕不改正文**，返回其**输入顺序号** `cue_order`（按粘贴 SRT 中
  字幕出现位置从 1 开始编号，与字幕自身的 SRT 序号无关）与中文原因：
  存在超宽且不可拆的片段（超长英文单词）、列数限制下最少行数超过上限，或
  禁则限制下无可行断法。
- **只改写成功字幕的正文**物理行：序号、时间轴、`X1:`/`X2:`/`Y1:`/`Y2:`/
  `Position:` 等定位参数、块间空行、原有换行（CRLF/LF/CR，断行产生的新
  换行沿用该字幕原有风格）以及文件开头的 UTF-8 BOM 均保持不变；没有任何
  正文需要改写时与输入逐字符一致（多个 BOM 也原样保留，有改写时最多保留
  一个 BOM）。
- 页面以逐字幕表格（输入序号 / SRT 序号 / 状态 / 断行结果与各行列数）展示，
  排不下的字幕红底高亮并列出原因；可一键下载 `wrapped.srt`：**页面预览与
  下载使用接口返回的同一份 `wrapped_srt`**（UTF-8，原 BOM / 换行风格保留）。
  参数不是正整数（含 `0`、负数、小数、布尔值、字符串）或 SRT 无法解析时，
  接口返回 `422`（`detail.field` 指出字段，SRT 错误的 `detail.type` 为 `srt`）。

## 功能与切分规则（字幕章节规划）

- 输入 SRT（复用同一套字幕解析器）、正整数 **章节数** `chapter_count`（K）与
  非负整数 **优选停顿毫秒数** `preferred_pause_ms`（P）。
- 按**输入顺序**把字幕切成 K 个**非空连续**章节；仅当相邻两条字幕满足
  「前一条结束时间不晚于后一条开始时间」时，二者之间才允许作为切分点
  （时间重叠或倒序的相邻字幕之间不可切）。
- 每章时长 = 章内首条开始时间 → 末条结束时间；目标时长 = 整段（首条开始 →
  末条结束）总时长 / K。在所有可行切法中**最小化总代价**：各章「时长 − 目标」
  的平方和 + 各切分处 `max(0, P − 停顿间隔)` 的平方和（停顿间隔 = 前条结束到
  后条开始的毫秒数，间隔达到 P 即不再惩罚）。
- 总代价完全相同时，取**切分点序列更靠前**的方案（第一个切分点尽量靠前，再
  依次比较后续切分点）。代价用整数精确计算（偏差项放大 K² 倍参与比较），
  不引入浮点误差。
- 逐章返回：章节号、起止时间（`HH:MM:SS,mmm`）、时长与目标偏差（毫秒）、
  **字幕输入顺序范围** `cue_order_from` / `cue_order_to`（按粘贴 SRT 中字幕
  出现位置从 1 开始编号，闭区间）与该章条数；逐切分点返回位置（第几条之后）、
  停顿间隔、停顿缺口 `max(0, P − 间隔)` 与其平方惩罚；并返回
  `total_duration_ms`、`target_duration_ms`、`duration_cost`、`pause_cost`
  与**总代价** `total_cost`（小数值均保留 3 位）。
- 页面以章节明细表、切分点明细表与 JSON 预览展示结果，「下载结果 JSON」保存
  的就是接口返回的**同一份 JSON**（`chapters.json`）。
- **无法切成 K 章时返回明确原因**（`422`，`detail.type` 为 `chapters`）：
  字幕条数少于 K（每章至少一条），或满足「前条结束不晚于后条开始」的切分
  位置不足 K−1 个（提示可切位置在哪些字幕之后）。参数不合法（K 不是正整数、
  P 不是非负整数）或 SRT 无法解析时同样返回 `422`（`detail.field` 指出字段，
  SRT 错误的 `detail.type` 为 `srt`）。

## 功能与检索规则（字幕主题短片段）

- 输入 SRT（复用同一套字幕解析器）与 **1～8 组**关键词，每组包含关键词
  `word` 与该词**所需出现的字幕条数** `required_count`（正整数）。
- 关键词先去首尾空白，再按 **Unicode `casefold` 折叠**比较（`Straße` 与
  `STRASSE` 折叠后同为 `strasse`，天然不区分英文大小写）；字幕正文同样
  `casefold` 后做子串判定。**空词**（空串或仅含空白，折叠后为空）与
  **折叠后重复**的词组均拒绝并返回 `422`，消息指出是第几组、与第几组重复。
- **同一条字幕内每个词至多计一次**：一条字幕里同一词出现多遍也只算 1 条命中；
  一条字幕可同时命中多个不同的词。
- 字幕开始时间必须**按输入顺序非递减**（相等允许，严格倒退拒绝）：不满足时
  返回 `422`（`detail.type` 为 `order`），并指出先倒退的那条**输入序号**
  （从 1 开始，与 SRT 自身序号无关，同时回显两侧 SRT 序号与开始时间）。
- 沿输入顺序寻找覆盖各词所需条数的**最短连续字幕窗口**（滑动窗口，每个左
  边界都对应其最早变得可行的右边界）；**片段起点取窗口首条的开始时间，终点
  取窗口内最晚结束**（窗口内字幕的结束时间不要求有序，按最大值计）。
- 选解顺序：**片段时长（最晚结束 − 首条开始）最短 → 起点最早 → 窗口起始
  输入序号最早**。
- 成功时返回 `found=true` 与片段：`start` / `end`（`HH:MM:SS,mmm`）、
  `duration_ms`、**字幕输入顺序范围** `cue_order_from` / `cue_order_to`
  （闭区间）与条数、逐词的 `required_count` / 窗口内 `hit_count` /
  `missing_count`，以及窗口内逐条字幕（输入序号、SRT 序号、起止时间、
  该条命中的关键词 `hit_words` 与文本）。
- **整体不足时返回 `found=false`**（HTTP 200，不作为错误）：`clip` 为 `null`，
  逐词给出全片命中条数 `hit_count` 与**缺少条数**
  `missing_count = max(0, 所需 − 全片命中)`。
- 页面以逐词命中表与片段字幕表（命中文本高亮、逐词标签）展示成功或无解结果，
  「下载结果 JSON」保存的就是接口返回的**同一份 JSON**（`topic-clip.json`）。
  词组数量越界、所需条数不是正整数、关键词非法、开始时间倒退或 SRT 无法解析
  时接口均返回 `422`（SRT 错误的 `detail.type` 为 `srt`）。

## 功能与配对规则（双语字幕配对）

- 分别粘贴**左右两份 SRT**（`left_srt` / `right_srt`，如中文 / 英文），两侧各自
  复用同一套字幕解析器；任一侧无法解析时返回 `422`（`detail.type` 为 `srt`），
  并在 `detail.message` 中以「左侧字幕：… / 右侧字幕：…」指明侧别与原因。
- 按**输入顺序**做**单调动态规划**配对（操作只向前推进、不交叉），允许五种操作：
  **1:1**、**1:2**（左一右二）、**2:1**（左二右一）配对，以及**跳过左**、
  **跳过右**（单侧字幕不配对）。
- **配对代价** = 两组时间跨度（组内首条开始 → 末条结束）的起止差绝对值之和，
  即 `|左组开始 − 右组开始| + |左组结束 − 右组结束|`；**合并每多一条加 500 毫秒**
  （1:2 / 2:1 即多一条，1:1 不加）；**跳过每条加 3000 毫秒**。
- 总代价相同时，按**从头到尾的操作序列**依次优先选择 **1:1、1:2、2:1、跳过左、
  跳过右**（操作优先级序列字典序最小者）。
- 逐组返回：操作步号 `step`、类型 `kind`、两侧字幕的**输入序号** `cue_order` 与
  **SRT 序号** `index`、**原文** `text`、两侧**跨度**（`left_start` / `left_end` /
  `right_start` / `right_end`）、起止差 `start_diff_ms` / `end_diff_ms`、合并代价
  `merge_penalty_ms` 与**组代价** `cost`；另返回**两侧未匹配条目**
  `unmatched_left` / `unmatched_right`（含各条跳过代价 3000 ms）以及
  `pair_cost` / `skip_cost` / `total_cost`。
- 页面以配对分组表与两侧未匹配表展示结果，并可预览、下载 **UTF-8 CSV**
  （`bilingual-align.csv`，带 BOM 便于 Excel 直接打开）：每行一个操作（配对组
  或单侧跳过），含换行 / 逗号 / 双引号的正文按 CSV 规则整体加引号、内部引号
  翻倍转义，多行正文不会破坏行列结构。

## 功能与择优规则（重叠字幕择优保留）

- 输入 SRT（复用同一套字幕解析器）、按**字幕输入顺序号** `cue_order`（从 1 开始，
  与 SRT 自身序号无关）逐条设置的**正整数优先级** `priorities`（未设置的条目按
  **1** 计）、**必须保留序号** `required_orders`（输入序号列表，可空）与非负整数
  **最小间隔毫秒数** `min_gap_ms`。
- **原时间轴不移动**：只做整条保留 / 舍弃，不改变任何条目的序号、开始 / 结束时间
  与正文；保留条目必须构成按输入顺序的子序列，且任意相邻保留条目满足
  **「前条结束 + 最小间隔 ≤ 后条开始」**（首尾相接恰好相等允许；开始时间倒退、
  时间重叠或间隔不足的两条不能相邻保留）。
- 在所有满足间隔约束与必须保留约束的子序列中，依次按以下标准选解：
  1. **保留条目的优先级总和最大**；
  2. 总和相同时**保留条数更多**；
  3. 仍相同时取**输入序号序列字典序更小**（第一个不同的序号尽量小）的方案。
- **必须保留条目彼此不相容时**（按输入序号相邻的两个必保条目不满足间隔条件，
  此时不存在任何可行子序列），接口返回 HTTP 200、`compatible: false` 与
  `conflicts` 冲突序号对列表（每对 `first` / `second` 为相邻必保输入序号），
  页面红字提示；序号超出字幕条数、重复设置优先级 / 必保序号、优先级不是正整数、
  最小间隔不是非负整数或 SRT 无法解析时返回 `422`（`detail.type` 为 `selection`
  或 `srt`）。
- 成功时（`compatible: true`）返回：逐条 `cues`（输入序号 `cue_order`、SRT 序号
  `index`、起止时间、`priority`、是否保留 `kept`、是否必保 `required` 与正文）、
  `kept_count` / `dropped_count` 与**保留条目优先级总和** `total_priority`，以及
  **保留 SRT** `selected_srt`：保留条目按原输入顺序整块序列化，**沿用原 SRT 序号、
  原时间码与原正文**（多行正文保留；标准 SRT 文本，UTF-8、`\\n` 换行）。
- 页面以绿 / 红双色逐条表格展示保留与舍弃条目（必保条目另加标记）及总优先级，
  「下载 selected.srt」保存的就是接口返回的同一份 `selected_srt`。

## 功能与接轴规则（删段后字幕接轴）

- 输入 SRT（复用同一套字幕解析器）与 **至少 1 段**要从视频中删除的时间区间
  `cuts`（`start` / `end`，`HH:MM:SS,mmm`，结束必须严格晚于开始）。
- **先校验并合并删除区间**：重叠（下一段开始 < 上一段结束）或**相接**
  （下一段开始 == 上一段结束）的区间合并为一个，合并后按开始时间升序、两两不交；
  接口回显合并后的区间 `merged_cuts` 与**累计删除时长** `total_removed_ms`。
- 字幕与删除区间都按**半开区间** `[start, end)` 处理，每条字幕区间减去删除区间
  并集后剩下的极大连续段即保留片段。**边界恰好相接不算重叠**：字幕结束恰为某
  删除区间的开始、或字幕开始恰为某删除区间的结束时，该字幕完整保留；字幕开始
  恰为删除开始（或结束恰为删除结束）时，该端点落入删除区间，相应一端被裁掉。
- **跨删除区间的字幕拆成多个片段**（每个保留片段一条输出字幕，正文随片段各自
  完整保留，含多行正文）；**完全落入删除区间的字幕整条丢弃**（无任何保留片段）。
- **所有保留片段按累计删除时长前移**：片段内部不含删除区间，前后端点前移量相同
  （均为该片段之前被删除的总时长），故**片段时长不变**、整体平移；**删除起点为
  `00:00:00,000`** 时片头内容被删除，后续内容自然前移到 0。
- 输出 `rejoined_srt`：全部保留片段按原字幕输入顺序、片段时间顺序**从 1 连续
  编号**重新序列化（标准 SRT，UTF-8、`\n` 换行；全部丢弃时为空串）。
- 同时返回**原序号到新序号的对应表** `mapping`：每条输入字幕（输入序号
  `cue_order` 从 1 开始，回显 SRT 自身序号 `index`）对应的新序号列表
  `new_orders`——拆分条目对应多个新序号，丢弃条目对应空列表。
- 逐条返回 `cues`：输入序号、SRT 序号、原起止时间、裁切状态 `status`
  （`kept` 完整保留 / `trimmed` 裁切后单段 / `split` 拆成多段 / `dropped`
  丢弃）、各保留片段的原区间与接轴后区间及新序号，以及 `cue_count` /
  `kept_cue_count` / `dropped_cue_count` / `segment_count` 统计。
- 页面以逐条表格（状态分色高亮：保留 / 裁切 / 拆分 / 丢弃）、合并后区间表与
  对应表展示结果，「下载 rejoined.srt」保存的就是接口返回的同一份
  `rejoined_srt`。删除区间为空列表、区间起止无效（格式错误或结束不晚于开始）、
  SRT 无法解析时，接口均返回 `422`（区间错误 `detail.type` 为 `cuts`，
  SRT 错误为 `srt`，字段校验错误由 `detail.field` 指出字段）。

## 功能与避让规则（镜头切点避让）

- 输入 SRT（复用同一套字幕解析器）、若干**镜头切点** `cut_points`（非负整数
  毫秒，列表可空；自动去重并按毫秒升序处理）、非负整数 **最大挪动量**
  `max_move_ms`（0～2000 毫秒，含两端）与非负整数 **相邻字幕最小间隔**
  `min_gap_ms`。
- **每条字幕的时长与正文保持不变**，只做整体平移：新开始时间 `s` 只能取整毫秒，
  且满足 `max(0, 原开始 − max_move_ms) ≤ s ≤ 原开始 + max_move_ms`
  （**新起点不得小于零**；时长不变，故新结束也必为正）。
- **切点不得落在字幕内部**：对每个切点 `c`，不允许
  `s < c < s + 原时长`；等价地，新起点不得落入整数禁区间
  `[c − 原时长 + 1, c − 1]`。切点与字幕边界重合是允许的：`s == c`（切点恰为
  字幕开始）或 `s + 原时长 == c`（切点恰为字幕结束）都不冲突。
- **相邻字幕**按输入顺序满足「前条新结束 + `min_gap_ms` ≤ 后条新开始」
  （首尾相接、间隔为 0 允许）；该约束只作用于输入顺序相邻的两条。
- 在全部可行安排中依次选解：
  1. **最小化各条开始挪动绝对值之和** `Σ|s − 原开始|`；
  2. 总和相同时取**新起点序列字典序最小**（第一个不同的起点尽量小，再依次
     比较后续）。实现为按字幕顺序的动态规划：每条字幕的候选起点窗口至多
     `2·max_move_ms+1` 个整毫秒，用切点禁区间在窗口上做掩码，后向 DP 行的
     后缀最小值承接相邻间隔约束，自后而前求最优尾代价，再自前而后在剩余预算
     内取最小可行起点还原字典序最小的最优序列。
- **无解时返回 HTTP 200、`feasible: false`**，并给出**最先无法继续安排的字幕
  输入序号** `blocked_order`（从 1 开始）：按最早可行起点贪心安排前序字幕
  （给后续留出最大空间）后，该条在其挪动窗口与相邻间隔约束内不存在任何不含
  切点的起点；贪心失败即前 `blocked_order` 条整体无解。此时 `adjusted_srt`
  为空串、`cues` 为空数组、`total_shift_ms` 为 0。
- 成功时（`feasible: true`）返回去重升序的 `cut_points`、回显参数、
  `cue_count`、**起点挪动绝对值之和** `total_shift_ms`、逐条 `cues`
  （输入序号 `cue_order`、SRT 序号 `index`、原起止 `original_start` /
  `original_end`、新起止 `start` / `end`（均为 `HH:MM:SS,mmm`）、**偏移**
  `shift_ms = 新开始 − 原开始`（负数前移、0 不动、正数后移）与正文 `text`），
  以及 **`adjusted_srt`**：复用现有无损重写，**只替换各时间轴行的起止时间码**
  （新结束 = 新起点 + 原时长），序号、正文、箭头空格、`X1:`/`Position:` 等
  定位参数、空行、CRLF/LF/CR 原有换行风格及开头 BOM 一律保持原样。
- 页面以逐条对照表（原时间轴 → 新时间轴、偏移按前移 / 后移分色）展示结果并可
  一键下载 `adjusted.srt`（页面预览与下载为接口返回的同一份 `adjusted_srt`）；
  无解时红字指出最先无法安排的字幕序号且不提供下载。切点含负数 / 非整数、
  最大挪动量不在 0～2000、最小间隔为负、SRT 无法解析时，接口均返回 `422`
  （`detail.field` 指出字段，SRT 错误的 `detail.type` 为 `srt`）。

## 一条命令启动（Docker Compose）

```bash
docker compose up -d --build
```

启动后访问：

- 时间漂移校正界面：<http://localhost:8000/>
- 字幕位置规划界面：<http://localhost:8000/placement>
- 阅读负荷热区界面：<http://localhost:8000/reading-load>
- 字幕专名统一界面：<http://localhost:8000/terms>
- 字幕智能断行界面：<http://localhost:8000/wrap>
- 字幕章节规划界面：<http://localhost:8000/chapters>
- 字幕主题短片段检索界面：<http://localhost:8000/topic-clip>
- 双语字幕配对界面：<http://localhost:8000/bilingual-align>
- 重叠字幕择优保留界面：<http://localhost:8000/selection>
- 删段后字幕接轴界面：<http://localhost:8000/cut-rejoin>
- 镜头切点避让界面：<http://localhost:8000/cut-avoid>
- 时间漂移接口：`POST` <http://localhost:8000/api/correct>
- 位置规划接口：`POST` <http://localhost:8000/api/placement>
- 阅读负荷接口：`POST` <http://localhost:8000/api/reading-load>
- 专名统一接口：`POST` <http://localhost:8000/api/terms>
- 智能断行接口：`POST` <http://localhost:8000/api/wrap>
- 章节规划接口：`POST` <http://localhost:8000/api/chapters>
- 主题片段检索接口：`POST` <http://localhost:8000/api/topic-clip>
- 双语字幕配对接口：`POST` <http://localhost:8000/api/bilingual-align>
- 重叠字幕择优保留接口：`POST` <http://localhost:8000/api/selection>
- 删段后字幕接轴接口：`POST` <http://localhost:8000/api/cut-rejoin>
- 镜头切点避让接口：`POST` <http://localhost:8000/api/cut-avoid>
- 健康检查：<http://localhost:8000/health>
- 接口文档（FastAPI 自带）：<http://localhost:8000/docs>

停止：`docker compose down`

### 配置

必要配置只有宿主机映射端口（容器内固定监听 8000，无需其它环境变量）：

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| 端口 | 通过环境变量 `HOST_PORT` 修改宿主机映射端口；容器内固定监听 8000 | `8000` |
| 重启策略 | `restart: unless-stopped`（见 `docker-compose.yml`） | — |

例如改用宿主机 9000 端口：

```bash
HOST_PORT=9000 docker compose up -d --build
# 访问 http://localhost:9000/ 、http://localhost:9000/placement 、
# http://localhost:9000/reading-load 、http://localhost:9000/terms 、
# http://localhost:9000/wrap 、http://localhost:9000/chapters 、
# http://localhost:9000/topic-clip 、http://localhost:9000/bilingual-align 、
# http://localhost:9000/selection 、http://localhost:9000/cut-rejoin
# 与 http://localhost:9000/cut-avoid
```

## 不用 Docker，直接运行（Python 3.10+）

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

同样访问 <http://localhost:8000/>、<http://localhost:8000/placement>、
<http://localhost:8000/reading-load>、<http://localhost:8000/terms>、
<http://localhost:8000/wrap>、<http://localhost:8000/chapters>、
<http://localhost:8000/topic-clip>、<http://localhost:8000/bilingual-align>、
<http://localhost:8000/selection>、<http://localhost:8000/cut-rejoin>
与 <http://localhost:8000/cut-avoid>。

## HTTP 接口

### `POST /api/correct`

```json
{
  "srt": "1\n00:00:05,000 --> 00:00:08,000\n你好\n",
  "anchors": [
    {"original": "00:00:10,000", "corrected": "00:00:08,000"},
    {"original": "00:02:40,000", "corrected": "00:02:35,000"}
  ]
}
```

成功返回 `corrected_srt`（与输入同构的完整 SRT 文本：只替换各时间轴行的起止
时间码，序号、正文、定位参数、空行、CRLF/LF 换行及 BOM 原样保留）、`cues`
（逐条前后时间对照）与 `warnings`（如时间截断等提示）。SRT 解析失败或锚点不
满足严格递增时返回 `422`，并在 `detail.message` 中给出中文原因。

### `POST /api/placement`

```json
{
  "srt": "1\n00:00:05,000 --> 00:00:09,000\n第一句\n\n2\n00:00:14,000 --> 00:00:18,000\n第二句\n",
  "top_blocks": [
    {"start": "00:00:12,000", "end": "00:00:20,000"}
  ],
  "bottom_blocks": [
    {"start": "00:00:24,000", "end": "00:00:32,000"}
  ],
  "switch_cost_ms": 2000
}
```

`top_blocks` / `bottom_blocks` 均可省略或传空数组（表示该侧全程无遮挡）。
成功返回：

- `vtt`：带 `line:10%` / `line:90%` 行位置设置的完整 WebVTT 文本；
- `cues`：逐条字幕的 `position`（`top` / `bottom`）、`top_occluded_ms`、
  `bottom_occluded_ms`、所选位置遮挡 `occluded_ms` 及 `still_occluded` 标记；
- `still_occluded_cues`：所选位置仍被遮挡的字幕（两侧同时遮挡时才可能出现）；
- `merged_top_blocks` / `merged_bottom_blocks`：两侧遮挡求并集后的时间段
  （`HH:MM:SS,mmm`），便于页面核对；
- `total_occluded_ms`：所选位置的遮挡总毫秒数；`switch_count`：上下换位次数。

时间段起止无效或 `switch_cost_ms` 不是非负整数时返回 `422`
（后者为请求体校验错误，前者与 SRT 解析错误在 `detail.message` 中给出中文原因）。

### `POST /api/reading-load`

```json
{
  "srt": "1\n00:00:00,000 --> 00:00:02,000\n一二三四五六\n\n2\n00:00:01,000 --> 00:00:03,000\n四五六\n",
  "rate_threshold": 4,
  "min_duration_ms": 500
}
```

`rate_threshold` 为字/秒正数（整数或小数均可，按十进制精确比较），
`min_duration_ms` 为非负整数毫秒。成功返回：

- `cues`：逐条字幕的输入顺序号 `order`、SRT 序号 `index`、起止时间、
  `char_count`（去 Unicode 空白后的字符数）、`duration_ms` 与 `rate`（字/秒）；
- `zones`：热区列表，每项包含 `start` / `end`（`HH:MM:SS,mmm`）、
  `duration_ms`、`peak_rate`（峰值合计速率，字/秒）与 `cue_orders`
  （参与字幕的输入顺序号，升序去重）；
- `rate_threshold` / `min_duration_ms`（回显参数）、`cue_count`、`zone_count`。

合计速率**严格超过**阈值的相接分段先合并，再按 `duration_ms >= min_duration_ms`
过滤；没有热区时 `zones` 为空数组。参数不合法或 SRT 解析失败返回 `422`。

### `POST /api/terms`

```json
{
  "srt": "1\r\n00:00:01,000 --> 00:00:03,000\r\n他新买的 IPHONE 到货了\r\n",
  "rules": [
    {"standard": "iPhone", "aliases": ["IPHONE", "iphone"]},
    {"standard": "北京大学", "aliases": ["Peking University", "北大"]}
  ]
}
```

`rules` 可省略或传空数组（不做任何替换，原文原样返回）。成功返回：

- `revised_srt`：与输入同构的完整 SRT 文本，只替换正文命中片段，序号、时间轴、
  行尾排版参数、空行、CRLF/LF/CR 换行及开头 BOM 原样保留；**没有命中时与输入
  逐字符一致**；
- `hits`：每处命中的 `cue_order`（字幕输入序号，从 1 开始）、`line_no`（该
  字幕正文内从 1 开始的物理行号）、`column`（行内从 0 开始的字符位置）、
  `source`（命中的原文别名）与 `standard`（标准写法）；
- `hit_count`：命中总数（未命中为 `0`，`hits` 为空数组）。

匹配区分大小写，仅扫描正文物理行；每个位置最长别名优先、等长按规则输入顺序，
命中片段与其替换文本整体跳过、不回扫。包含空别名（空串或纯空白）或同一别名
指向不同标准写法时返回 `422`（`detail.type` 为 `terms`）；SRT 无法解析时
`detail.type` 为 `srt`。

### `POST /api/wrap`

```json
{
  "srt": "1\r\n00:00:01,000 --> 00:00:04,000\r\n这是一条比较长的中文字幕，需要智能断行\r\n",
  "max_columns": 16,
  "max_lines": 2
}
```

`max_columns` / `max_lines` 均为正整数。成功返回：

- `wrapped_srt`：与输入同构的完整 SRT 文本，只改写成功字幕的正文物理行，
  序号、时间轴、行尾定位参数、空行、CRLF/LF/CR 换行及开头 BOM 原样保留；
  没有任何正文需要改写时与输入逐字符一致；
- `cues`：逐条字幕结果，含 `cue_order`（输入顺序号，从 1 开始）、`index`
  （SRT 自身序号）、`success`、`changed`（正文是否实际被改写）、
  `original_text`、`lines`（每行 `text` 与显示宽度 `width`）与失败时的
  `reason`；
- `failures`：排不下、保留原正文的字幕列表（`cues` 中 `success=false`
  项的子集）；
- `cue_count` / `wrapped_count`（成功条数）/ `failed_count`（排不下条数）
  / `changed_count`（正文实际改写条数）。

每条正文先把原有换行与连续空白折叠为单个空格，只在空格或汉字 / 中文标点
相邻处断行（英文单词不拆）；东亚宽 / 全角字符计 2 列、其他可见字符计 1 列、
组合字符计 0 列；禁止行首为闭标点、行尾为开标点；动态规划最小化各行剩余
列数的平方和，同分时断点序列靠前。排不下的三类原因（不可拆片段超宽、最少
行数超限、禁则下无可行断法）均在对应字幕的 `reason` 中说明。参数不是
正整数或 SRT 无法解析时返回 `422`。

### `POST /api/chapters`

```json
{
  "srt": "1\n00:00:00,000 --> 00:00:03,000\n第一句\n\n2\n00:00:03,500 --> 00:00:06,000\n第二句\n",
  "chapter_count": 2,
  "preferred_pause_ms": 800
}
```

`chapter_count` 为正整数（章节数 K），`preferred_pause_ms` 为非负整数毫秒
（优选停顿 P）。成功返回：

- `chapters`：逐章的 `order`、`start` / `end`（`HH:MM:SS,mmm`）、
  `duration_ms`、`deviation_ms`（时长 − 目标，毫秒）、`cue_order_from` /
  `cue_order_to`（字幕输入顺序范围，从 1 开始的闭区间）与 `cue_count`；
- `splits`：逐切分点的 `after_cue_order` / `before_cue_order`、`gap_ms`
  （停顿间隔）、`pause_shortfall_ms`（`max(0, P − 间隔)`）与 `penalty`
  （缺口平方）；
- `total_duration_ms` / `target_duration_ms`（整段总时长与目标时长，毫秒）、
  `duration_cost`（各章时长偏差平方和）、`pause_cost`（各切分处停顿缺口
  平方和）与 `total_cost`（总代价，两者之和）；小数值保留 3 位；
- `chapter_count` / `preferred_pause_ms`（回显参数）与 `cue_count`。

切分只发生在「前条结束不晚于后条开始」的相邻字幕之间，同分取切分点序列
更靠前者。无法切成 K 章（字幕不足 K 条，或合法切分点不足 K−1 个）时返回
`422`（`detail.type` 为 `chapters`，`detail.message` 给出具体原因）；
参数不合法或 SRT 解析失败同样返回 `422`。

### `POST /api/topic-clip`

```json
{
  "srt": "1\n00:00:00,000 --> 00:00:03,000\n人工智能新进展\n\n2\n00:00:03,000 --> 00:00:06,000\n大模型发布\n",
  "keywords": [
    {"word": "人工智能", "required_count": 1},
    {"word": "模型", "required_count": 1}
  ]
}
```

`keywords` 为 1～8 组，`word` 为关键词（去首尾空白后按 Unicode casefold
折叠比较），`required_count` 为正整数。成功检索到时返回 `found: true`：

- `clip`：`start` / `end`（窗口首条开始 → 窗口内最晚结束，
  `HH:MM:SS,mmm`）、`duration_ms`、`cue_order_from` / `cue_order_to`
  （字幕输入顺序范围，从 1 开始的闭区间）与 `cue_count`；
- `clip.word_hits`（顶层 `keywords` 为同一份）：逐词的 `word`、
  `required_count`、窗口内命中条数 `hit_count`（同一字幕每词至多计一次）
  与 `missing_count`；
- `clip.cues`：窗口内逐条字幕的输入序号 `cue_order`、SRT 序号 `index`、
  起止时间、`hit_words`（该条命中的关键词列表）与 `text`；
- `cue_count`：全片字幕条数。

**整体不足时返回 HTTP 200、`found: false`、`clip: null`**，`keywords`
中逐词给出全片 `hit_count` 与缺少条数 `missing_count`
（`max(0, 所需 − 全片命中)`）。

全局选解按「片段时长最短 → 起点最早 → 窗口起始输入序号最早」。空词、
折叠后与更早词组重复、词组数量不在 1～8、`required_count` 不是正整数时
返回 `422`（`detail.type` 为 `topic` 或字段校验）；开始时间未非递减时
`detail.type` 为 `order`，消息指出先倒退的输入序号；SRT 解析失败时
`detail.type` 为 `srt`。

### `POST /api/bilingual-align`

```json
{
  "left_srt": "1\n00:00:01,000 --> 00:00:04,000\n这是一个比较长的句子\n",
  "right_srt": "1\n00:00:01,000 --> 00:00:02,000\nThis is\n\n2\n00:00:02,000 --> 00:00:04,000\na longer sentence\n"
}
```

`left_srt` / `right_srt` 为左右两份 SRT 全文（均必填）。成功返回：

- `pairs`：逐配对组（按操作顺序），每组含 `step`、`kind`（`1:1` / `1:2` /
  `2:1`）、两侧字幕列表 `left` / `right`（各条含输入序号 `cue_order`、SRT 序号
  `index`、起止时间与 `text`）、两侧跨度 `left_start` / `left_end` /
  `right_start` / `right_end`、`start_diff_ms` / `end_diff_ms` /
  `merge_penalty_ms` 与组代价 `cost`；
- `unmatched_left` / `unmatched_right`：两侧被跳过（未匹配）的字幕，含 `step`
  与每条 `skip_cost_ms`（3000）；
- `pair_cost` / `skip_cost` / `total_cost`：配对代价、跳过代价与总代价；
- `left_count` / `right_count` / `pair_count` / `unmatched_left_count` /
  `unmatched_right_count` 与回显的 `merge_penalty_ms`（500）/
  `skip_penalty_ms`（3000）。

配对为单调动态规划：允许 1:1、1:2、2:1 与单侧跳过；配对代价为两组时间跨度
（组内首条开始 → 末条结束）的起止差绝对值之和，合并每多一条加 500 毫秒，
跳过每条加 3000 毫秒；总代价相同时按操作序列优先 1:1、1:2、2:1、跳过左、
跳过右。任一侧 SRT 解析失败返回 `422`（`detail.type` 为 `srt`，
`detail.message` 以「左侧字幕：… / 右侧字幕：…」指明侧别）。

### `POST /api/selection`

```json
{
  "srt": "1\n00:00:00,000 --> 00:00:04,000\n第一句\n\n2\n00:00:03,000 --> 00:00:07,000\n重叠句\n",
  "priorities": [{"cue_order": 2, "priority": 10}],
  "required_orders": [1],
  "min_gap_ms": 0
}
```

`priorities` 为 `{cue_order, priority}` 列表（均可省略或传空数组）：
`cue_order` 为字幕输入序号（从 1 开始），`priority` 为正整数，未列出的条目
优先级按 1 计；同一序号重复设置返回 `422`。`required_orders` 为必须保留条目的
输入序号数组（正整数，可空，重复序号返回 `422`）；`min_gap_ms` 为非负整数毫秒。

成功且必保条目相容时返回 `compatible: true`：

- `selected_srt`：保留条目按原顺序整块序列化的 SRT，**沿用原 SRT 序号、原时间码
  与原正文**（不移动时间轴，舍弃条目整块删除，多行正文保留）；
- `cues`：全量逐条结果，含输入序号 `cue_order`、SRT 序号 `index`、起止时间
  （`HH:MM:SS,mmm`）、`priority`、`kept`（是否保留）、`required`（是否必保）
  与 `text`；
- `kept_count` / `dropped_count` / `cue_count` 与保留条目优先级总和
  `total_priority`，并回显 `min_gap_ms`；`conflicts` 为空数组。

**必须保留条目彼此不相容时返回 HTTP 200、`compatible: false`**：`selected_srt`
为空串、`cues` 为空数组，`conflicts` 列出相邻冲突对（每对含必保输入序号
`first` / `second`，即前条结束 + 最小间隔晚于后条开始）。选解顺序为「优先级总和
最大 → 保留条数最多 → 输入序号序列字典序最小」。序号越界、优先级 / 必保序号
重复、优先级非正整数、最小间隔非非负整数或 SRT 解析失败时返回 `422`
（参数错误 `detail.type` 为 `selection`，SRT 错误为 `srt`）。

### `POST /api/cut-rejoin`

```json
{
  "srt": "1\n00:00:00,000 --> 00:00:04,000\n开场白\n\n2\n00:00:04,000 --> 00:00:12,000\n横跨删除区间的一句\n\n3\n00:00:12,000 --> 00:00:16,000\n结尾\n",
  "cuts": [
    {"start": "00:00:06,000", "end": "00:00:08,000"},
    {"start": "00:00:08,000", "end": "00:00:10,000"}
  ]
}
```

`cuts` 为至少 1 段删除区间（`start` / `end`，`HH:MM:SS,mmm`，结束必须严格
晚于开始）；重叠或相接的区间先合并（上例合并为 `00:00:06,000` →
`00:00:10,000`）。成功返回：

- `rejoined_srt`：保留片段按累计删除时长前移后**从 1 连续编号**的完整 SRT
  （上例第 2 条被拆成 `00:00:04,000 --> 00:00:06,000` 与
  `00:00:06,000 --> 00:00:08,000` 两段，第 3 条前移为
  `00:00:08,000 --> 00:00:12,000`；全部丢弃时为空串）；
- `merged_cuts`：合并后的删除区间（`start` / `end` / `duration_ms`）与
  `total_removed_ms`（累计删除毫秒数）；
- `cues`：逐条字幕的输入序号 `cue_order`、SRT 序号 `index`、原起止时间、
  裁切状态 `status`（`kept` / `trimmed` / `split` / `dropped`）、各保留片段
  （`new_order`、原区间 `original_start` / `original_end` 与接轴后区间
  `start` / `end`）与正文 `text`；
- `mapping`：原序号到新序号的对应表（`cue_order` / `index` /
  `new_orders`，拆分条目对应多个新序号，丢弃条目为空列表）；
- `cue_count` / `kept_cue_count` / `dropped_cue_count` / `segment_count`。

边界按半开区间处理：字幕结束恰为删除开始、或字幕开始恰为删除结束时完整保留；
删除起点为 `00:00:00,000` 时后续内容前移到 0。删除区间为空列表、区间起止无效
或 SRT 解析失败时返回 `422`（区间错误 `detail.type` 为 `cuts`，SRT 错误为
`srt`）。

### `POST /api/cut-avoid`

```json
{
  "srt": "1\n00:00:05,000 --> 00:00:07,000\n开场字幕\n\n2\n00:00:08,000 --> 00:00:10,000\n第二句\n",
  "cut_points": [7000, 9000, 17000, 25000],
  "max_move_ms": 2000,
  "min_gap_ms": 0
}
```

`cut_points` 为镜头切点的非负整数毫秒数组（可省略或传空数组；自动去重并按
时间升序处理），`max_move_ms` 为每条字幕起点前后允许的最大挪动量（0～2000 的
整数毫秒），`min_gap_ms` 为相邻字幕最小间隔（非负整数毫秒）。每条字幕保持
原时长（新结束 = 新开始 + 原时长）与正文，仅整体平移；切点可与字幕边界
重合但不得落在字幕内部。成功时返回 `feasible: true`：

- `adjusted_srt`：与输入同构的完整 SRT 文本，复用无损重写，只替换各时间轴
  行的起止时间码，序号、正文、行尾定位参数、空行、CRLF/LF/CR 换行及开头
  BOM 原样保留；
- `cues`：逐条字幕的输入序号 `cue_order`、SRT 序号 `index`、原起止
  `original_start` / `original_end`、新起止 `start` / `end`
  （`HH:MM:SS,mmm`）、偏移 `shift_ms`（新开始 − 原开始，负前移、正后移）
  与正文 `text`；
- `cut_points`（去重升序回显）、`max_move_ms` / `min_gap_ms`（回显参数）、
  `cue_count` 与起点挪动绝对值之和 `total_shift_ms`；`blocked_order` 为
  `null`。

**无解时返回 HTTP 200、`feasible: false`**：`blocked_order` 指出最先无法
继续安排的字幕输入序号（按最早可行起点贪心安排前序字幕后，该条在挪动窗口与
相邻间隔内找不到任何不含切点的起点），`adjusted_srt` 为空串、`cues` 为空
数组、`total_shift_ms` 为 0。切点含负数 / 非整数、最大挪动量不在 0～2000、
最小间隔为负时返回 `422`（`detail.field` 分别指出 `cut_points` /
`max_move_ms` / `min_gap_ms`），SRT 解析失败时 `detail.type` 为 `srt`。

## 目录结构

```
.
├── app/
│   ├── main.py           # FastAPI 应用、/api/correct、/api/placement、/api/reading-load、/api/terms、/api/wrap、/api/chapters、/api/topic-clip、/api/bilingual-align、/api/selection、/api/cut-rejoin 与 /api/cut-avoid 接口
│   ├── mapping.py        # 锚点校验与分段线性映射
│   ├── placement.py      # 遮挡时段并集、遮挡时长统计、位置规划 DP、WebVTT 导出
│   ├── reading_load.py   # 字 / 秒速率、半开时间轴事件扫描、热区合并与最短时长过滤
│   ├── terms.py          # 专名规则校验、最长优先一次扫描与正文无损替换
│   ├── linewrap.py       # 列宽计量、原子切分、断行禁则、平方剩余 DP 与正文无损重写
│   ├── chapters.py       # 章节切分可行性校验与均衡切分 DP（时长偏差平方 + 停顿缺口平方）
│   ├── topic_clip.py     # 关键词 casefold 命中、开始时间非递减校验与最短连续窗口滑动检索
│   ├── bilingual.py      # 双语字幕单调配对 DP（1:1 / 1:2 / 2:1 与单侧跳过、操作序列同分裁决）
│   ├── selection.py      # 重叠字幕择优保留 DP（优先级 / 条数 / 序号字典序三级编码加权区间调度）
│   ├── cutrejoin.py      # 删除区间校验合并、字幕裁切拆分与按累计删除时长前移接轴
│   ├── cutavoid.py       # 镜头切点校验、最早可行起点可行性贪心与切点避让 DP（绝对值挪动量 + 起点序列字典序）
│   ├── srt.py            # SRT 解析、正文行定位与无损重写（保留原格式）
│   ├── timecode.py       # 时间码解析与格式化（SRT / WebVTT）
│   └── static/
│       ├── index.html         # 时间漂移校正页面（原生 HTML/CSS/JS）
│       ├── placement.html     # 字幕位置规划页面（原生 HTML/CSS/JS）
│       ├── reading_load.html  # 阅读负荷热区页面（原生 HTML/CSS/JS）
│       ├── terms.html         # 字幕专名统一页面（原生 HTML/CSS/JS）
│       ├── wrap.html          # 字幕智能断行页面（原生 HTML/CSS/JS）
│       ├── chapters.html      # 字幕章节规划页面（原生 HTML/CSS/JS）
│       ├── topic_clip.html    # 字幕主题短片段检索页面（原生 HTML/CSS/JS）
│       ├── bilingual_align.html  # 双语字幕配对页面（原生 HTML/CSS/JS）
│       ├── selection.html     # 重叠字幕择优保留页面（原生 HTML/CSS/JS）
│       ├── cut_rejoin.html    # 删段后字幕接轴页面（原生 HTML/CSS/JS）
│       └── cut_avoid.html     # 镜头切点避让页面（原生 HTML/CSS/JS）
├── tests/               # 核心算法与接口测试
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
