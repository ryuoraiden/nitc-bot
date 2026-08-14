"""Small text helpers shared by the command cogs."""
from __future__ import annotations


def expand_line_breaks(value: str) -> str:
    """Allow multiline input from Discord's single-line slash-command fields.

    Discord offers no way to type a newline into a string option, so the
    commands that accept prose (`/stick`, `/giveaway create`) take a literal
    backslash-n and turn it into a real line break here.
    """
    return value.replace(r"\n", "\n")
