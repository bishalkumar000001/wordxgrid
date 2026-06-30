import math
import random
import io
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont
from typing import List, Tuple, Dict, Optional

DIRECTIONS = [
    (0, 1),    # right
    (1, 0),    # down
    (0, -1),   # left
    (-1, 0),   # up
    (1, 1),    # down-right
    (1, -1),   # down-left
    (-1, 1),   # up-right
    (-1, -1),  # up-left
]

# Vivid line colors for found-word strikethrough lines
HIGHLIGHT_COLORS = [
    (255,  80,  80),    # red
    (255, 210,  50),    # yellow
    (60,  220, 100),    # green
    (60,  160, 255),    # blue
    (210,  80, 220),    # purple
    (0,   220, 220),    # cyan
    (255, 140,  40),    # orange
    (255,  90, 170),    # pink
    (80,  230, 160),    # mint
    (160, 120, 255),    # lavender
    (220, 180,  20),    # gold
    (0,   200, 180),    # teal
]

IST = timezone(timedelta(hours=5, minutes=30))

# ── Theme palettes ─────────────────────────────────────────────────────────────
_DAY = dict(
    bg          = (255, 255, 255),   # white background
    cell_bg     = (245, 245, 248),   # near-white cell
    cell_border = (200, 200, 210),   # light grid lines
    title_bar   = (20,  20,  35),    # dark title strip
    letter      = (15,  15,  15),    # black letters
    strike      = (0,   0,   0,  210),  # black strikethrough
)
_NIGHT = dict(
    bg          = (10,  12,  20),    # near-black background
    cell_bg     = (22,  25,  40),    # dark cell fill
    cell_border = (45,  50,  75),    # subtle grid lines
    title_bar   = (15,  18,  35),    # darker title strip
    letter      = (230, 235, 255),   # off-white letters
    strike      = (255, 255, 255, 210),  # white strikethrough
)


def _get_theme() -> dict:
    """Return day theme 08:00–19:59 IST, night theme otherwise."""
    hour = datetime.now(IST).hour   # 0-23
    return _DAY if 8 <= hour < 20 else _NIGHT


def can_place(grid, word, row, col, dr, dc):
    rows = len(grid)
    cols = len(grid[0])
    for i, ch in enumerate(word):
        r = row + dr * i
        c = col + dc * i
        if r < 0 or r >= rows or c < 0 or c >= cols:
            return False
        if grid[r][c] not in ('.', ch):
            return False
    return True


def place_word(grid, word, row, col, dr, dc):
    for i, ch in enumerate(word):
        r = row + dr * i
        c = col + dc * i
        grid[r][c] = ch


def build_grid(words: List[str], size: int = 10) -> Tuple[List[List[str]], dict]:
    for attempt in range(600):
        grid = [['.' for _ in range(size)] for _ in range(size)]
        placed = {}
        success = True

        shuffled = words[:]
        random.shuffle(shuffled)

        for word in shuffled:
            word_placed = False
            dirs = DIRECTIONS[:]
            random.shuffle(dirs)

            positions = [(r, c) for r in range(size) for c in range(size)]
            random.shuffle(positions)

            for dr, dc in dirs:
                for row, col in positions:
                    if can_place(grid, word, row, col, dr, dc):
                        place_word(grid, word, row, col, dr, dc)
                        placed[word] = (row, col, dr, dc)
                        word_placed = True
                        break
                if word_placed:
                    break

            if not word_placed:
                success = False
                break

        if success:
            letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            for r in range(size):
                for c in range(size):
                    if grid[r][c] == '.':
                        grid[r][c] = random.choice(letters)
            return grid, placed

    # fallback: slightly bigger grid
    bigger = size + 2
    grid = [['.' for _ in range(bigger)] for _ in range(bigger)]
    placed = {}
    for word in words:
        for _ in range(2000):
            dr, dc = random.choice(DIRECTIONS)
            row = random.randint(0, bigger - 1)
            col = random.randint(0, bigger - 1)
            if can_place(grid, word, row, col, dr, dc):
                place_word(grid, word, row, col, dr, dc)
                placed[word] = (row, col, dr, dc)
                break
    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    for r in range(bigger):
        for c in range(bigger):
            if grid[r][c] == '.':
                grid[r][c] = random.choice(letters)
    return grid, placed


def _load_font(size: int):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _found_cells(placed_words: Dict, found_words: List[str]) -> set:
    """Return set of (row, col) for all cells belonging to found words."""
    cells = set()
    for word in found_words:
        if word not in placed_words:
            continue
        wr, wc, dr, dc = placed_words[word]
        for i in range(len(word)):
            cells.add((wr + dr * i, wc + dc * i))
    return cells


