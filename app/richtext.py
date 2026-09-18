# -*- coding: utf-8 -*-
"""富文本：白名单清洗 / 纯文本提取 / 摘要。

设计要点
--------
1. **入库洗一次、出库再洗一次。** 写路径调用 `parse_body()` 存干净的 HTML；
   模板渲染走 `rich_body()` 又洗一遍 —— 把数据库当成不可信来源，双保险。
   这样即使有人绕过表单直接 INSERT（或从外部导入数据），页面也不会被打出 XSS。
2. 白名单之外的标签**只脱标签、留文字**（`<div>` → 内容保留），
   但 script/style/iframe 这类**连内容一起丢**，免得把代码当正文显示出来。
3. 属性一律白名单，只留 `<a>` 的 href/title 与 `<img>` 的 src/alt/title/尺寸，
   其余（含 `style`、`on*`）全丢；href 只认 http/https/mailto/tel 与相对路径，
   挡掉 `javascript:` `data:`。
4. **正文图片只认本站附件**：`<img>` 的 src 必须严格等于 `/f/<数字>/inline`
   （也就是本站附件表里的某一行）。外链图片一律丢弃 —— 一是校内网本来就断外网，
   二是外链图会带 referer 泄漏、还能被对方换成本地探测；上传过的图走附件体系，
   权限、生命周期、导出打包全都跟着话题走。
5. 纯文本正文（老数据 + 没开 JS 时的降级提交）走 `body_format='text'`，
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
    "ul", "ol", "li", "blockquote", "code", "pre", "hr", "a", "img",
    "h2", "h3", "h4",
}

VOID_TAGS = {"br", "hr", "img"}

# 这些标签连内容一起丢掉（内容当代码看没有任何意义，露出来反而像故障）
DROP_CONTENT_TAGS = {
    "script", "style", "iframe", "frame", "frameset", "object", "embed",
    "applet", "noscript", "template", "svg", "math", "textarea", "select",
    "option", "title", "head", "meta", "link", "base", "form", "input",
    "button", "video", "audio", "canvas", "map", "area",
}

# 属性白名单：a 只留链接相关，img 只留图片本身需要用到的
ATTR_WHITELIST = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
}

SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:")

# 正文里唯一允许的图片地址：本站附件的页内预览地址（对应 files.inline 路由）。
# 用正则死死卡住，`//evil.com/x.png`、`/f/1/download`、外链通通进不来。
IMG_SRC_RE = re.compile(r"^/f/(\d+)/inline$")
# 图片尺寸上限，防止有人用 width="99999" 撑破版式
IMG_MAX_SIDE = 1600

MAX_DEPTH = 24                      # 嵌套深度上限，防构造极端结构
BODY_FORMATS = ("text", "html")

_TAG_RE = re.compile(r"<[^>]*>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_END_RE = re.compile(r"(?i)</\s*(p|div|li|blockquote|pre|h[1-6]|tr|ul|ol)\s*>")
_LI_OPEN_RE = re.compile(r"(?i)<\s*li\b[^>]*>")
_IMG_TAG_RE = re.compile(r"(?i)<\s*img\b[^>]*>")
# 找正文里引用的附件（不加锚点：正文里可能带换行、或 ~ 前后有别的内容）
_ATT_REF_RE = re.compile(r"/f/(\d+)/inline")


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


def _clean_src(raw: str) -> str:
    """只放行本站附件的页内预览地址 `/f/<数字>/inline`，其余一律返回 ''。"""
    flat = "".join(ch for ch in (raw or "") if ord(ch) > 32)
    return flat if IMG_SRC_RE.match(flat) else ""


def _clean_side(raw: str):
    """图片宽高：只留数字，并夹到 IMG_MAX_SIDE 以内。返回 '' 表示丢弃该属性。"""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return ""
    return str(min(int(digits), IMG_MAX_SIDE))


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []
        self.drop_depth = 0

    # ---------- 属性 ----------
    def _attrs_html(self, tag, attrs):
        """拼属性串。返回 None 表示「这个标签整个不要」（图片 src 非法时）。"""
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
            elif k == "src":
                v = _clean_src(v)
                if not v:
                    return None      # 图源不合法 → 整张图丢掉，不留空壳 <img>
            elif k in ("width", "height"):
                v = _clean_side(v)
                if not v:
                    continue
            keep.append((k, v))
        if tag == "a":
            keep.append(("rel", "noopener noreferrer"))
            keep.append(("target", "_blank"))
        return "".join(f' {k}="{escape(v, quote=True)}"' for k, v in keep)

    def _open_tag(self, tag, attrs):
        """开始标签（含空元素）。返回 None = 整个标签丢弃。"""
        if tag == "img":
            a = self._attrs_html("img", attrs)
            return None if a is None else f"<img{a}>"
        if tag in VOID_TAGS:
            return f"<{tag}>"
        return None

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
            html = self._open_tag(tag, attrs)
            if html:
                self.out.append(html)
            return
        if len(self.stack) >= MAX_DEPTH:
            return
        self.out.append(f"<{tag}{self._attrs_html(tag, attrs)}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = (tag or "").lower()
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            html = self._open_tag(tag, attrs)
            if html:
                self.out.append(html)
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
    s = _IMG_TAG_RE.sub("[图片]", s)        # 图片留个占位，免得摘要把整段吞掉
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
    """判断富文本是不是「实质上空的」：`<br>`、`<p></p>`、全空格都算空。

    **只放了一张图片的正文不算空** —— 老师经常发一张图文通知，没配文字。
    """
    if not html:
        return True
    if _IMG_TAG_RE.search(str(html)):
        return False
    txt = _TAG_RE.sub("", str(html))
    return not unescape(txt).replace("\u200b", "").strip()


def extract_att_ids(html: str):
    """正文里引用到的图片附件 id（保序去重）。

    发给服务端做兜底：编辑器会把用到的附件 id 写进隐藏字段 attach_ids，
    万一 JS 没同步上（或有人手写 HTML 提交），这里再扫一遍正文，
    免得图片附件停在 draft 状态 —— 停在 draft 就等于提交后被清掉、图裂。
    认领 SQL 本身限定 `owner_id` + `attachable_type='draft'`，
    所以伪造别人附件 id 也认领不走。
    """
    if not html:
        return []
    out = []
    for m in _ATT_REF_RE.finditer(str(html)):
        i = int(m.group(1))
        if i not in out:
            out.append(i)
    return out


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
