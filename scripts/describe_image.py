#!/usr/bin/env python3
"""DeepSeek Vision Bridge — Image → Text Description Tool.

Takes an image (file path, URL, or base64 data URL), sends it to the user's
configured Vision API, and outputs an exhaustive text description.

Usage:
    python describe_image.py --image "path/to/image.png"
    python describe_image.py --image "https://example.com/photo.jpg"
    python describe_image.py --image "data:image/png;base64,..."
    python describe_image.py --image "photo.jpg" --model "gpt-4o"
    python describe_image.py --locate              # find recent images
    python describe_image.py --check "image.png"   # validate only

Configuration (via environment variables, set by invoke-describe.ps1):
    DEEPSEEK_VISION_BRIDGE_API_KEY    Vision API key
    DEEPSEEK_VISION_BRIDGE_BASE_URL   Vision API base URL
    DEEPSEEK_VISION_BRIDGE_MODEL      Vision model name
"""

import argparse
import asyncio
import base64
import io
import json
import os
import sys
import time
import re
import tempfile
import atexit
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta

import httpx
from PIL import Image

# ── Force UTF-8 output on Windows ──────────────────────────────────
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# ── Constants ────────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0
MAX_IMAGE_DIMENSION = 2048
MAX_IMAGE_BYTES = 5 * 1024 * 1024
API_TIMEOUT = 120.0

