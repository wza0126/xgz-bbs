# -*- coding: utf-8 -*-
"""富文本：白名单清洗 / 纯文本提取 / 摘要。

设计要点
--------
1. **入库洗一次、出库再洗一次。** 写路径调用 `parse_body()` 存干净的 HTML；
   模板渲染走 `rich_body()` 又洗一遍 —— 把数据库当成不可信来源，双保险。
   这样即使有人绕过表单直接 INSERT（或从外部导入数据），页面也不会被打出 XSS。
2. 白名单之外的标签**只脱标签、留文字**（`<div>` → 内容保留），
   但 script/style/iframe 这类**连内容一起丢**，免得把代码当正文显示出来。
3. 属性一律白名单，只留 `<a>` 的 href/title，其余（含 `style`、`on*`）全丢；
   href 只认 http/https/mailto/tel 与相对路径，挡掉 `javascript:` `data:`。
4. 纯文本正文（老数据 + 没开 JS 时的降级提交）走 `body_format='text'`，
   渲染时转义 + CSS `white-space:pre-wrap` 保留换行，与升级前完全一致。
"""
import re
from html import escape, unescape
from html.parser import HTMLParser

from markupsafe import Markup

# 允许保留的标签。div 也放行 —— contenteditable 在部分浏览器里回车会产出 <div>
ALLOWED_TAGS = {
    "p", "br", "div", "span",
    "strong", "b", "em", "i", "u", "s", "strike", "del", "ins", "mark",
    "ul", "ol", "li", "blockquote", "code", "pre", "hr", "a",
    "h2", "h3", "h4",
}

VOID_TAGS = {"br", "hr"}

# 这些标签连内容一起丢掉（内容当代码看没有任何意义，露出来反而像故障）
DROP_CONTENT_TAGS = {
    "script", "style", "iframe", "frame", "frameset", "object", "embed",
    "applet", "noscript", "template", "svg", "math", "textarea", "select",
    "option", "title", "head", "meta", "link", "base", "form", "input",
    "button", "video", "audio", "canvas", "map", "area",
}

# 只有 <a> 允许带属性
ATTR_WHITELIST = {"a": {"href", "title"}}

SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:")

MAX_DEPTH = 24                      # 嵌套深度上限，防构造极端结构
BODY_FORMATS = ("text", "html")

