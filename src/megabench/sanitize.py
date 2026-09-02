"""SQL abstraction and privacy-preserving normalization.

The sanitizer intentionally does not try to preserve executable SQL perfectly.
Its job is to preserve workload shape while removing business identifiers and
literal values.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .util import stable_hash


SQL_KEYWORDS = {
    "ADD",
    "ALL",
    "ALTER",
    "AND",
    "ANY",
    "ARRAY",
    "AS",
    "ASC",
    "BETWEEN",
    "BY",
    "CASE",
    "CAST",
    "CREATE",
    "CROSS",
    "DATABASE",
    "DELETE",
    "DESC",
    "DISTINCT",
    "DROP",
    "ELSE",
    "END",
    "EXCEPT",
    "EXISTS",
    "FINAL",
    "FROM",
    "FULL",
    "GLOBAL",
    "GROUP",
    "HAVING",
    "IF",
    "ILIKE",
    "IN",
    "INNER",
    "INSERT",
    "INTERVAL",
    "INTO",
    "IS",
    "JOIN",
    "LEFT",
    "LIKE",
    "LIMIT",
    "NOT",
    "NULL",
    "ON",
    "OR",
    "ORDER",
    "OUTER",
    "OVER",
    "PARTITION",
    "PREWHERE",
    "RIGHT",
    "SAMPLE",
    "SELECT",
    "SEMI",
    "SETTINGS",
    "THEN",
    "TO",
    "UNION",
    "UPDATE",
    "USING",
    "VALUES",
    "WHEN",
    "WHERE",
    "WITH",
}

COMMON_FUNCTIONS = {
    "abs",
    "avg",
    "bitand",
    "bitmapand",
    "bitmapbuild",
    "bitmapcardinality",
    "bitmapcontains",
    "bitmapor",
    "bitmaptoarray",
    "casewithconstantexpression",
    "cityhash64",
    "coalesce",
    "concat",
    "count",
    "countif",
    "dateadd",
    "datediff",
    "datetrunc",
    "empty",
    "formatdatetime",
    "greatest",
    "if",
    "ifnull",
    "in",
    "intdiv",
    "isnotnull",
    "isnull",
    "least",
    "length",
    "like",
    "lower",
    "max",
    "min",
    "multiif",
    "now",
    "nullif",
    "position",
    "quantile",
    "quantileexact",
    "quantiles",
    "rand",
    "regexp",
    "replace",
    "round",
    "sum",
    "sumif",
    "todate",
    "todatetime",
    "tofloat32",
    "tofloat64",
    "toint32",
    "toint64",
    "tostring",
    "today",
    "tostartofday",
    "tostartofhour",
    "tostartofinterval",
    "tostartofminute",
    "tostartofmonth",
    "tostartofweek",
    "toupper",
    "uniq",
    "uniqexact",
    "upper",
}

TABLE_CONTEXT_KEYWORDS = {
    "FROM",
    "JOIN",
    "UPDATE",
    "INTO",
    "TABLE",
}

ALIAS_STOP_WORDS = SQL_KEYWORDS | {
    "ARRAY",
    "FORMAT",
    "LEFT",
    "RIGHT",
    "INNER",
    "OUTER",
    "FULL",
    "CROSS",
    "GLOBAL",
}

TOKEN_RE = re.compile(
    r"""
    (?P<string>'(?:''|\\'|[^'])*')
  | (?P<quoted>`[^`]*`|"(?:""|[^"])*")
  | (?P<number>\b\d+(?:\.\d+)?\b)
  | (?P<word>[A-Za-z_][A-Za-z0-9_$]*)
  | (?P<op><=|>=|<>|!=|==|::|[-+*/%<>=])
  | (?P<punct>[(),.;])
  | (?P<dot>\.)
  | (?P<other>\S)
    """,
    re.VERBOSE | re.DOTALL,
)

COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)
DATE_RE = re.compile(r"^'\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{1,2}:\d{1,2})?'$")


@dataclass(frozen=True)
class Token:
    kind: str
    text: str


@dataclass(frozen=True)
class SanitizedSQL:
    sql: str
    template_key: str
    placeholder_counts: dict[str, int]
    operator_counts: dict[str, int]
    table_count: int
    column_count: int
    unknown_function_count: int


def sanitize_sql(sql: str) -> SanitizedSQL:
    tokens = tokenize(sql)
    table_map, alias_map = _discover_tables_and_aliases(tokens)
    col_map: dict[str, str] = {}
    fn_map: dict[str, str] = {}
    placeholders: Counter[str] = Counter()
    operators: Counter[str] = Counter()
    out: list[str] = []

    i = 0
    paren_depth = 0
    while i < len(tokens):
        token = tokens[i]
        text = token.text
        upper = text.upper()

        if text == "(":
            out.append(text)
            paren_depth += 1
            i += 1
            continue

        if text == ")":
            out.append(text)
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue

        if token.kind == "string":
            placeholder = "{{date}}" if DATE_RE.match(text) else "{{str}}"
            placeholders[placeholder.strip("{}")] += 1
            out.append(placeholder)
            i += 1
            continue

        if token.kind == "number":
            placeholder = "{{float}}" if "." in text else "{{int}}"
            placeholders[placeholder.strip("{}")] += 1
            out.append(placeholder)
            i += 1
            continue

        if token.kind == "word":
            if paren_depth == 0 and upper in TABLE_CONTEXT_KEYWORDS:
                out.append(upper)
                i += 1
                if i < len(tokens) and tokens[i].text == "(":
                    continue
                compound, end = _read_identifier_compound(tokens, i)
                if compound:
                    out.append(table_map.get(compound, _next_table_name(table_map, compound)))
                    i = end
                    if i < len(tokens) and tokens[i].kind == "word" and tokens[i].text.upper() == "AS":
                        out.append("AS")
                        i += 1
                    if _is_alias_candidate(tokens, i):
                        alias = _clean_identifier(tokens[i].text)
                        out.append(alias_map.get(alias, alias))
                        i += 1
                continue

            compound, end = _read_identifier_compound(tokens, i)
            if compound and compound in table_map:
                out.append(table_map[compound])
                i = end
                continue

            if upper in SQL_KEYWORDS:
                out.append(upper)
                if upper in {"JOIN", "UNION", "GROUP", "ORDER", "WHERE", "PREWHERE", "HAVING", "LIMIT"}:
                    operators[upper.lower()] += 1
                i += 1
                continue

            clean = _clean_identifier(text)
            next_is_call = i + 1 < len(tokens) and tokens[i + 1].text == "("
            if next_is_call:
                lower = clean.lower()
                if lower in COMMON_FUNCTIONS:
                    out.append(lower)
                else:
                    if lower not in fn_map:
                        fn_map[lower] = f"fn_{len(fn_map) + 1:03d}"
                    out.append(fn_map[lower])
                i += 1
                continue

            if clean in alias_map:
                out.append(alias_map[clean])
            else:
                out.append(_next_column_name(col_map, clean))
            i += 1
            continue

        if token.kind == "quoted":
            clean = _clean_identifier(text)
            out.append(_next_column_name(col_map, clean))
            i += 1
            continue

        if token.kind == "op":
            operators[_operator_name(text)] += 1

        out.append(text)
        i += 1

    rendered = _render(out)
    template_key = stable_hash(rendered, 16)
    return SanitizedSQL(
        sql=rendered,
        template_key=template_key,
        placeholder_counts=dict(placeholders),
        operator_counts=dict(operators),
        table_count=len(table_map),
        column_count=len(col_map),
        unknown_function_count=len(fn_map),
    )


def tokenize(sql: str) -> list[Token]:
    cleaned = COMMENT_RE.sub(" ", sql or "")
    return [Token(match.lastgroup or "other", match.group(0)) for match in TOKEN_RE.finditer(cleaned)]


def _discover_tables_and_aliases(tokens: list[Token]) -> tuple[dict[str, str], dict[str, str]]:
    table_map: dict[str, str] = {}
    alias_map: dict[str, str] = {}
    i = 0
    paren_depth = 0
    while i < len(tokens):
        token = tokens[i]
        if token.text == "(":
            paren_depth += 1
            i += 1
            continue
        if token.text == ")":
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue
        if paren_depth != 0 or token.kind != "word" or token.text.upper() not in TABLE_CONTEXT_KEYWORDS:
            i += 1
            continue

        i += 1
        if i < len(tokens) and tokens[i].text == "(":
            continue

        compound, end = _read_identifier_compound(tokens, i)
        if not compound:
            continue

        table_name = _next_table_name(table_map, compound)
        i = end
        if i < len(tokens) and tokens[i].kind == "word" and tokens[i].text.upper() == "AS":
            i += 1
        if _is_alias_candidate(tokens, i):
            alias = _clean_identifier(tokens[i].text)
            alias_map[alias] = f"t{len(alias_map) + 1}"
            i += 1
        else:
            alias_map[_last_identifier_part(compound)] = table_name
    return table_map, alias_map


def _read_identifier_compound(tokens: list[Token], start: int) -> tuple[str | None, int]:
    parts: list[str] = []
    i = start
    expect_ident = True
    while i < len(tokens):
        token = tokens[i]
        if expect_ident and token.kind in {"word", "quoted"}:
            parts.append(_clean_identifier(token.text))
            expect_ident = False
            i += 1
            continue
        if not expect_ident and i < len(tokens) and token.text == ".":
            expect_ident = True
            i += 1
            continue
        break
    if not parts:
        return None, start
    return ".".join(parts), i


def _is_alias_candidate(tokens: list[Token], index: int) -> bool:
    if index >= len(tokens):
        return False
    token = tokens[index]
    if token.kind not in {"word", "quoted"}:
        return False
    return token.text.upper() not in ALIAS_STOP_WORDS


def _next_table_name(table_map: dict[str, str], raw: str) -> str:
    if raw not in table_map:
        table_map[raw] = f"events_wide_table_{len(table_map) + 1:03d}"
    return table_map[raw]


def _next_column_name(col_map: dict[str, str], raw: str) -> str:
    if raw not in col_map:
        col_map[raw] = f"c_{len(col_map) + 1:04d}"
    return col_map[raw]


def _clean_identifier(text: str) -> str:
    text = text.strip()
    if (text.startswith("`") and text.endswith("`")) or (text.startswith('"') and text.endswith('"')):
        text = text[1:-1]
    return text.lower()


def _last_identifier_part(compound: str) -> str:
    return compound.split(".")[-1]


def _operator_name(text: str) -> str:
    return {
        "=": "eq",
        "!=": "neq",
        "<>": "neq",
        "<": "lt",
        "<=": "lte",
        ">": "gt",
        ">=": "gte",
    }.get(text, "op")


def _render(tokens: list[str]) -> str:
    pieces: list[str] = []
    prev = ""
    for token in tokens:
        if not token:
            continue
        if not pieces:
            pieces.append(token)
        elif token in {".", ",", ")", ";"}:
            pieces.append(token)
        elif prev in {"(", "."}:
            pieces.append(token)
        elif token == "(":
            if prev.lower().startswith(("fn_", "count", "sum", "avg", "min", "max", "uniq", "to", "if", "coalesce")):
                pieces.append(token)
            else:
                pieces.append(" " + token)
        else:
            pieces.append(" " + token)
        prev = token
    rendered = "".join(pieces)
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered
