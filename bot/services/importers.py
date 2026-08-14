import csv
from io import StringIO


def decode_text_payload(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8-sig", payload, 0, 1, "unsupported text encoding")


def parse_text_cards(payload: str) -> list[tuple[str, str, list[str], bool]]:
    text = payload.strip()
    if not text:
        return []

    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t;,")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel

    rows: list[tuple[str, str, list[str], bool]] = []
    reader = csv.reader(StringIO(text), dialect)
    for raw_row in reader:
        row = [cell.strip() for cell in raw_row]
        if len(row) < 2 or not row[0] or not row[1]:
            continue
        tags: list[str] = []
        if len(row) >= 3 and row[2]:
            tags = [tag.strip() for tag in row[2].replace(",", " ").split() if tag.strip()]
        create_reverse = len(row) >= 4 and row[3].lower() in {"1", "yes", "true", "reverse"}
        rows.append((row[0], row[1], tags, create_reverse))
    return rows