# Common image extensions
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff', '.svg'}
# Common screenshot directories
SCREENSHOT_DIRS = list(dict.fromkeys([
    os.path.join(os.environ.get("USERPROFILE", ""), "Pictures", "Screenshots"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
    tempfile.gettempdir(),
]))  # deduped via dict.fromkeys

# ── Default model (consistent with invoke-describe.ps1) ──────────
DEFAULT_MODEL = "qwen-vl-max"

# ── Vision prompt — can be overridden via env vars ───────────────────────────────────
SYSTEM_PROMPT = os.environ.get(
    "DEEPSEEK_VISION_BRIDGE_SYSTEM_PROMPT",
    (
        "You are an expert image analyst. Your description is the ONLY source of visual "
        "information for a blind reader. Describe with ABSOLUTE EXHAUSTIVENESS.\n\n"
        "=== ZONE-BY-ZONE SCAN ===\n"
        "Mentally divide the image into 5 zones (top-left, top-right, center, "
        "bottom-left, bottom-right). Describe each zone separately.\n\n"
        "=== TEXT CONTENT ===\n"
        "Transcribe ALL visible text VERBATIM, line by line. For each text element note:\n"
        "- Exact wording (do not paraphrase)\n"
        "- Font size (small/medium/large), color (with HEX code if discernible), "
        "weight (bold/normal/light), alignment (left/center/right)\n"
        "- Position relative to other elements\n\n"
        "=== COLORS ===\n"
        "Name specific colors. Use hex notation for major color blocks.\n"
        "Note gradients, transparency effects, shadows, highlights.\n\n"
        "=== PEOPLE ===\n"
        "- Count, gender, approximate age, skin tone\n"
        "- Facial expression, gaze direction, emotion\n"
        "- Clothing: type, color, pattern, texture, logos\n"
        "- Pose, gesture, hand position, body orientation\n\n"
        "=== OBJECTS ===\n"
        "- Type, quantity, size (approximate), condition, material\n"
        "- Brand names, model numbers, labels visible\n"
        "- State (open/closed, on/off, filled/empty)\n\n"
        "=== UI / INTERFACE ELEMENTS ===\n"
        "- Buttons: label, color, state (enabled/disabled/hover/pressed), shape\n"
        "- Input fields: placeholder text, cursor position, focus state\n"
        "- Tabs, menus, dropdowns, checkboxes, radio buttons, toggles\n"
        "- Scrollbar position, selected items, active window title\n"
        "- Icons: shape, meaning, size\n\n"
        "=== DATA ===\n"
        "- Charts: type, axes labels, data values, legend, trend direction\n"
        "- Tables: headers, row count, cell values, alignment\n"
        "- Numbers: exact values as shown\n\n"
        "=== ENVIRONMENT ===\n"
        "- Background: indoor/outdoor, surface texture, lighting conditions\n"
        "- Time of day, weather (if applicable), atmosphere/mood\n\n"
        "=== PIXEL-LEVEL DETAILS ===\n"
        "- Blurred/out-of-focus areas, depth of field\n"
        "- Borders, dividing lines, separators, shadows, rounded corners\n"
        "- Watermarks, timestamps, logos, copyright notices\n\n"
        "=== IRON RULES ===\n"
        "1. NEVER summarize or skip. Describe every single element.\n"
        "2. NEVER use words like 'etc', 'and so on', 'other', 'similar'.\n"
        "3. NEVER say 'as shown' without describing what is shown.\n"
        "4. If unsure, state your best guess with 'appears to be'.\n"
        "5. Respond in the SAME LANGUAGE as the image's primary content.\n"
        "6. If the image contains Chinese text, write the full description in Chinese."
    ),
)

USER_PROMPT = os.environ.get(
    "DEEPSEEK_VISION_BRIDGE_USER_PROMPT",
    "Describe this image with maximum exhaustiveness. Scan zone by zone. "
    "Transcribe all text verbatim with font details. Note all colors with #HEX codes. "
    "Count people, describe clothing, expressions, gestures. "
    "List every object, UI element, data point. Include pixel-level details: "
    "shadows, borders, corners, blur, watermarks. "
    "DO NOT summarize. DO NOT omit. DO NOT use 'etc' or 'similar' words. "
    "Respond in the same primary language as the image content.",
)


# ── Helpers ───────────────────────────────────────────────────────────
def log(msg: str, level: str = "info") -> None:
    """Write status message to stderr."""
    tag = {"info": "[info]", "ok": "[ok]", "warn": "[warn]", "error": "[error]"}.get(level, level)
    sys.stderr.write(f"{tag} {msg}\n")


# ── Image processing ──────────────────────────────────────────────────
def compress_if_needed(image_data: bytes) -> bytes:
    """Compress image if exceeds size/dimension limits."""
    if len(image_data) <= MAX_IMAGE_BYTES:
        # Still check dimensions even if size is OK
        try:
            img = Image.open(BytesIO(image_data))
            if img.size[0] > MAX_IMAGE_DIMENSION or img.size[1] > MAX_IMAGE_DIMENSION:
                img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=85, optimize=True)
                return buf.getvalue()
        except Exception:
            pass
        return image_data
    try:
        img = Image.open(BytesIO(image_data))
        original_size = img.size
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        result = buf.getvalue()
        log(f"图片压缩: {len(image_data)} -> {len(result)} bytes "
            f"({original_size[0]}x{original_size[1]} -> {img.size[0]}x{img.size[1]})")
        return result
    except Exception as e:
        log(f"压缩失败 ({e}), 使用原图")
        return image_data


def image_to_data_url(image_data: bytes, default_type: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_data).decode("ascii")
    return f"data:{default_type};base64,{b64}"


def validate_image(data: bytes) -> tuple[bool, str]:
    """Validate that the data is a readable image."""
    if len(data) == 0:
        return False, "文件为空"
    try:
        img = Image.open(BytesIO(data))
        img.verify()
        return True, f"{img.format} ({img.size[0]}x{img.size[1]})" if img.format else "valid"
    except Exception as e:
        # Fallback: try loading without verify
        try:
            img = Image.open(BytesIO(data))
            img.load()
            return True, f"{img.format} ({img.size[0]}x{img.size[1]})" if img.format else "valid"
        except Exception:
            return False, str(e)


