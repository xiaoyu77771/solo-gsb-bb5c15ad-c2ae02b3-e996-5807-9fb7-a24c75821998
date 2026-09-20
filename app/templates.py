r"""Replacement template handling.

Templates use Python ``re`` backreference syntax (``\1``, ``\g<name>``,
``\g<1>``). The common ``$1``/``${name}``/``$<name>``/``$name`` dollar
style is accepted too and translated before validation. ``$`` without a
valid reference is kept as a literal dollar sign.
"""

from __future__ import annotations

import re

# Either a Python-style backreference or a dollar-style one.
_TOKEN_RE = re.compile(
    r"""
    \\g(?P<gname><\w+>)
  | \\(?P<num>[0-9]{1,2})
  | \$(?P<dollarbrace>\{(?P<dbn>\d+|\w+)\})
  | \$(?P<dollarlang><(?P<dln>\w+)>)
  | \$(?P<dollarnum>\d+)
  | \$(?P<dollarname>[A-Za-z_]\w*)
  | \$&
    """,
    re.VERBOSE,
)


class TemplateError(Exception):
    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.position = position


def normalize_template(template: str) -> str:
    r"""Translate ``$``-style references into ``re`` backslash references.

    ``$1`` -> ``\1``, ``${name}`` / ``$<name>`` / ``$name`` ->
    ``\g<name>``, ``$&`` -> ``\g<0>``. Anything else (including a lone
    ``$``) is emitted unchanged. Python-style references are copied
    verbatim.
    """

    out: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(template):
        out.append(template[pos:m.start()])
        pos = m.end()
        if m.group("gname") is not None:
            out.append(m.group(0))
        elif m.group("num") is not None:
            out.append(m.group(0))
        elif m.group("dollarbrace") is not None:
            name = m.group("dbn")
            out.append(rf"\g<{name}>")
        elif m.group("dollarlang") is not None:
            out.append(rf"\g<{m.group('dln')}>")
        elif m.group("dollarnum") is not None:
            digits = m.group("dollarnum")
            # \12 is ambiguous (group 12 vs group 1 + '2'); braces remove
            # the ambiguity, and multi-digit references need them anyway.
            out.append(rf"\g<{digits}>")
        elif m.group("dollarname") is not None:
            out.append(rf"\g<{m.group('dollarname')}>")
        else:  # $& -> whole match
            out.append(r"\g<0>")
    out.append(template[pos:])
    return "".join(out)


class _PatternView:
    """Minimal view over a compiled pattern needed by re._parser."""

    def __init__(self, pattern: re.Pattern[str]):
        self.groups = pattern.groups
        self.groupindex = pattern.groupindex


def validate_template(template: str, pattern: re.Pattern[str]) -> None:
    r"""Raise TemplateError if the template references missing groups.

    The normalized template is guaranteed to contain braced ``\g<...>`` or
    ``\N`` references only, so ``re._parser.parse_template`` validates the
    complete reference set up front (it never silently ignores a bad ref).
    """
    try:
        from re import _parser

        _parser.parse_template(template, _PatternView(pattern))
    except TemplateError:
        raise
    except (re.error, IndexError, ValueError) as exc:
        # re.error carries .pos; IndexError ("unknown group name") does not.
        position = getattr(exc, "pos", None)
        raise TemplateError(str(exc), position) from exc
