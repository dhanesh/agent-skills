# string helpers

import re


def slugify(s: str) -> str:
    """Convert a string to a URL-friendly slug.

    Lowercases the string and replaces every run of non-alphanumeric
    characters with a single hyphen, stripping leading/trailing hyphens.
    """
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')
