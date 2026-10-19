from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    text: str
    start: int
    end: int


def split_document(text, title, tokenizer, target=300, overlap=40):
    """Use original tokenizer offsets: no decoding round trip and no truncation."""
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    offsets = [(a, b) for a, b in offsets if b > a]
    prefix = f"passage: {title}\n"
    overhead = len(tokenizer(prefix, add_special_tokens=True)["input_ids"])
    capacity = min(target, 512 - overhead)
    if capacity <= overlap:
        raise ValueError("title/prefix leaves insufficient token capacity")
    chunks = []
    cursor = 0
    while cursor < len(offsets):
        stop = min(cursor + capacity, len(offsets))
        start_char, end_char = offsets[cursor][0], offsets[stop - 1][1]
        candidate = text[start_char:end_char]
        # Tokenization at a new boundary can differ. Shrink rather than truncate.
        while len(tokenizer(prefix + candidate, add_special_tokens=True)["input_ids"]) > 512:
            stop -= 1
            if stop <= cursor + overlap:
                raise ValueError("cannot fit a nonempty chunk under 512 tokens")
            end_char = offsets[stop - 1][1]
            candidate = text[start_char:end_char]
        chunks.append(Chunk(candidate, start_char, end_char))
        if stop == len(offsets):
            break
        cursor = stop - overlap
    return chunks