async def resolve_image(source: str) -> str:
    """Resolve image source (file path / URL / data URL) to a data URL."""
    # ── Handle data URLs ───────────────────────────────────────────
    if source.startswith("data:image/"):
        try:
            header, b64_part = source.split(",", 1)
            content_type = header.split(";")[0].replace("data:", "")
            # Clean possible whitespace/newlines in base64
            b64_part = b64_part.strip().replace("\n", "").replace("\r", "").replace(" ", "")
            raw = base64.b64decode(b64_part)
            compressed = compress_if_needed(raw)
            if compressed is not raw:
                return image_to_data_url(compressed, content_type)
            return source
        except Exception as e:
            log(f"Data URL 解析失败: {e}", "warn")
            return source

    # ── Handle HTTP URLs ───────────────────────────────────────────
    elif source.startswith("http://") or source.startswith("https://"):
        async with httpx.AsyncClient() as client:
            log(f"下载图片: {source[:80]}...")
            resp = await client.get(source, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            data = resp.content
            log(f"已下载: {len(data)} bytes, {content_type}")
            compressed = compress_if_needed(data)
            return image_to_data_url(compressed, content_type)

    # ── Handle local files ─────────────────────────────────────────
    else:
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {source} (解析后: {path})")

        data = path.read_bytes()
        log(f"读取文件: {path.name} ({len(data)} bytes)")

        valid, info = validate_image(data)
        if not valid:
            raise ValueError(f"无法读取图片: {info}")

        log(f"图片验证: {info}")
        compressed = compress_if_needed(data)
        content_type = f"image/{path.suffix.lstrip('.')}" if path.suffix else "image/png"
        # Normalize content types
        if content_type == "image/jpg":
            content_type = "image/jpeg"
        return image_to_data_url(compressed, content_type)


# ── Vision API call ───────────────────────────────────────────────────
async def describe(
    image_source: str,
    detail: str = "auto",
    api_key: str = "",
    base_url: str = "",
    model: str = DEFAULT_MODEL,
    question: str = "",
) -> str:
    """Send image to Vision API and return text description."""

    # Resolve image to data URL
    try:
        image_url = await resolve_image(image_source)
    except FileNotFoundError as e:
        log(str(e), "error")
        return f"[ERROR] {e}"
    except httpx.HTTPStatusError as e:
        log(f"下载失败: HTTP {e.response.status_code}", "error")
        return f"[ERROR] 下载图片失败: HTTP {e.response.status_code}"
    except Exception as e:
        log(f"处理图片失败: {e}", "error")
        return f"[ERROR] 无法处理图片: {e}"

    # Build API URL (auto-append /v1 if missing)
    api_url = base_url.rstrip("/") + ("/v1" if "/v1" not in base_url else "") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # Build user text: if question is provided, ask Vision API to answer it first
    if question:
        user_text = (
            f"[QUESTION] {question} [/QUESTION]\n\n"
            "First, directly answer the question above based on what you see in the image. "
            "Be specific and precise. Then, provide an exhaustive description of the entire image."
        )
    else:
        user_text = USER_PROMPT

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url, "detail": detail}},
                    {"type": "text", "text": user_text},
                ],
            },
        ],
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    log(f"调用 Vision API: {model} @ {base_url}")
    log(f"图片大小: {len(image_url)} chars (base64)")

    # ── Retry loop ────────────────────────────────────────────────
    last_error = "unknown"
    async with httpx.AsyncClient(timeout=httpx.Timeout(API_TIMEOUT)) as client:
        for attempt in range(MAX_RETRIES):
            try:
                resp = await asyncio.wait_for(
                    client.post(api_url, json=payload, headers=headers),
                    timeout=API_TIMEOUT,
                )
                log(f"API 响应: HTTP {resp.status_code}")

                if resp.status_code == 200:
                    data = resp.json()
                    if "choices" not in data or not data["choices"]:
                        log("API 返回了空的 choices", "warn")
                        return "[ERROR] Vision API 返回了空结果"

                    content = data["choices"][0]["message"]["content"]
                    if not content or not content.strip():
                        log("Vision API 返回了空的描述内容", "error")
                        return "[ERROR] Vision API 返回了空描述。请重试或更换模型。"
                    # If question was asked, wrap output with structured markers
                    if question:
                        # Insert [IMAGE DESCRIPTION] before the first === section header
                        m = re.search(r"\n(={3,}\s)", content)
                        if m:
                            pos = m.start()
                            content = "[PRELIMINARY ANALYSIS]\n" + content[:pos] + "\n\n[IMAGE DESCRIPTION]\n" + content[pos:]
                        else:
                            content = "[PRELIMINARY ANALYSIS]\n" + content + "\n\n[IMAGE DESCRIPTION]"
                    usage = data.get("usage", {})
                    log(
                        f"tokens: prompt={usage.get('prompt_tokens', '?')}, "
                        f"completion={usage.get('completion_tokens', '?')}",
                        "ok"
                    )
                    return content.strip()

                elif resp.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code}"
                    log(f"服务端错误 {resp.status_code}: {resp.text[:200]}", "warn")

                elif resp.status_code == 400:
                    body = resp.text[:800]
                    log(f"400 错误: {body}", "error")
                    # Check for vision-related errors
                    if any(kw in body.lower() for kw in ["vision", "image", "image_url"]):
                        return (
                            f"[ERROR] 模型 '{model}' 不支持视觉/图片输入。"
                            f"请使用支持视觉的模型 (如 gpt-4o, claude-sonnet-4.6, qwen-vl-max)。"
                            f"\nAPI 响应: {body}"
                        )
                    # Check for other common issues
                    if "content_filter" in body.lower() or "content_policy" in body.lower():
                        return f"[ERROR] 图片触发了内容安全策略。\nAPI 响应: {body}"
                    return f"[ERROR] Vision API 拒绝了请求 (HTTP 400): {body}"

                elif resp.status_code in (401, 403):
                    log(f"认证失败 ({resp.status_code})", "error")
                    return "[ERROR] Vision API 认证失败。请检查 API Key 是否正确和有效。运行 configure.ps1 重新配置。"

                else:
                    log(f"HTTP {resp.status_code}: {resp.text[:300]}", "error")
                    return f"[ERROR] Vision API 返回 HTTP {resp.status_code}"

            except httpx.TimeoutException:
                last_error = "timeout"
                log(f"请求超时 (第 {attempt + 1} 次尝试)", "warn")
            except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_error = str(e)
                log(f"连接错误: {e}", "warn")
            except Exception as e:
                log(f"未知异常: {type(e).__name__}: {e}", "error")
                return f"[ERROR] {type(e).__name__}: {e}"

            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log(f"{delay}s 后重试...")
                await asyncio.sleep(delay)

    return f"[ERROR] Vision API 在 {MAX_RETRIES} 次尝试后仍无法访问: {last_error}"


