"""Keys for the faces' per-word spacing tables."""
import hashlib


def word_key(text: str) -> str:
    """The SHA-256 of a menu word, like the text table's row keys: the tables hold no official word."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
