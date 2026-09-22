"""Wordle word sources.

Hidden answers come ONLY from ``wordle_words.cleaned.txt``.
Guess validation comes from the larger JSON dictionaries:
``wordle_words_5.json`` and ``wordle_words_6.json``.
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ANSWER_FILE = BASE_DIR / "wordle_words.cleaned.txt"
VALID_WORD_LIST_FILES = {
    5: BASE_DIR / "wordle_words_5.json",
    6: BASE_DIR / "wordle_words_6.json",
}


def _clean_words(raw_words, length: int) -> list[str]:
    seen = set()
    result = []
    for raw_word in raw_words:
        word = str(raw_word).strip().upper()
        if len(word) == length and word.isalpha() and word not in seen:
            result.append(word)
            seen.add(word)
    return result


def _load_answers() -> dict[int, list[str]]:
    """Load the hidden-answer pool exclusively from wordle_words.cleaned.txt."""
    answers = {5: [], 6: []}
    if not ANSWER_FILE.exists():
        return answers

    with ANSWER_FILE.open("r", encoding="utf-8") as file:
        raw_words = file.readlines()

    for length in answers:
        answers[length] = _clean_words(raw_words, length)
    return answers


def _load_valid_words() -> dict[int, list[str]]:
    """Load player guesses from the larger 5/6-letter JSON dictionaries."""
    words_by_length = {5: [], 6: []}

    for length, json_file in VALID_WORD_LIST_FILES.items():
        if not json_file.exists():
            continue
        with json_file.open("r", encoding="utf-8") as file:
            raw_words = json.load(file)
        words_by_length[length] = _clean_words(raw_words, length)

    # Compatibility fallback if a JSON file is missing.
    if not all(words_by_length.values()) and ANSWER_FILE.exists():
        with ANSWER_FILE.open("r", encoding="utf-8") as file:
            raw_words = file.readlines()
        for length in words_by_length:
            if not words_by_length[length]:
                words_by_length[length] = _clean_words(raw_words, length)

    return words_by_length


ANSWERS_BY_LENGTH = _load_answers()
WORDS_BY_LENGTH = _load_valid_words()