# ── Image locator ─────────────────────────────────────────────────────
def locate_recent_images(max_age_minutes: int = 60, max_results: int = 20) -> list[Path]:
    """Find recent image files in common directories."""
    cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
    images = []

    for dir_path in SCREENSHOT_DIRS:
        if not os.path.isdir(dir_path):
            continue
        try:
            for entry in os.listdir(dir_path):
                full = Path(dir_path) / entry
                if not full.is_file():
                    continue
                if full.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                try:
                    mtime = datetime.fromtimestamp(full.stat().st_mtime)
                    if mtime >= cutoff:
                        images.append((full, mtime))
                except OSError:
                    pass
        except PermissionError:
            continue

    # Sort by modification time, newest first
    images.sort(key=lambda x: x[1], reverse=True)
    return [p for p, _ in images[:max_results]]


# ── Temp file tracker for cleanup ─────────────────────────────────────
_temp_files: list[str] = []

def _cleanup_temp_files() -> None:
    """Remove temp files created by convert_to_png."""
    for fp in _temp_files:
        try:
            Path(fp).unlink(missing_ok=True)
        except OSError:
            pass

atexit.register(_cleanup_temp_files)

# ── Image format converter ────────────────────────────────────────────
def convert_to_png(source: str) -> str:
    """Convert any image to PNG and return path to temp file."""
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {source}")

    try:
        img = Image.open(path)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")

        tmp = Path(tempfile.gettempdir()) / f"vision_bridge_{path.stem}.png"
        img.save(tmp, "PNG")
        _temp_files.append(str(tmp))
        log(f"转换为 PNG: {tmp}")
        return str(tmp)
    except Exception as e:
        log(f"格式转换失败: {e}", "warn")
        return source