def render_grid_image(
    grid: List[List[str]],
    title: str = "WORD GRID CHALLENGE",
    placed_words: Optional[Dict[str, tuple]] = None,
    found_words: Optional[List[str]] = None,
    word_order: Optional[List[str]] = None,
) -> bytes:
    rows = len(grid)
    cols = len(grid[0])

    cell_size    = 62
    padding      = 20
    title_height = 64
    border       = 3

    img_w = cols * cell_size + padding * 2
    img_h = rows * cell_size + padding * 2 + title_height

    found_words  = found_words  or []
    placed_words = placed_words or {}
    word_order   = word_order   or []

    T    = _get_theme()

    # ── Base image ────────────────────────────────────────────────────────────
    img  = Image.new('RGBA', (img_w, img_h), T['bg'] + (255,))
    draw = ImageDraw.Draw(img)

    font       = _load_font(27)
    title_font = _load_font(22)

    # ── Title bar ─────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, img_w, title_height], fill=T['title_bar'])
    draw.rectangle([0, title_height - 3, img_w, title_height], fill=(255, 200, 0))

    try:
        bbox = draw.textbbox((0, 0), title, font=title_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:
        tw, th = len(title) * 13, 22

    # shadow then bright text
    draw.text(
        ((img_w - tw) // 2 + 2, (title_height - th) // 2 + 2),
        title, fill=(0, 0, 0, 130), font=title_font,
    )
    draw.text(
        ((img_w - tw) // 2, (title_height - th) // 2),
        title, fill=(255, 220, 50), font=title_font,
    )

    # ── Outer grid border ─────────────────────────────────────────────────────
    gx0 = padding - border
    gy0 = title_height + padding - border
    gx1 = img_w - padding + border
    gy1 = img_h - padding + border
    border_col = (80, 90, 130) if T['bg'][0] < 128 else (40, 40, 40)
    draw.rectangle([gx0, gy0, gx1, gy1], outline=border_col, width=border)

    # ── Cells ─────────────────────────────────────────────────────────────────
    found_cell_set = _found_cells(placed_words, found_words)

    for r in range(rows):
        for c in range(cols):
            x = padding + c * cell_size
            y = title_height + padding + r * cell_size
            draw.rectangle(
                [x, y, x + cell_size - 1, y + cell_size - 1],
                fill=T['cell_bg'],
                outline=T['cell_border'],
                width=1,
            )

    # ── Letters drawn FIRST so the line cuts across them ─────────────────────
    for r in range(rows):
        for c in range(cols):
            x  = padding + c * cell_size
            y  = title_height + padding + r * cell_size
            ch = grid[r][c]
            try:
                bbox = draw.textbbox((0, 0), ch, font=font)
                cw   = bbox[2] - bbox[0]
                ch_h = bbox[3] - bbox[1]
            except Exception:
                cw, ch_h = 18, 18
            tx = x + (cell_size - cw) // 2
            ty = y + (cell_size - ch_h) // 2
            draw.text((tx, ty), ch, fill=T['letter'], font=font)

    # ── Strikethrough lines drawn ON TOP of letters ───────────────────────────
    if found_words and placed_words:
        line_layer = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        line_draw  = ImageDraw.Draw(line_layer)
        line_width = max(5, cell_size // 10)

        for word in found_words:
            if word not in placed_words:
                continue

            wr, wc, dr, dc = placed_words[word]
            n = len(word)

            cx0 = padding + wc * cell_size + cell_size // 2
            cy0 = title_height + padding + wr * cell_size + cell_size // 2
            cx1 = padding + (wc + dc * (n - 1)) * cell_size + cell_size // 2
            cy1 = title_height + padding + (wr + dr * (n - 1)) * cell_size + cell_size // 2

            line_draw.line(
                [(cx0, cy0), (cx1, cy1)],
                fill=T['strike'],
                width=line_width,
            )

        img  = Image.alpha_composite(img, line_layer)
        draw = ImageDraw.Draw(img)

    # ── Save ──────────────────────────────────────────────────────────────────
    out = img.convert('RGB')
    buf = io.BytesIO()
    out.save(buf, format='PNG', optimize=False)
    buf.seek(0)
    return buf.read()


def get_hint_text(words: List[str], found: List[str]) -> str:
    lines = []
    for i, w in enumerate(words, 1):
        if w in found:
            lines.append(f"✅ {i}. <s>{w}</s>")
        else:
            masked = w[0] + '_' * (len(w) - 1)
            lines.append(f"➥ {masked} ({len(w)})")
    return "\n".join(lines)


def make_hint_for_word(word: str, revealed: int = 2) -> str:
    visible = min(revealed, len(word) - 1)
    return word[:visible] + '-' * (len(word) - visible) + f" ({len(word)})"
