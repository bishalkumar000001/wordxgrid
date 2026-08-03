"""Wordle-specific allowed words.

Loads 5- and 6-letter English words from the local "wordle_words.txt" file.
"""

from pathlib import Path

WORD_LIST_FILE = Path(__file__).with_suffix('.txt')


def _load_words() -> dict[int, list[str]]:
    words_by_length = {5: [], 6: []}
    with WORD_LIST_FILE.open('r', encoding='utf-8') as file:
        for line in file:
            word = line.strip().upper()
            if len(word) in words_by_length and word.isalpha():
                words_by_length[len(word)].append(word)
    return words_by_length


WORDS_BY_LENGTH = _load_words()
