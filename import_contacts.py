"""One-shot bulk import of Google Contacts CSV into the contacts table.

Usage: python import_contacts.py
Reads contacts.csv from the same directory.
"""

import csv
import sys
from dotenv import load_dotenv

load_dotenv()

from config import settings
from database import _get_client, add_contact

CSV_PATH = "contacts.csv"


def split_multi(value: str) -> list[str]:
    """Google CSV uses ' ::: ' as separator for multi-valued fields."""
    if not value:
        return []
    parts = [p.strip() for p in value.split(":::")]
    return [p for p in parts if p]


def build_name(row: dict) -> str:
    parts = [
        (row.get("First Name") or "").strip(),
        (row.get("Middle Name") or "").strip(),
        (row.get("Last Name") or "").strip(),
    ]
    name = " ".join(p for p in parts if p)
    if name:
        return name
    org = (row.get("Organization Name") or "").strip()
    if org:
        return org
    return (row.get("Nickname") or "").strip()


def build_aliases(row: dict, primary_name: str) -> list[str]:
    aliases = set()
    nick = (row.get("Nickname") or "").strip()
    if nick and nick != primary_name:
        aliases.add(nick)
    org = (row.get("Organization Name") or "").strip()
    if org and org != primary_name:
        aliases.add(org)
    file_as = (row.get("File As") or "").strip()
    if file_as and file_as != primary_name:
        aliases.add(file_as)
    return sorted(aliases)


def build_notes(row: dict) -> str:
    bits = []
    title = (row.get("Organization Title") or "").strip()
    dept = (row.get("Organization Department") or "").strip()
    if title:
        bits.append(title)
    if dept:
        bits.append(dept)
    notes = (row.get("Notes") or "").strip()
    if notes:
        bits.append(notes)
    return " | ".join(bits)


def main():
    chat_id = settings.MIKI_OWNER_CHAT_ID
    if not chat_id:
        print("MIKI_OWNER_CHAT_ID missing — aborting")
        sys.exit(1)

    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"loaded {len(rows)} rows from {CSV_PATH}")

    inserted = 0
    skipped_empty = 0
    failed = 0
    for idx, row in enumerate(rows, 1):
        name = build_name(row)
        emails = split_multi(row.get("E-mail 1 - Value") or "") + \
                 split_multi(row.get("E-mail 2 - Value") or "") + \
                 split_multi(row.get("E-mail 3 - Value") or "")
        phones = split_multi(row.get("Phone 1 - Value") or "") + \
                 split_multi(row.get("Phone 2 - Value") or "")

        if not name and not emails and not phones:
            skipped_empty += 1
            continue

        if not name:
            name = emails[0] if emails else (phones[0] if phones else "")

        primary_email = emails[0] if emails else None
        extra_emails = emails[1:] if len(emails) > 1 else []
        primary_phone = phones[0] if phones else None
        extra_phones = phones[1:] if len(phones) > 1 else []

        aliases = build_aliases(row, name)
        for e in extra_emails:
            aliases.append(e)
        for p in extra_phones:
            aliases.append(p)
        notes = build_notes(row) or None

        try:
            add_contact(
                chat_id=chat_id,
                name=name,
                email=primary_email,
                phone=primary_phone,
                aliases=aliases or None,
                notes=notes,
            )
            inserted += 1
            if inserted % 50 == 0:
                print(f"  progress: {inserted} inserted...")
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  FAILED row {idx} ({name!r}): {e}")

    print()
    print("=== DONE ===")
    print(f"inserted/updated: {inserted}")
    print(f"skipped empty:    {skipped_empty}")
    print(f"failed:           {failed}")
    print(f"total processed:  {len(rows)}")


if __name__ == "__main__":
    main()
