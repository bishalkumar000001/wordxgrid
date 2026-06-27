import random
import io
from PIL import Image, ImageDraw, ImageFont
from typing import List, Tuple, Dict, Optional

DIRECTIONS = [
    (0, 1),   # right
    (1, 0),   # down
    (0, -1),  # left
    (-1, 0),  # up
    (1, 1),   # down-right
    (1, -1),  # down-left
    (-1, 1),  # up-right
    (-1, -1), # up-left
]

# 12 distinct highlight colors (R, G, B, Alpha) — vibrant but transparent
HIGHLIGHT_COLORS = [
    (255, 80,  80,  140),   # red
    (80,  200, 120, 140),   # green
    (80,  160, 255, 140),   # blue
    (255, 200, 60,  140),   # yellow
    (200, 80,  255, 140),   # purple
    (60,  220, 220, 140),   # cyan
    (255, 130, 40,  140),   # orange
    (255, 100, 180, 140),   # pink
    (100, 255, 160, 140),   # mint
    (160, 130, 255, 140),   # lavender
    (255, 220, 100, 140),   # gold
    (80,  200, 200, 140),   # teal
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


def build_grid(words: List[str]) -> Tuple[List[List[str]], dict]:
    max_len = max(len(w) for w in words)
    size = max(max_len + 4, len(words) + 2, 12)

    for attempt in range(500):
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

    # fallback: larger grid
    size += 4
    grid = [['.' for _ in range(size)] for _ in range(size)]
    placed = {}
    for word in words:
        for _ in range(1000):
            dr, dc = random.choice(DIRECTIONS)
            row = random.randint(0, size - 1)
            col = random.randint(0, size - 1)
            if can_place(grid, word, row, col, dr, dc):
                place_word(grid, word, row, col, dr, dc)
                placed[word] = (row, col, dr, dc)
                break
    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    for r in range(size):
        for c in range(size):
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


def render_grid_image(
    grid: List[List[str]],
    title: str = "WORD GRID CHALLENGE",
    placed_words: Optional[Dict[str, tuple]] = None,
    found_words: Optional[List[str]] = None,
    word_order: Optional[List[str]] = None,
) -> bytes:
    """
    Render the word grid as a PNG image.

    found_words: list of words that have been correctly guessed.
    placed_words: dict mapping word -> (row, col, dr, dc).
    word_order: full ordered list of words (for stable color assignment).
    """
    rows = len(grid)
    cols = len(grid[0])

    cell_size = 42
    padding = 20
    title_height = 50

    img_w = cols * cell_size + padding * 2
    img_h = rows * cell_size + padding * 2 + title_height

    # Work in RGBA so we can alpha-composite the highlights
    base = Image.new('RGBA', (img_w, img_h), (30, 30, 30, 255))
    draw = ImageDraw.Draw(base)

    font = _load_font(22)
    title_font = _load_font(18)

    # Title bar
    draw.rectangle([0, 0, img_w, title_height], fill=(20, 20, 60, 255))
    title_text = f"WG {title} WG"
    try:
        bbox = draw.textbbox((0, 0), title_text, font=title_font)
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = len(title_text) * 10
    draw.text(((img_w - tw) // 2, 14), title_text, fill=(255, 220, 50, 255), font=title_font)

    # Grid outer border
    draw.rectangle(
        [padding - 4, title_height + padding - 4,
         img_w - padding + 4, img_h - padding + 4],
        fill=(15, 15, 40, 255), outline=(80, 80, 120, 255), width=2,
    )

    # Build a map: (r, c) -> highlight RGBA for found words
    highlight_map: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
    if placed_words and found_words and word_order:
        for word in found_words:
            if word not in placed_words:
                continue
            color_idx = word_order.index(word) % len(HIGHLIGHT_COLORS)
            color = HIGHLIGHT_COLORS[color_idx]
            wr, wc, dr, dc = placed_words[word]
            for i in range(len(word)):
                r = wr + dr * i
                c = wc + dc * i
                highlight_map[(r, c)] = color

    # Draw each cell
    for r in range(rows):
        for c in range(cols):
            x = padding + c * cell_size
            y = title_height + padding + r * cell_size

            # Base cell background
            cell_color = (25, 25, 55, 255) if (r + c) % 2 == 0 else (20, 20, 48, 255)
            draw.rectangle(
                [x + 1, y + 1, x + cell_size - 2, y + cell_size - 2],
                fill=cell_color,
            )
            draw.rectangle(
                [x, y, x + cell_size - 1, y + cell_size - 1],
                outline=(60, 60, 100, 255), width=1,
            )

            # Highlight overlay for found words
            if (r, c) in highlight_map:
                hr, hg, hb, ha = highlight_map[(r, c)]
                overlay = Image.new('RGBA', (cell_size - 3, cell_size - 3), (hr, hg, hb, ha))
                base.alpha_composite(overlay, dest=(x + 1, y + 1))
                # Redraw the border on top so the grid stays visible
                draw2 = ImageDraw.Draw(base)
                draw2.rectangle(
                    [x, y, x + cell_size - 1, y + cell_size - 1],
                    outline=(hr, hg, hb, 200), width=2,
                )

            # Letter
            ch = grid[r][c]
            try:
                bbox = draw.textbbox((0, 0), ch, font=font)
                cw = bbox[2] - bbox[0]
                ch_h = bbox[3] - bbox[1]
            except Exception:
                cw = 14
                ch_h = 14

            tx = x + (cell_size - cw) // 2
            ty = y + (cell_size - ch_h) // 2 - 1

            # Found-word letters are bright white; others are normal
            letter_color = (255, 255, 255, 255) if (r, c) in highlight_map else (200, 200, 240, 255)
            draw.text((tx, ty), ch, fill=letter_color, font=font)

    # Convert to RGB for PNG output (Telegram needs RGB/JPEG-safe)
    final = Image.new('RGB', base.size, (30, 30, 30))
    final.paste(base, mask=base.split()[3])

    buf = io.BytesIO()
    final.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf.read()


def get_hint_text(words: List[str], found: List[str]) -> str:
    lines = []
    for i, w in enumerate(words, 1):
        if w in found:
            lines.append(f"✅ {i}. <s>{w}</s>")
        else:
            hint = w[0] + '-' * (len(w) - 1)
            lines.append(f"🔤 {i}. {hint} ({len(w)})")
    return "\n".join(lines)


def make_hint_for_word(word: str, revealed: int = 2) -> str:
    visible = min(revealed, len(word) - 1)
    return word[:visible] + '-' * (len(word) - visible) + f" ({len(word)})"
