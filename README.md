# caixin-cli

命令行工具，抓取 [财新网](https://www.caixin.com/) 文章和《财新周刊》整期内容，保存为 Markdown（含配图）。

> ⚠️ **仅供个人存档，禁止转载**
>
> 本工具用于已订阅用户离线阅读/存档自己已付费的内容。财新网所有内容版权归 **财新传媒** 所有。
> **抓取的内容仅限个人自用，不得转载、传播、商用或公开发布。**
> 使用本工具即表示你已订阅相关内容，并遵守财新网服务条款与中华人民共和国著作权法。作者不对滥用本工具导致的任何后果负责。

---

## 功能

- `caixin article <url>` - 抓单篇文章（自动处理多页 `?p1…?pN`）
- `caixin weekly <期号>` - 抓整期《财新周刊》全部文章，按栏目分组
- `caixin latest` - 抓取（或仅列出）最新一期
- `caixin weekly-list` - 列出往期（总期号 / 年度期号 / 出版日期 / 封面标题）
- `caixin search <关键词>` - 搜索财新文章（经搜狗 `site:caixin.com`）；`--fetch` 搜到即下载
- `caixin channel <频道>` - 列出/抓取某频道（板块）最新文章（经济/金融/公司/政经/世界/观点/科技/…）
- 付费全文经 **登录 Cookie + 无头浏览器（Edge/Chrome）** 渲染获取
- 输出 Markdown：YAML 元信息 + 正文 + 本地下载的配图

## 环境要求

- Python 3.11+
- 一个 Chromium 内核浏览器（Microsoft Edge 或 Google Chrome）
- 依赖：`httpx` `beautifulsoup4` `lxml` `markdownify` `typer` `rich` `python-slugify` `playwright`

## 安装

```bash
git clone https://github.com/Youwillrememberme/caixin-cli.git caixin
cd caixin
pip install -e .
# Edge/Chrome 已装即可；否则装一次 Chromium：
python -m playwright install chromium
```

## 配置 Cookie（抓付费全文必需）

财新正文需登录态。抓一次你的登录 Cookie 存到配置文件，之后所有命令自动带上：

1. 浏览器登录 [caixin.com](https://www.caixin.com/)，打开任意付费文章 -> `F12` -> Network -> 刷新 -> 找一条请求 -> 复制请求头里整行 `Cookie:` 的值（不要 `Cookie:` 这个词本身）。
2. 存到 `~/.caixin/config.toml`：

```toml
[auth]
cookie = "粘贴你的整段 Cookie"

[output]
dir = "caixin-downloads"
delay = 1.0
```

3. 验证：`caixin weekly-list`（能列出往期即配置生效）。

也可用 `--cookie '...'`、`--cookie-file 文件` 或环境变量 `CAIXIN_COOKIE`。
无 Cookie 时仅拿到摘要（会标注 `paywalled: true`）。

> Cookie 是登录凭证，妥善保管；**不要**把 Cookie 提交进 git（已在 `.gitignore` 排除）。失效后重新抓一次更新即可。

## 用法

```bash
# 单篇文章（全文）
caixin article https://weekly.caixin.com/2026-07-11/102463000.html

# 整期周刊（四种写法均可）
caixin weekly latest                  # 最新一期
caixin weekly cw1214                  # cw + 总期号
caixin weekly 1214                    # 总期号
caixin weekly 2026-27                 # 年-期
caixin weekly https://weekly.caixin.com/2026/cw1214/   # 完整 URL

# 只看目录、不下载
caixin weekly latest --list-only
caixin weekly cw1214 --section 金融 --list-only   # 只看某栏目

# 列出往期
caixin weekly-list
caixin weekly-list --year 2025 --limit 10

# 搜索
caixin search "AI 芯片" --limit 10
caixin search "锂电" --fetch            # 搜到即下载
```

# 频道（板块）
caixin channel home --limit 20       # 列出财新首页最新文章（跨频道汇总）
caixin channel list                  # 列出所有频道
caixin channel economy --limit 20    # 列出经济频道最新文章
caixin channel finance --fetch        # 抓取金融频道最新文章
```

### 常用选项

| 选项 | 作用 |
|---|---|
| `--out <目录>` | 输出目录（默认 `./caixin-downloads`） |
| `--no-images` | 不下载配图 |
| `--stdout` | `article` 打印到屏幕不存盘 |
| `--limit N` | `weekly` / `search` 限制篇数 |
| `--delay 1.0` | 请求间隔秒数 |
| `--list-only` | `weekly` / `latest` 只列目录 |
| `--section <栏目>` | `weekly` 只抓某栏目 |
| `--cookie` / `--cookie-file` | 临时提供 Cookie |

全局选项放在命令前，命令选项放在命令后，例如：

```bash
caixin --out D:\caixin --no-images weekly latest --limit 5
```

## 输出结构

```
caixin-downloads/
  articles/                      # 单篇
    2026-07-11-财新周刊-美墨加协定-触礁.md
  weekly-2026-cw1214/            # 整期
    01-封面报道-成败世界杯.md
    02-...
    images/                      # 配图
  search/                        # 搜索下载
    AI芯片/...
```

每个 `.md` 文件含 YAML 元信息（标题/作者/日期/来源/期号/栏目/页数/article_id/抓取时间/paywalled）+ 导语 + 正文 + 本地配图。

## 工作原理

- 文章页服务端只渲染标题/元数据和一段**摘要**；**全文**由浏览器经带签名的网关请求（`gateway.caixin.com/api/newauth/...`，含 `x-nonce`/`x-sign` 签名头）加载，且网关有 TLS 指纹反爬（httpx 直接请求会 `401`）。
- 本工具用 **Playwright 驱动你本机的浏览器**（带你的 Cookie）原生渲染页面，让页面自己的 JS 完成签名与正文加载，再从 `#Main_Content_Val` 提取正文、拼接多页、剥离 AI 标注噪音。
- 元数据、周刊目录、搜索等不涉及签名，走普通 HTTP（httpx）。

## 下一阶段路线图

- ✅ **按板块/频道列出文章**（已完成）：`caixin channel <频道>` 列出/抓取某频道最新文章，支持 19 个频道
  （首页 + 经济/金融/公司/政经/世界/观点/环科/科技/地产/汽车/消费/能源/健康/民生/ESG/数字说/中国改革/比较）。
- 单篇导出 HTML / PDF
- 更新检测（自上次抓取后新增的文章）
- 配置化默认频道与输出模板

## 故障排查

- **`仅摘要` / `paywalled: true`**：Cookie 缺失/失效，或文章不在订阅范围。重新抓 Cookie。
- **渲染慢**：每篇 3–6 秒（浏览器渲染），整期 2–3 分钟；用 `--limit` 先少抓几篇，或 `--no-images`。
- **提示找不到浏览器**：`python -m playwright install chromium`。
- **`search` 无结果**：搜狗结果页结构可能变化；`article` / `weekly` 不受影响。
- **`2026-27` 等年-期格式解析错**：通常已自动修正；若仍报错，改用 `cw<总期号>` 形式。

## 许可

代码采用 **MIT** 许可（见 [LICENSE](LICENSE)）。

**抓取的财新内容不适用本许可**：版权归财新传媒，仅限个人自用，**禁止转载、传播、商用**。

## 免责声明

本工具不绕过任何付费墙--它仅在你已合法订阅、已登录的前提下，把你已可访问的内容导出为本地 Markdown，方便离线阅读与存档。请勿用于批量抓取、转售或任何违反财新网服务条款的用途。