# ── CLI entry ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="DeepSeek Vision Bridge — 将图片转换为详尽的文字描述"
    )
    parser.add_argument(
        "--image", "-i", default=None,
        help="图片来源: 本地文件路径 / HTTP URL / data: URL",
    )
    parser.add_argument(
        "--locate", "-l", action="store_true",
        help="搜索最近的图片文件并列出 (不进行分析)",
    )
    parser.add_argument(
        "--check", "-c", default=None,
        help="仅验证图片是否可以读取 (不进行分析)",
    )
    parser.add_argument(
        "--convert", default=None,
        help="将图片转换为 PNG 格式并保存到临时目录",
    )
    parser.add_argument(
        "--detail", "-d", default="auto", choices=["auto", "low", "high"],
        help="Vision 分析精度 (默认: auto)",
    )
    parser.add_argument(
        "--model", "-m", default=None,
        help="覆盖配置的视觉模型 (如 gpt-4o, claude-sonnet-4.6)",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="不输出进度信息到 stderr",
    )
    parser.add_argument(
        "--question", default=None,
        help="用户对图片的具体问题 (如 图中人物是谁)",
    )

    args = parser.parse_args()

    # ── --locate mode ──────────────────────────────────────────────
    if args.locate:
        print("正在搜索最近的图片...", file=sys.stderr)
        images = locate_recent_images()
        if not images:
            print("未找到最近的图片文件。")
        else:
            print(f"找到 {len(images)} 张最近图片：")
            for i, p in enumerate(images, 1):
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime)
                    age = (datetime.now() - mtime).seconds // 60
                    size_kb = p.stat().st_size // 1024
                    print(f"  [{i}] {p}  ({size_kb}KB, {age} 分钟前)")
                except OSError:
                    print(f"  [{i}] {p}")
        return

    # ── --check mode ───────────────────────────────────────────────
    if args.check:
        path = Path(args.check).expanduser().resolve()
        if not path.exists():
            print(f"[FAIL] 文件不存在: {args.check}")
            sys.exit(1)
        try:
            data = path.read_bytes()
            valid, info = validate_image(data)
            if valid:
                size_kb = len(data) // 1024
                print(f"[OK] 可读的图片: {info}, {size_kb}KB")
            else:
                print(f"[FAIL] 无法读取: {info}")
                sys.exit(1)
        except Exception as e:
            print(f"[FAIL] 读取失败: {e}")
            sys.exit(1)
        return

    # ── --convert mode ─────────────────────────────────────────────
    if args.convert:
        try:
            result = convert_to_png(args.convert)
            print(result)
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        return

    # ── --image mode (main flow) ───────────────────────────────────
    if not args.image:
        parser.error("请指定 --image / --locate / --check / --convert 之一")

    api_key = os.environ.get("DEEPSEEK_VISION_BRIDGE_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_VISION_BRIDGE_BASE_URL", "")
    model = args.model or os.environ.get("DEEPSEEK_VISION_BRIDGE_MODEL", DEFAULT_MODEL)

    if not api_key:
        log("DEEPSEEK_VISION_BRIDGE_API_KEY 未设置。请先运行 configure.ps1。", "error")
        sys.exit(1)
    if not base_url:
        log("DEEPSEEK_VISION_BRIDGE_BASE_URL 未设置。请先运行 configure.ps1。", "error")
        sys.exit(1)

    if args.quiet:
        sys.stderr = open(os.devnull, "w")

    start = time.time()
    try:
        result = asyncio.run(describe(args.image, args.detail, api_key, base_url, model, args.question or ""))
        elapsed = time.time() - start
        log(f"完成! 耗时 {elapsed:.1f}s, 描述长度: {len(result)} 字符", "ok")
        print(result)
    except KeyboardInterrupt:
        log("用户中断", "warn")
        sys.exit(130)
    except Exception as e:
        log(f"致命错误: {e}", "error")
        print(f"[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
