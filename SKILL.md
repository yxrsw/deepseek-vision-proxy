---
name: deepseek-vision-proxy
description: >
  【图像→文本转换器】将图片转换为纯文本描述，供 DeepSeek 等纯文本模型理解。
  仅支持文件路径方式发送图片。直接拖拽/粘贴图片将导致对话崩溃。
  每当用户消息中出现图片文件路径或图片关键词时，你必须立即使用本技能将其转为文字，
  绝对不要让 image_url 块到达 DeepSeek 后端。这是唯一的图片处理方式。
---

# DeepSeek Vision Bridge — 图像→文本转换器

> ⚠️ **致命警告**
> 
> 本技能**仅支持文件路径方式**发送图片。
> 
> 🚫 **绝对不要拖拽 / 粘贴 / 直接发送图片** — DeepSeek 会立即响应 
> `unknown variant image_url` 错误，整个对话崩溃且不可恢复。
> 
> ✅ **唯一正确方式**：在聊天框中粘贴图片的**文件绝对路径**。
> 例如：`C:\Users\用户名\Desktop\截图.png`
> 
> 🔁 如果已经拖拽了图片导致对话崩溃，请看下方「错误恢复工作流」。

---

## 核心问题

后端模型 DeepSeek 是纯文本模型，无法处理图片。
含有 `image_url` 块的消息被发送到 DeepSeek API 时：

```
Failed to deserialize the JSON body into the target type:
messages[N]: unknown variant `image_url`, expected `text`
```

**整个对话崩溃，无法恢复。**
必须在 DeepSeek 看到任何图片之前，用 Vision API 将其转为纯文字描述。

---

## 铁律 (IRON RULE)

> 🚫 **绝对不要在回复中构造 `image_url` 块。**
> ✅ **检测到图片 → 立即调用 Vision Bridge → 纯文本回复。**

---

## 触发检测规则（混合模式）

收到每条用户消息时，**首先扫描**以下模式。命中任一即触发 Vision Bridge。

### 文件扩展名（自动命中）

```
.png  .jpg  .jpeg  .gif  .bmp  .webp  .tiff  .svg  .ico
```

### 中文关键词（30+ 个）

```
图片  图  图中  这张图  那个图  截图  截屏  照片  相片  图像
看图  贴图  发图  上图  下图  如图  见图  图示  配图  原图
大图  小图  缩略图  动图  表情包  壁纸  头像  扫码  二维码
```

### 英文关键词

```
image  photo  picture  screenshot  snapshot  pic  img
```

### 判断逻辑

> 同时出现 **关键词** + **文件路径或 URL** → 必须立即调用 Vision Bridge。
> 即使用户只发文件路径（如 `C:\Users\...\img.png`），含图片扩展名也触发。

---

## 防御模式工作流（自动拦截）

### Step 1: 检测

用户消息 → 扫描关键词 + 路径模式。

### Step 2: 立即调用 Vision Bridge

**不要等。不要犹豫。**

如果用户消息中包含明确问题（「这人是谁」「翻译图中文字」「这是什么」等），必须使用 `-Question` 参数：

```powershell
# 带问题的调用
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\.codex\skills\deepseek-vision-proxy\scripts\invoke-describe.ps1" `
  -Image "<路径>" `
  -Question "<用户问题>"
```

如果用户只发了图片路径没有具体问题：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\.codex\skills\deepseek-vision-proxy\scripts\invoke-describe.ps1" `
  -Image "<图片绝对路径或URL>"
```

- **stderr**：进度信息 (`[info]`, `[ok]`, `[warn]`, `[error]`)
- **stdout**：纯文本图片描述
- **耗时**：3–120 秒

### Step 3: 纯文本回复

**绝对不要在回复中包含 `image_url` 块或任何图片引用。**

```markdown
我已通过 Vision Bridge 分析了你的图片：
---
[Vision Bridge 输出的完整文字描述]
---
基于以上内容，我的回复是：
<纯文字回答>
```

---

## 工具模式工作流（主动使用）

### 查找最近截图

用户说「分析我刚才的截图」但没有提供路径时：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\.codex\skills\deepseek-vision-proxy\scripts\invoke-describe.ps1" `
  -Locate
