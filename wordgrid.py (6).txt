import math
import random
import io
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

# ── Dark theme palette ────────────────────────────────────────────────────────
BG_COLOR       = (10,  12,  20)    # near-black background
CELL_BG        = (22,  25,  40)    # dark cell fill
CELL_BORDER    = (45,  50,  75)    # subtle grid lines
LETTER_COLOR   = (230, 235, 255)   # off-white letters
LETTER_FOUND   = (230, 235, 255)   # same brightness — visible through the black line


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

    # ── Base image: dark background ───────────────────────────────────────────
    img  = Image.new('RGBA', (img_w, img_h), BG_COLOR + (255,))
    draw = ImageDraw.Draw(img)

    font       = _load_font(27)
    title_font = _load_font(22)

    # ── Title bar ─────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, img_w, title_height], fill=(15, 18, 35))
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
    draw.rectangle([gx0, gy0, gx1, gy1], outline=(80, 90, 130), width=border)

    # ── Dark cells ────────────────────────────────────────────────────────────
    found_cell_set = _found_cells(placed_words, found_words)

    for r in range(rows):
        for c in range(cols):
            x = padding + c * cell_size
            y = title_height + padding + r * cell_size
            cell_color = (30, 34, 55) if (r, c) in found_cell_set else CELL_BG
            draw.rectangle(
                [x, y, x + cell_size - 1, y + cell_size - 1],
                fill=cell_color,
                outline=CELL_BORDER,
                width=1,
            )

    # ── Strikethrough lines for found words ───────────────────────────────────
    if found_words and placed_words and word_order:
        line_layer = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        line_draw  = ImageDraw.Draw(line_layer)

        line_width = max(6, cell_size // 8)   # crisp but not chunky

        for word in found_words:
            if word not in placed_words:
                continue

            wr, wc, dr, dc = placed_words[word]
            n = len(word)

            # center of first and last cell
            cx0 = padding + wc * cell_size + cell_size // 2
            cy0 = title_height + padding + wr * cell_size + cell_size // 2
            cx1 = padding + (wc + dc * (n - 1)) * cell_size + cell_size // 2
            cy1 = title_height + padding + (wr + dr * (n - 1)) * cell_size + cell_size // 2

            # simple black strikethrough line
            line_draw.line(
                [(cx0, cy0), (cx1, cy1)],
                fill=(0, 0, 0, 200),
                width=line_width,
            )

        img  = Image.alpha_composite(img, line_layer)
        draw = ImageDraw.Draw(img)

    # ── Letters ───────────────────────────────────────────────────────────────
    for r in range(rows):
        for c in range(cols):
            x  = padding + c * cell_size
            y  = title_height + padding + r * cell_size
            ch = grid[r][c]

            is_found = (r, c) in found_cell_set
            color    = LETTER_FOUND if is_found else LETTER_COLOR

            try:
                bbox = draw.textbbox((0, 0), ch, font=font)
                cw   = bbox[2] - bbox[0]
                ch_h = bbox[3] - bbox[1]
            except Exception:
                cw, ch_h = 18, 18

            tx = x + (cell_size - cw) // 2
            ty = y + (cell_size - ch_h) // 2
            draw.text((tx, ty), ch, fill=color, font=font)

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
