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

# 12 vivid highlight colors for pills
HIGHLIGHT_COLORS = [
    (220, 50,  50),    # red
    (255, 200, 0),     # yellow
    (50,  180, 80),    # green
    (50,  130, 230),   # blue
    (200, 60,  210),   # purple
    (0,   190, 190),   # cyan
    (255, 120, 30),    # orange
    (240, 80,  150),   # pink
    (80,  210, 140),   # mint
    (140, 100, 230),   # lavender
    (180, 140, 0),     # dark gold
    (0,   160, 160),   # teal
]


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

    # fallback: try a bigger grid
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


def _draw_pill(layer: Image.Image, x0: float, y0: float, x1: float, y1: float,
               color: Tuple[int, int, int, int], radius: float):
    """Draw a capsule/pill shape between (x0,y0) and (x1,y1) on an RGBA layer."""
    draw = ImageDraw.Draw(layer)

    # Circle caps at both endpoints
    draw.ellipse(
        [x0 - radius, y0 - radius, x0 + radius, y0 + radius],
        fill=color,
    )
    draw.ellipse(
        [x1 - radius, y1 - radius, x1 + radius, y1 + radius],
        fill=color,
    )

    # Connecting rectangle perpendicular to the word direction
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length < 1:
        return

    # Perpendicular unit vector scaled by radius
    px = (-dy / length) * radius
    py = (dx / length) * radius

    pts = [
        (x0 + px, y0 + py),
        (x0 - px, y0 - py),
        (x1 - px, y1 - py),
        (x1 + px, y1 + py),
    ]
    draw.polygon(pts, fill=color)


def render_grid_image(
    grid: List[List[str]],
    title: str = "WORD GRID CHALLENGE",
    placed_words: Optional[Dict[str, tuple]] = None,
    found_words: Optional[List[str]] = None,
    word_order: Optional[List[str]] = None,
) -> bytes:
    rows = len(grid)
    cols = len(grid[0])

    cell_size    = 62          # HD — bigger cells
    padding      = 20
    title_height = 64
    border       = 3

    img_w = cols * cell_size + padding * 2
    img_h = rows * cell_size + padding * 2 + title_height

    # ── Base image: pure white background ────────────────────────────────────
    img = Image.new('RGBA', (img_w, img_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    font       = _load_font(27)   # HD font size
    title_font = _load_font(22)

    # ── Title bar — dark gradient-style strip ────────────────────────────────
    draw.rectangle([0, 0, img_w, title_height], fill=(20, 20, 30))
    # Subtle accent line under title
    draw.rectangle([0, title_height - 3, img_w, title_height], fill=(255, 200, 0))

    title_text = title
    try:
        bbox = draw.textbbox((0, 0), title_text, font=title_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:
        tw, th = len(title_text) * 13, 22
    # Shadow
    draw.text(
        ((img_w - tw) // 2 + 2, (title_height - th) // 2 + 2),
        title_text, fill=(0, 0, 0, 120), font=title_font,
    )
    draw.text(
        ((img_w - tw) // 2, (title_height - th) // 2),
        title_text, fill=(255, 220, 50), font=title_font,
    )

    # ── Outer grid border ─────────────────────────────────────────────────────
    gx0 = padding - border
    gy0 = title_height + padding - border
    gx1 = img_w - padding + border
    gy1 = img_h - padding + border
    draw.rectangle([gx0, gy0, gx1, gy1], outline=(40, 40, 40), width=border)

    # ── White cells with subtle grid lines ───────────────────────────────────
    for r in range(rows):
        for c in range(cols):
            x = padding + c * cell_size
            y = title_height + padding + r * cell_size
            draw.rectangle(
                [x, y, x + cell_size - 1, y + cell_size - 1],
                fill=(255, 255, 255),
                outline=(200, 200, 200),
                width=1,
            )

    # ── Highlight pills on transparent overlay ────────────────────────────────
    if placed_words and found_words and word_order:
        pill_layer = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))

        for word in found_words:
            if word not in placed_words:
                continue
            color_idx = word_order.index(word) % len(HIGHLIGHT_COLORS)
            r_col, g_col, b_col = HIGHLIGHT_COLORS[color_idx]
            pill_color = (r_col, g_col, b_col, 200)

            wr, wc, dr, dc = placed_words[word]
            n = len(word)

            cx0 = padding + wc * cell_size + cell_size / 2
            cy0 = title_height + padding + wr * cell_size + cell_size / 2
            cx1 = padding + (wc + dc * (n - 1)) * cell_size + cell_size / 2
            cy1 = title_height + padding + (wr + dr * (n - 1)) * cell_size + cell_size / 2

            pill_radius = cell_size * 0.40

            _draw_pill(pill_layer, cx0, cy0, cx1, cy1, pill_color, pill_radius)

        img = Image.alpha_composite(img, pill_layer)
        draw = ImageDraw.Draw(img)

    # ── Letters — bold black, centered, drawn on top of pills ────────────────
    for r in range(rows):
        for c in range(cols):
            x = padding + c * cell_size
            y = title_height + padding + r * cell_size
            ch = grid[r][c]
            try:
                bbox = draw.textbbox((0, 0), ch, font=font)
                cw   = bbox[2] - bbox[0]
                ch_h = bbox[3] - bbox[1]
            except Exception:
                cw, ch_h = 18, 18

            tx = x + (cell_size - cw) // 2
            ty = y + (cell_size - ch_h) // 2
            draw.text((tx, ty), ch, fill=(15, 15, 15), font=font)

    # ── Save as high-quality PNG ──────────────────────────────────────────────
    out = img.convert('RGB')
    buf = io.BytesIO()
    out.save(buf, format='PNG', optimize=False)   # no optimize → sharper/faster
    buf.seek(0)
    return buf.read()


def get_hint_text(words: List[str], found: List[str]) -> str:
    """Word list shown in the grid caption.
    Unfound words: first letter + dashes  (e.g. W____)
    Found words:   full word with strikethrough
    """
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
