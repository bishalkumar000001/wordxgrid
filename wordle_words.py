"""Wordle-specific allowed words.

The supplied JSON dictionaries are used for the 5- and 6-letter modes.
The original text file remains as a fallback for older deployments that do
not yet have the JSON files.
"""

import json
from pathlib import Path

WORD_LIST_FILE = Path(__file__).with_suffix('.txt')
WORD_LIST_FILES = {
    5: Path(__file__).with_name("wordle_words_5.json"),
    6: Path(__file__).with_name("wordle_words_6.json"),
}


def _load_words() -> dict[int, list[str]]:
    words_by_length = {5: [], 6: []}

    for length, json_file in WORD_LIST_FILES.items():
        if json_file.exists():
            with json_file.open("r", encoding="utf-8") as file:
                raw_words = json.load(file)
        else:
            raw_words = []

        seen = set()
        for raw_word in raw_words:
            word = str(raw_word).strip().upper()
            if len(word) == length and word.isalpha() and word not in seen:
                words_by_length[length].append(word)
                seen.add(word)

    # Keep the bot compatible with an older installation during deployment.
    if not all(words_by_length.values()):
        with WORD_LIST_FILE.open("r", encoding="utf-8") as file:
            for line in file:
                word = line.strip().upper()
                if len(word) in words_by_length and word.isalpha():
                    if word not in words_by_length[len(word)]:
                        words_by_length[len(word)].append(word)

    return words_by_length


WORDS_BY_LENGTH = _load_words()