```

列出最近图片后，让用户确认，再调用 `-Image`。

### 验证图片可读性

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\.codex\skills\deepseek-vision-proxy\scripts\invoke-describe.ps1" `
  -Check "path/to/image.png"
```

### 预检诊断

不调用 API，仅检查环境：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\.codex\skills\deepseek-vision-proxy\scripts\invoke-describe.ps1" `
  -Test
```

---

## 智能问答模式（推荐）

当用户同时发送图片路径和问题时（如「这人是谁？C:\screenshot.png」），agent 必须智能提取问题并传给 Vision Bridge：

### Step 1: 提取问题

从用户消息中分离图片路径和问题文本：
- 图片路径：匹配 `.png`、`.jpg` 等扩展名的路径片段
- 问题文本：去除路径后的剩余内容

### Step 2: 调用 Vision Bridge（带问题）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\.codex\skills\deepseek-vision-proxy\scripts\invoke-describe.ps1" `
  -Image "<图片路径>" `
  -Question "<提取的问题>"
```

### Step 3: 利用两段式输出

Vision API 会输出结构化结果：
- `[PRELIMINARY ANALYSIS]` — 直接看图回答用户问题
- `[IMAGE DESCRIPTION]` — 完整详尽图片描述

agent 将这两段输出作为上下文供给 DeepSeek，使其能基于视觉分析结果深度回答。

### 示例

用户：「这人是谁？C:\Users\lenovo\Desktop\photo.png」

agent 应提取：Image=`C:\Users\lenovo\Desktop\photo.png`，Question=`这人是谁？`

---

## 错误恢复工作流（对话已崩溃时）

如果 DeepSeek 已返回 `unknown variant image_url`：

1. 从最近消息中提取文件名
2. 若提取不到，用 `-Locate` 搜索最近图片
3. 回复用户 + 附加警告：

> ⚠️ 本对话已被 image_url 块污染，后续可能再崩。建议开新对话。

---

## 多张图片处理

逐张调用 Vision Bridge，给每张图加 **[图1]**、**[图2]** 标签。

---

## 配置

| 设置 | 环境变量 | 存储方式 |
|------|---------|---------|
| API Key | `DEEPSEEK_VISION_BRIDGE_API_KEY` | DPAPI 加密文件 |
| Base URL | `DEEPSEEK_VISION_BRIDGE_BASE_URL` | 用户环境变量 |
| Model | `DEEPSEEK_VISION_BRIDGE_MODEL` | 用户环境变量 |

模型配置由 Key + URL 决定，适配任何 OpenAI 兼容视觉 API。

### 支持模型

- `gpt-4o`, `gpt-4o-mini` (OpenAI)
- `claude-sonnet-4.6` (Anthropic)
- `qwen-vl-max`, `qwen-vl-plus` (通义千问)
- `gemini-2.5-flash`, `gemini-2.5-pro` (Google)

### 临时覆盖模型

```powershell
$env:DEEPSEEK_VISION_BRIDGE_MODEL = "gpt-4o"
# 然后运行 invoke-describe.ps1
```

---

## 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| `[ERROR] API key 未设置` | Key 未配置 | 运行 `configure.ps1` |
| `[ERROR] 认证失败 (401)` | Key 无效/过期 | 重新运行 `configure.ps1` |
| `[ERROR] HTTP 400` | 模型不支持视觉 | 换 `gpt-4o` 或 `qwen-vl-max` |
| `[ERROR] Vision API 不可达` | 网络/URL 问题 | 检查 URL 和网络 |
| `[ERROR] 请求超时` | 图片太大/API 慢 | 最多等 120s，或缩小图片 |
| `Python not found` | 未安装 Python | `winget install Python.Python.3.12` |
| `No module named 'PIL'` | 未装 Pillow | `pip install Pillow httpx` |
| `[ERROR] 空描述` | API 异常 | 重试或换模型 |
| 中文乱码 | 控制台编码 | 脚本已内置 UTF-8 强制输出 |

---

## 隐私说明

- 图片仅发送到用户配置的 Vision API 端点
- 图片数据仅内存处理，不写入磁盘
- API Key 使用 Windows DPAPI 加密，绝不出现明文
