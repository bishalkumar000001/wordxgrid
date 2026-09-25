"""Large Word Chain dictionary (4-20 letters).

The word list is stored in chain_words.txt.gz so the source tree stays manageable.
"""
from pathlib import Path
import gzip
from collections import defaultdict

CHAIN_WORDS_BY_LENGTH = defaultdict(list)
_chain_words = set()
_DATA = Path(__file__).with_name("chain_words.txt.gz")

with gzip.open(_DATA, "rt", encoding="utf-8") as f:
    for raw in f:
        word = raw.strip().upper()
        if 4 <= len(word) <= 20 and word.isalpha():
            _chain_words.add(word)
            CHAIN_WORDS_BY_LENGTH[len(word)].append(word)

# Keep the public names used by mini_games.py.
CHAIN_WORDS = _chain_words
CHAIN_WORDS_BY_LENGTH = dict(CHAIN_WORDS_BY_LENGTH)
