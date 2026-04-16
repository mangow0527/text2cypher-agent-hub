from __future__ import annotations


def append_item_under_section(document: str, section: str, item: str) -> str:
    marker = f"## {section}"
    if marker not in document:
        suffix = f"\n\n## {section}\n\n{item}\n"
        return f"{document.rstrip()}{suffix}"

    before, after = document.split(marker, 1)
    lines = after.splitlines()
    insertion_index = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        if line.startswith("## "):
            insertion_index = index
            break

    updated_lines = lines[:insertion_index] + ["", item] + lines[insertion_index:]
    return f"{before}{marker}" + "\n".join(updated_lines).rstrip() + "\n"
