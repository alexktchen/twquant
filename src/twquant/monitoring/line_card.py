"""LINE 群組每日圖卡（T-423／D-204）：把名單、族群資金、晨報、盤後快訊畫成一張 PNG，配色同網站（深色、橘色重點）。

純函數：輸入卡片內容（Card）與樣式（configs/line_group.yaml 的 card 段），回 PNG bytes；不寫檔、不連網。
同一份輸入兩次畫出來逐位元相同（Pillow 版本不變的前提下）。版面尺寸以 1040 寬為基準，寬度改了等比例縮放。
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from twquant.config import LineCardFont, LineCardStyle
from twquant.errors import ConfigError

# 版面尺寸（像素，以 1040 寬為基準；畫的時候乘上 width/1040）
_BASE_WIDTH = 1040
_PAD_X = 56
_HEADER_H = 176
_BRAND_SIZE = 26
_TITLE_SIZE = 52
_HEADING_SIZE = 36
_NOTE_SIZE = 28
_ROW_H = 62
_RANK_W = 52
_CODE_W = 104
_NAME_SIZE = 32
_CODE_SIZE = 28
_TAG_SIZE = 26
_BADGE_SIZE = 22
_TEXT_SIZE = 30
_TEXT_LINE_H = 46
_SECTION_GAP = 30
_FOOTER_SIZE = 30
_SMALL_SIZE = 22


@dataclass(frozen=True)
class CardRow:
    """名單的一列：名次、代號、股名、右邊的小標（族群）、名字後面的徽章（NEW）。"""

    rank: str
    code: str
    name: str
    tag: str = ""
    badge: str = ""


@dataclass(frozen=True)
class CardSection:
    """一個區塊：標題＋旁註，底下是名單列或一段段文字，最後一行「…另 N 檔」。"""

    heading: str
    note: str = ""
    rows: tuple[CardRow, ...] = ()
    paragraphs: tuple[str, ...] = ()
    more: str = ""


@dataclass(frozen=True)
class Card:
    """一整張卡：標題、區塊、底部的「點圖看…」與網站、免責聲明。"""

    title: str
    sections: tuple[CardSection, ...]
    cta: str
    host: str
    footer: str


@dataclass(frozen=True)
class Fonts:
    """一般與粗體字型檔（含 .ttc 裡第幾個字面）。"""

    regular: LineCardFont
    bold: LineCardFont


def pick_fonts(style: LineCardStyle, exists: Callable[[str], bool] | None = None) -> Fonts:
    """照設定的候選順序挑第一個存在的字型檔（GitHub Actions 裝 Noto Sans CJK、Windows 用微軟正黑體）。"""
    check = exists or (lambda p: Path(p).is_file())

    def first(cands: tuple[LineCardFont, ...], what: str) -> LineCardFont:
        for f in cands:
            if check(f.path):
                return f
        raise ConfigError(
            f"找不到{what}中文字型：{[f.path for f in cands]}（GitHub Actions 要 apt-get install fonts-noto-cjk）"
        )

    return Fonts(first(style.fonts_regular, "一般"), first(style.fonts_bold, "粗體"))


def wrap(text: str, width: float, measure: Callable[[str], float]) -> list[str]:
    """逐字斷行（中文沒有空白可斷）；原文的換行照留。"""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if cur and measure(cur + ch) > width:
                lines.append(cur)
                cur = ch.lstrip() if ch == " " else ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def ellipsize(text: str, width: float, measure: Callable[[str], float]) -> str:
    """放不下就截短加「…」。"""
    if measure(text) <= width:
        return text
    while text and measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


def render_card(card: Card, style: LineCardStyle, fonts: Fonts) -> tuple[bytes, int, int]:
    """畫成 PNG，回 (bytes, 寬, 高)。高度依內容而定（Flex 訊息照這個比例顯示）。"""
    k = style.width / _BASE_WIDTH

    def px(v: float) -> int:
        return round(v * k)

    cache: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}

    def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (bold, size)
        if key not in cache:
            face = fonts.bold if bold else fonts.regular
            cache[key] = ImageFont.truetype(face.path, px(size), index=face.index)
        return cache[key]

    c = style.colors
    w = style.width
    inner = w - 2 * px(_PAD_X)
    ops: list[Callable[[ImageDraw.ImageDraw], None]] = []

    def text_op(
        xy: tuple[float, float], s: str, f: ImageFont.FreeTypeFont, fill: str, anchor: str = "la"
    ) -> None:
        ops.append(lambda d: d.text(xy, s, font=f, fill=fill, anchor=anchor))

    def line_op(xy: tuple[float, float, float, float]) -> None:
        ops.append(lambda d: d.line(xy, fill=c.rule, width=px(2)))

    def badge_op(box: tuple[float, float, float, float]) -> None:
        ops.append(lambda d: d.rounded_rectangle(box, radius=px(8), fill=c.accent))

    # 標題列
    ops.append(lambda d: d.rectangle((0, 0, w, px(_HEADER_H)), fill=c.panel))
    ops.append(lambda d: d.rectangle((0, 0, w, px(6)), fill=c.accent))
    text_op((px(_PAD_X), px(40)), "twquant", font(_BRAND_SIZE, True), c.accent)
    title_f = font(_TITLE_SIZE, True)
    text_op((px(_PAD_X), px(80)), ellipsize(card.title, inner, title_f.getlength), title_f, c.text)
    y = px(_HEADER_H) + px(_SECTION_GAP)

    for i, sec in enumerate(card.sections):
        if i > 0:
            line_op((px(_PAD_X), y, w - px(_PAD_X), y))
            y += px(_SECTION_GAP)
        if sec.heading:
            hf = font(_HEADING_SIZE, True)
            nf = font(_NOTE_SIZE)
            note_w = nf.getlength(sec.note) + px(20) if sec.note else 0
            head_lines = wrap(sec.heading, inner - note_w, hf.getlength)
            for j, ln in enumerate(head_lines):
                text_op((px(_PAD_X), y), ln, hf, c.text)
                if j == 0 and sec.note:
                    text_op((w - px(_PAD_X), y + px(6)), sec.note, nf, c.muted, "ra")
                y += px(_HEADING_SIZE + 14)
            y += px(8)
        for r in sec.rows:
            mid = y + px(_ROW_H) / 2
            x = px(_PAD_X)
            text_op((x, mid), r.rank, font(_CODE_SIZE), c.muted, "lm")
            x += px(_RANK_W)
            text_op((x, mid), r.code, font(_CODE_SIZE), c.text, "lm")
            x += px(_CODE_W)
            tag_f = font(_TAG_SIZE)
            tag = ellipsize(r.tag, inner * 0.32, tag_f.getlength) if r.tag else ""
            tag_w = tag_f.getlength(tag) if tag else 0
            badge_f = font(_BADGE_SIZE, True)
            badge_w = badge_f.getlength(r.badge) + px(20) if r.badge else 0
            name_f = font(_NAME_SIZE, True)
            room = (w - px(_PAD_X)) - x - tag_w - badge_w - px(28)
            name = ellipsize(r.name, room, name_f.getlength)
            text_op((x, mid), name, name_f, c.text, "lm")
            if r.badge:
                bx = x + name_f.getlength(name) + px(14)
                box = (bx, mid - px(17), bx + badge_w, mid + px(17))
                badge_op(box)
                text_op(((box[0] + box[2]) / 2, mid), r.badge, badge_f, c.badge_text, "mm")
            if tag:
                text_op((w - px(_PAD_X), mid), tag, tag_f, c.muted, "rm")
            y += px(_ROW_H)
        tf = font(_TEXT_SIZE)
        for para in sec.paragraphs:
            for ln in wrap(para, inner, tf.getlength):
                text_op((px(_PAD_X), y), ln, tf, c.text)
                y += px(_TEXT_LINE_H)
            y += px(8)
        if sec.more:
            text_op((px(_PAD_X) + px(_RANK_W), y + px(6)), sec.more, font(_TAG_SIZE), c.muted)
            y += px(_ROW_H) - px(10)
        y += px(_SECTION_GAP) - px(10)

    # 底部：點圖看完整內容、網站、免責聲明
    sf = font(_SMALL_SIZE)
    foot_lines = wrap(card.footer, inner, sf.getlength) if card.footer else []
    foot_top = y + px(10)
    foot_h = px(40 + _FOOTER_SIZE + 24) + len(foot_lines) * px(_SMALL_SIZE + 12) + px(28)
    height = foot_top + foot_h
    ops.append(lambda d: d.rectangle((0, foot_top, w, height), fill=c.panel))
    fy = foot_top + px(40)
    text_op((px(_PAD_X), fy), card.cta, font(_FOOTER_SIZE, True), c.accent)
    text_op((w - px(_PAD_X), fy + px(4)), card.host, font(_NOTE_SIZE), c.muted, "ra")
    fy += px(_FOOTER_SIZE + 24)
    for ln in foot_lines:
        text_op((px(_PAD_X), fy), ln, sf, c.muted)
        fy += px(_SMALL_SIZE + 12)

    img = Image.new("RGB", (w, int(height)), c.bg)
    draw = ImageDraw.Draw(img)
    for op in ops:
        op(draw)
    # 調色盤壓縮：深色底＋少數幾個顏色，從 ~200 KB 壓到 ~50 KB（雲端資料庫只有 500 MB）
    small = img.quantize(
        colors=style.palette_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    buf = io.BytesIO()
    small.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), w, int(height)


def to_jpeg(png: bytes, quality: int) -> bytes:
    """PNG 圖卡轉 JPEG（記事本上傳只吃 JPEG；圖卡是深底不透明，直接轉 RGB 不會有透明邊）。"""
    out = io.BytesIO()
    with Image.open(io.BytesIO(png)) as im:
        im.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()