_TAG_RE = re.compile(r"<[^>]*>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_END_RE = re.compile(r"(?i)</\s*(p|div|li|blockquote|pre|h[1-6]|tr|ul|ol)\s*>")
_LI_OPEN_RE = re.compile(r"(?i)<\s*li\b[^>]*>")


def _clean_href(raw: str) -> str:
    """只放行安全的链接目标。返回 '' 表示丢弃该属性。"""
    v = (raw or "").strip()
    # 先抹掉所有控制字符与空白：`java\tscript:` 这类就是靠它绕过的
    flat = "".join(ch for ch in v if ord(ch) > 32)
    low = flat.lower()
    if not flat:
        return ""
    if low.startswith("#") or low.startswith("/") or low.startswith("."):
        return flat
    if low.startswith(SAFE_SCHEMES):
        return flat
    # 没写协议的（www.xxx.com / a.html）当相对地址，不构成脚本执行
    if ":" not in low.split("/")[0]:
        return flat
    return ""


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []
        self.drop_depth = 0

    # ---------- 属性 ----------
    def _attrs_html(self, tag, attrs):
        allow = ATTR_WHITELIST.get(tag)
        if not allow:
            return ""
        keep = []
        for k, v in attrs or []:
            k = (k or "").lower()
            if k not in allow:
                continue
            v = "" if v is None else str(v)
            if k == "href":
                v = _clean_href(v)
                if not v:
                    continue
            keep.append((k, v))
        if tag == "a":
            keep.append(("rel", "noopener noreferrer"))
            keep.append(("target", "_blank"))
        return "".join(f' {k}="{escape(v, quote=True)}"' for k, v in keep)

    # ---------- 标签 ----------
    def handle_starttag(self, tag, attrs):
        tag = (tag or "").lower()
        if self.drop_depth:
            if tag in DROP_CONTENT_TAGS:
                self.drop_depth += 1
            return
        if tag in DROP_CONTENT_TAGS:
            self.drop_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            return                      # 只脱标签，内容保留
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}>")
            return
        if len(self.stack) >= MAX_DEPTH:
            return
        self.out.append(f"<{tag}{self._attrs_html(tag, attrs)}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = (tag or "").lower()
        if tag in VOID_TAGS:
            if not self.drop_depth:
                self.out.append(f"<{tag}>")
            return
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs_html(tag, attrs)}></{tag}>")

    def handle_endtag(self, tag):
        tag = (tag or "").lower()
        if self.drop_depth:
            if tag in DROP_CONTENT_TAGS:
                self.drop_depth -= 1
            return
        if tag in VOID_TAGS or tag not in ALLOWED_TAGS or tag not in self.stack:
            return
        # 一路弹到匹配的那个，顺手补上中间没闭合的
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    # ---------- 文本 ----------
    def handle_data(self, data):
        if self.drop_depth:
            return
        self.out.append(escape(data, quote=False))

    def handle_entityref(self, name):
        if not self.drop_depth:
            self.out.append(escape(f"&{name};", quote=False))

    def handle_charref(self, name):
        if not self.drop_depth:
            self.out.append(escape(f"&#{name};", quote=False))

    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return

    def result(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(text: str, max_chars: int = None) -> str:
    """白名单清洗。解析失败时退化为「转义后的纯文本」，绝不原样放行。"""
    if not text:
        return ""
    text = str(text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    p = _Sanitizer()
    try:
        p.feed(text)
        p.close()
    except Exception:  # noqa: BLE001  解析器极少抛错，真抛了就降级
        return escape(_TAG_RE.sub("", text), quote=False)
    return p.result()


def to_plain(text: str, fmt: str = "html") -> str:
    """正文 → 纯文本。用于材料导出、搜索结果摘要、@提及匹配。"""
    if not text:
        return ""
    text = str(text)
    if fmt != "html":
        return text
    s = _SCRIPT_RE.sub("", text)
    s = _BR_RE.sub("\n", s)
    s = _LI_OPEN_RE.sub("· ", s)
    s = _BLOCK_END_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = unescape(s)
    s = s.replace("\u200b", "").replace("\xa0", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def is_blank_html(html: str) -> bool:
    """判断富文本是不是「实质上空的」：`<br>`、`<p></p>`、全空格都算空。"""
    if not html:
        return True
    txt = _TAG_RE.sub("", str(html))
    return not unescape(txt).replace("\u200b", "").strip()


def excerpt(text: str, fmt: str = "html", limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", to_plain(text, fmt)).strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def rich_body(text: str, fmt: str = "text"):
    """模板里用的安全渲染结果（Markup）。

    - `html`：白名单清洗后原样输出（已确定安全，可当 Markup）
    - `text`：转义后输出，换行交给 CSS 的 white-space:pre-wrap
    """
    if not text:
        return Markup("")
    if fmt == "html":
        return Markup(sanitize_html(text))
    return Markup(escape(str(text), quote=False))


def parse_body(form, field: str = "body", max_chars: int = None):
    """从表单读正文 → `(正文, 格式)`。

    格式由同级隐藏字段 `<field>_format` 决定，只有 JS 编辑器会填 `html`；
    没开 JS 时它是 `text`，提交上来的就是纯文本 —— 老路径原样保留。

    超长直接抛 ValueError（不静默截断，免得用户以为发出去了）。
    """
    raw = form.get(field) or ""
    fmt = "html" if (form.get(f"{field}_format") or "").strip() == "html" else "text"

    if max_chars and len(raw) > max_chars:
        raise ValueError(f"正文太长了，最多 {max_chars} 个字，现在有 {len(raw)} 个")

    if fmt == "html":
        cleaned = sanitize_html(raw, max_chars)
        if is_blank_html(cleaned):
            return "", fmt
        return cleaned, fmt

    raw = raw.strip()
    if len(raw) > (max_chars or 10 ** 9):
        raise ValueError(f"正文太长了，最多 {max_chars} 个字")
    return raw, fmt
