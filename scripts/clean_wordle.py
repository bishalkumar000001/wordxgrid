#!/usr/bin/env python3
from pathlib import Path
p = Path('wordle_words.txt')
out = Path('wordle_words.cleaned.txt')
seen = set()
lines_out = []
for line in p.read_text(encoding='utf-8').splitlines():
    w = line.strip()
    if not w:
        continue
    # normalize to upper
    W = w.upper()
    # keep only alphabetic words
    if not W.isalpha():
        continue
    if len(W) not in (5,6):
        continue
    if W in seen:
        continue
    seen.add(W)
    lines_out.append(W)
out.write_text('\n'.join(lines_out)+('\n' if lines_out else ''), encoding='utf-8')
print(f'Wrote {len(lines_out)} cleaned words to {out}')
