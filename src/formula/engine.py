# -*- coding: utf-8 -*-
import re
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class _TokenType(Enum):
    NUMBER = auto()
    IDENT = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    GT = auto()
    LT = auto()
    GE = auto()
    LE = auto()
    EQ = auto()
    NE = auto()
    AND = auto()
    OR = auto()
    EOF = auto()


class _Token:
    __slots__ = ("type", "value")

    def __init__(self, type: _TokenType, value: Any = None):
        self.type = type
        self.value = value

    def __repr__(self):
        return f"Token({self.type}, {self.value!r})"


_KEYWORDS = {"AND": _TokenType.AND, "OR": _TokenType.OR}

_TOKEN_SPEC = [
    (r"\d+\.?\d*", _TokenType.NUMBER),
    (r"[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*", _TokenType.IDENT),
    (r"\(", _TokenType.LPAREN),
    (r"\)", _TokenType.RPAREN),
    (r",", _TokenType.COMMA),
    (r"\+>", _TokenType.GE),
    (r"\+<", _TokenType.LE),
    (r">=", _TokenType.GE),
    (r"<=", _TokenType.LE),
    (r"<>", _TokenType.NE),
    (r">", _TokenType.GT),
    (r"<", _TokenType.LT),
    (r"=", _TokenType.EQ),
    (r"\+", _TokenType.PLUS),
    (r"-", _TokenType.MINUS),
    (r"\*", _TokenType.STAR),
    (r"/", _TokenType.SLASH),
]

_TOKEN_RE = re.compile(
    "|".join(f"(?P<T{i}>{pat})" for i, (pat, _) in enumerate(_TOKEN_SPEC))
)


def _tokenize(expr: str) -> List[_Token]:
    tokens: List[_Token] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if m and m.start() == pos:
            for i, (_, tt) in enumerate(_TOKEN_SPEC):
                val = m.group(f"T{i}")
                if val is not None:
                    if tt == _TokenType.IDENT:
                        upper = val.upper()
                        if upper in _KEYWORDS:
                            tokens.append(_Token(_KEYWORDS[upper], upper))
                        else:
                            tokens.append(_Token(tt, val))
                    elif tt == _TokenType.NUMBER:
                        tokens.append(_Token(tt, float(val)))
                    else:
                        tokens.append(_Token(tt, val))
                    pos = m.end()
                    break
            else:
                pos += 1
        else:
            pos += 1
    tokens.append(_Token(_TokenType.EOF))
    return tokens


class _Parser:
    def __init__(self, tokens: List[_Token]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> _Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else _Token(_TokenType.EOF)

    def _advance(self) -> _Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, tt: _TokenType) -> _Token:
        tok = self._advance()
        if tok.type != tt:
            raise SyntaxError(f"Expected {tt}, got {tok}")
        return tok

    def parse(self):
        node = self._or_expr()
        return node

    def _or_expr(self):
        left = self._and_expr()
        while self._peek().type == _TokenType.OR:
            self._advance()
            right = self._and_expr()
            left = ("OR", left, right)
        return left

    def _and_expr(self):
        left = self._comp_expr()
        while self._peek().type == _TokenType.AND:
            self._advance()
            right = self._comp_expr()
            left = ("AND", left, right)
        return left

    def _comp_expr(self):
        left = self._add_expr()
        while self._peek().type in (
            _TokenType.GT, _TokenType.LT, _TokenType.GE,
            _TokenType.LE, _TokenType.EQ, _TokenType.NE,
        ):
            op = self._advance()
            right = self._add_expr()
            left = (op.value, left, right)
        return left

    def _add_expr(self):
        left = self._mul_expr()
        while self._peek().type in (_TokenType.PLUS, _TokenType.MINUS):
            op = self._advance()
            right = self._mul_expr()
            left = (op.value, left, right)
        return left

    def _mul_expr(self):
        left = self._unary_expr()
        while self._peek().type in (_TokenType.STAR, _TokenType.SLASH):
            op = self._advance()
            right = self._unary_expr()
            left = (op.value, left, right)
        return left

    def _unary_expr(self):
        if self._peek().type == _TokenType.MINUS:
            self._advance()
            operand = self._primary()
            return ("NEG", operand)
        if self._peek().type == _TokenType.PLUS:
            self._advance()
            return self._primary()
        return self._primary()

    def _primary(self):
        tok = self._peek()
        if tok.type == _TokenType.NUMBER:
            self._advance()
            return ("NUM", tok.value)
        if tok.type == _TokenType.IDENT:
            self._advance()
            if self._peek().type == _TokenType.LPAREN:
                self._advance()
                args: list = []
                if self._peek().type != _TokenType.RPAREN:
                    args.append(self.parse())
                    while self._peek().type == _TokenType.COMMA:
                        self._advance()
                        args.append(self.parse())
                self._expect(_TokenType.RPAREN)
                return ("CALL", tok.value.upper(), args)
            return ("VAR", tok.value.upper())
        if tok.type == _TokenType.LPAREN:
            self._advance()
            node = self.parse()
            self._expect(_TokenType.RPAREN)
            return node
        raise SyntaxError(f"Unexpected token: {tok}")


_BUILTIN_VARS = {
    "CLOSE", "C", "OPEN", "O", "HIGH", "H", "LOW", "L",
    "VOL", "VOLUME", "AMOUNT", "PCT_CHG",
}


def _resolve_var(name: str, env: Dict[str, pd.Series], df: pd.DataFrame) -> pd.Series:
    if name in env:
        return env[name]
    col_map = {
        "CLOSE": "close", "C": "close",
        "OPEN": "open", "O": "open",
        "HIGH": "high", "H": "high",
        "LOW": "low", "L": "low",
        "VOL": "volume", "VOLUME": "volume",
        "AMOUNT": "amount",
        "PCT_CHG": "pct_chg",
    }
    col = col_map.get(name)
    if col and col in df.columns:
        s = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        env[name] = s
        return s
    raise NameError(f"Undefined variable: {name}")


def _fn_ma(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("MA(x, n) requires 2 arguments")
    x, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    return x.rolling(window=n_val, min_periods=1).mean()


def _fn_ema(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("EMA(x, n) requires 2 arguments")
    x, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    return x.ewm(span=n_val, adjust=False).mean()


def _fn_sma(args: List[pd.Series], **_) -> pd.Series:
    if len(args) < 2 or len(args) > 3:
        raise ValueError("SMA(x, n, m) requires 2-3 arguments")
    x = args[0]
    n = int(args[1].iloc[-1]) if isinstance(args[1], pd.Series) else int(args[1])
    m = int(args[2].iloc[-1]) if len(args) > 2 and isinstance(args[2], pd.Series) else (int(args[2]) if len(args) > 2 else 1)
    result = np.full(len(x), np.nan)
    result[0] = x.iloc[0]
    weight = m / n
    for i in range(1, len(x)):
        result[i] = (x.iloc[i] * weight) + result[i - 1] * (1 - weight)
    return pd.Series(result, index=x.index)


def _fn_cross(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("CROSS(a, b) requires 2 arguments")
    a, b = args
    prev_a = a.shift(1)
    prev_b = b.shift(1)
    cross_up = (prev_a <= prev_b) & (a > b)
    return cross_up.astype(float)


def _fn_ref(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("REF(x, n) requires 2 arguments")
    x, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    return x.shift(n_val).fillna(0.0)


def _fn_hhv(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("HHV(x, n) requires 2 arguments")
    x, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    return x.rolling(window=n_val, min_periods=1).max()


def _fn_llv(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("LLV(x, n) requires 2 arguments")
    x, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    return x.rolling(window=n_val, min_periods=1).min()


def _fn_std(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("STD(x, n) requires 2 arguments")
    x, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    return x.rolling(window=n_val, min_periods=1).std()


def _fn_abs(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 1:
        raise ValueError("ABS(x) requires 1 argument")
    return args[0].abs()


def _fn_max(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("MAX(a, b) requires 2 arguments")
    return np.maximum(args[0], args[1])


def _fn_min(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("MIN(a, b) requires 2 arguments")
    return np.minimum(args[0], args[1])


def _fn_if(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 3:
        raise ValueError("IF(cond, true_val, false_val) requires 3 arguments")
    cond, true_val, false_val = args
    if isinstance(cond, pd.Series):
        mask = cond != 0
    else:
        mask = pd.Series(cond != 0, index=true_val.index if isinstance(true_val, pd.Series) else false_val.index)
    result = pd.Series(np.nan, index=mask.index)
    result[mask] = true_val[mask] if isinstance(true_val, pd.Series) else true_val
    result[~mask] = false_val[~mask] if isinstance(false_val, pd.Series) else false_val
    return result


def _fn_stickline(args: List[pd.Series], **_) -> pd.Series:
    if len(args) < 3:
        raise ValueError("STICKLINE requires at least 3 arguments")
    cond = args[0]
    price1 = args[1]
    price2 = args[2]
    mask = cond != 0
    result = pd.Series(0.0, index=cond.index)
    result[mask] = price1[mask] if isinstance(price1, pd.Series) else price1
    return result


def _fn_drawicon(args: List[pd.Series], **_) -> pd.Series:
    if len(args) < 2:
        raise ValueError("DRAWICON requires at least 2 arguments")
    cond = args[0]
    price = args[1]
    mask = cond != 0
    result = pd.Series(np.nan, index=cond.index)
    result[mask] = price[mask] if isinstance(price, pd.Series) else price
    return result


def _fn_drawtext(args: List[pd.Series], **_) -> pd.Series:
    if len(args) < 2:
        raise ValueError("DRAWTEXT requires at least 2 arguments")
    cond = args[0]
    price = args[1]
    mask = cond != 0
    result = pd.Series(np.nan, index=cond.index)
    result[mask] = price[mask] if isinstance(price, pd.Series) else price
    return result


def _fn_winner(args: List[pd.Series], df: Optional[pd.DataFrame] = None, **_) -> pd.Series:
    if len(args) < 1:
        raise ValueError("WINNER requires at least 1 argument")
    price = args[0]
    close = df["close"] if df is not None and "close" in df.columns else price
    result = pd.Series(0.5, index=price.index)
    if len(args) >= 2:
        ref_price = args[1]
        mask = close <= ref_price
        result[mask] = 0.9
        result[~mask] = 0.3
    else:
        result = (close.rank(pct=True) * 0.8 + 0.1).clip(0.1, 0.95)
    return result


def _fn_cost(args: List[pd.Series], df: Optional[pd.DataFrame] = None, **_) -> pd.Series:
    if len(args) < 1:
        raise ValueError("COST requires at least 1 argument")
    pct = args[0]
    close = df["close"] if df is not None and "close" in df.columns else pd.Series(0, index=pct.index)
    pct_val = pct.iloc[-1] if isinstance(pct, pd.Series) else float(pct)
    offset = (pct_val - 50.0) / 100.0
    return close * (1.0 + offset)


def _fn_sum(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("SUM(x, n) requires 2 arguments")
    x, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    return x.rolling(window=n_val, min_periods=1).sum()


def _fn_count(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("COUNT(cond, n) requires 2 arguments")
    cond, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    mask = (cond != 0).astype(float)
    return mask.rolling(window=n_val, min_periods=1).sum()


def _fn_every(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("EVERY(cond, n) requires 2 arguments")
    cond, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    mask = (cond != 0).astype(float)
    return mask.rolling(window=n_val, min_periods=1).min()


def _fn_exist(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("EXIST(cond, n) requires 2 arguments")
    cond, n = args
    n_val = int(n.iloc[-1]) if isinstance(n, pd.Series) else int(n)
    mask = (cond != 0).astype(float)
    return mask.rolling(window=n_val, min_periods=1).max()


def _fn_between(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 3:
        raise ValueError("BETWEEN(x, a, b) requires 3 arguments")
    x, a, b = args
    low_val = np.minimum(a, b)
    high_val = np.maximum(a, b)
    return ((x >= low_val) & (x <= high_val)).astype(float)


def _fn_not(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 1:
        raise ValueError("NOT(x) requires 1 argument")
    return (args[0] == 0).astype(float)


def _fn_zig(args: List[pd.Series], **_) -> pd.Series:
    if len(args) < 2:
        raise ValueError("ZIG(data, pct) requires 2 arguments")
    data = args[0]
    pct = args[1]
    pct_val = float(pct.iloc[-1]) if isinstance(pct, pd.Series) else float(pct)
    values = data.values.astype(float)
    n = len(values)
    if n < 2:
        return data.copy()
    pivots = [0]
    direction = 0
    last_extreme_idx = 0
    last_extreme_val = values[0]
    for i in range(1, n):
        if direction == 0:
            change_pct = abs(values[i] - last_extreme_val) / max(abs(last_extreme_val), 1e-10) * 100
            if change_pct >= pct_val:
                direction = 1 if values[i] > last_extreme_val else -1
                pivots.append(i)
                last_extreme_idx = i
                last_extreme_val = values[i]
            elif (direction == 0 and values[i] > last_extreme_val) or (direction == 0 and values[i] < last_extreme_val):
                if values[i] > last_extreme_val:
                    last_extreme_idx = i
                    last_extreme_val = values[i]
                elif values[i] < last_extreme_val:
                    last_extreme_idx = i
                    last_extreme_val = values[i]
        elif direction == 1:
            if values[i] >= last_extreme_val:
                last_extreme_idx = i
                last_extreme_val = values[i]
                pivots[-1] = i
            else:
                change_pct = (last_extreme_val - values[i]) / max(abs(last_extreme_val), 1e-10) * 100
                if change_pct >= pct_val:
                    direction = -1
                    pivots.append(i)
                    last_extreme_idx = i
                    last_extreme_val = values[i]
        elif direction == -1:
            if values[i] <= last_extreme_val:
                last_extreme_idx = i
                last_extreme_val = values[i]
                pivots[-1] = i
            else:
                change_pct = (values[i] - last_extreme_val) / max(abs(last_extreme_val), 1e-10) * 100
                if change_pct >= pct_val:
                    direction = 1
                    pivots.append(i)
                    last_extreme_idx = i
                    last_extreme_val = values[i]
    result = np.full(n, np.nan)
    if len(pivots) >= 2:
        pivot_set = sorted(set(pivots))
        for idx in range(len(pivot_set)):
            result[pivot_set[idx]] = values[pivot_set[idx]]
        for idx in range(len(pivot_set) - 1):
            start = pivot_set[idx]
            end = pivot_set[idx + 1]
            for j in range(start + 1, end):
                ratio = (j - start) / (end - start)
                result[j] = values[start] + ratio * (values[end] - values[start])
    else:
        result = values.copy()
    return pd.Series(result, index=data.index)


def _fn_dma(args: List[pd.Series], **_) -> pd.Series:
    if len(args) != 2:
        raise ValueError("DMA(x, a) requires 2 arguments")
    x = args[0]
    a = args[1]
    a_vals = a.values if isinstance(a, pd.Series) else np.full(len(x), float(a))
    x_vals = x.values.astype(float)
    result = np.full(len(x), np.nan)
    result[0] = x_vals[0]
    for i in range(1, len(x)):
        alpha = float(a_vals[i]) if i < len(a_vals) else float(a_vals[-1])
        alpha = max(0.0, min(1.0, alpha))
        result[i] = alpha * x_vals[i] + (1.0 - alpha) * result[i - 1]
    return pd.Series(result, index=x.index)


_BUILTIN_FUNCTIONS = {
    "MA": _fn_ma,
    "EMA": _fn_ema,
    "SMA": _fn_sma,
    "CROSS": _fn_cross,
    "REF": _fn_ref,
    "HHV": _fn_hhv,
    "LLV": _fn_llv,
    "STD": _fn_std,
    "ABS": _fn_abs,
    "MAX": _fn_max,
    "MIN": _fn_min,
    "IF": _fn_if,
    "STICKLINE": _fn_stickline,
    "DRAWICON": _fn_drawicon,
    "DRAWTEXT": _fn_drawtext,
    "WINNER": _fn_winner,
    "COST": _fn_cost,
    "SUM": _fn_sum,
    "COUNT": _fn_count,
    "EVERY": _fn_every,
    "EXIST": _fn_exist,
    "BETWEEN": _fn_between,
    "NOT": _fn_not,
    "ZIG": _fn_zig,
    "DMA": _fn_dma,
}

_STATEMENT_RE = re.compile(
    r"^\s*([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s*(:=|:)\s*(.+?)\s*$"
)

_DRAWING_SUFFIX_RE = re.compile(
    r",\s*(?:COLOR\w+|LINETHICK\d+|NODRAW|POINTDOT|CROSSDOT|CIRCLEDOT|VOLSTICK|COLOR[0-9A-Fa-f]+)",
    re.IGNORECASE,
)


class FormulaEngine:
    def __init__(self):
        self._functions = dict(_BUILTIN_FUNCTIONS)

    def register_function(self, name: str, fn):
        self._functions[name.upper()] = fn

    def evaluate(self, formula: str, df: pd.DataFrame) -> Dict[str, List[float]]:
        statements = self._split_statements(formula)
        env: Dict[str, pd.Series] = {}
        outputs: Dict[str, pd.Series] = {}

        for raw_stmt in statements:
            stmt = raw_stmt.strip()
            if not stmt:
                continue
            parsed = self._parse_statement(stmt)
            if parsed is None:
                continue
            var_name, is_output, expr_str = parsed
            expr_str = _DRAWING_SUFFIX_RE.sub("", expr_str)
            try:
                tokens = _tokenize(expr_str)
                parser = _Parser(tokens)
                ast = parser.parse()
                value = self._eval_node(ast, env, df)
                if isinstance(value, (int, float)):
                    value = pd.Series(float(value), index=df.index)
                elif not isinstance(value, pd.Series):
                    value = pd.Series(float(value), index=df.index)
                env[var_name] = value
                if is_output:
                    outputs[var_name] = value
            except Exception as e:
                raise RuntimeError(f"Error evaluating '{var_name}': {e}") from e

        return {k: v.tolist() for k, v in outputs.items()}

    def _split_statements(self, formula: str) -> List[str]:
        formula = re.sub(r"//.*", "", formula)
        formula = re.sub(r"\{[^}]*\}", "", formula)
        parts = formula.split(";")
        return [p.strip() for p in parts if p.strip()]

    def _parse_statement(self, stmt: str) -> Optional[Tuple[str, bool, str]]:
        m = _STATEMENT_RE.match(stmt)
        if m:
            var_name = m.group(1)
            assign_op = m.group(2)
            expr_str = m.group(3)
            is_output = assign_op == ":"
            return var_name, is_output, expr_str
        return None

    def _eval_node(self, node, env: Dict[str, pd.Series], df: pd.DataFrame) -> pd.Series:
        if isinstance(node, tuple):
            op = node[0]
            if op == "NUM":
                return pd.Series(float(node[1]), index=df.index)
            if op == "VAR":
                return _resolve_var(node[1], env, df)
            if op == "NEG":
                val = self._eval_node(node[1], env, df)
                if isinstance(val, pd.Series):
                    return -val
                return -val
            if op == "CALL":
                fname = node[1]
                raw_args = node[2]
                args = [self._eval_node(a, env, df) for a in raw_args]
                if fname in self._functions:
                    return self._functions[fname](args, df=df)
                raise NameError(f"Unknown function: {fname}")
            if op in ("+", "-", "*", "/"):
                left = self._eval_node(node[1], env, df)
                right = self._eval_node(node[2], env, df)
                if not isinstance(left, pd.Series):
                    left = pd.Series(float(left), index=df.index)
                if not isinstance(right, pd.Series):
                    right = pd.Series(float(right), index=df.index)
                if op == "+":
                    return left + right
                if op == "-":
                    return left - right
                if op == "*":
                    return left * right
                if op == "/":
                    return left / right.replace(0, np.nan)
            if op in (">", "<", ">=", "<=", "=", "<>"):
                left = self._eval_node(node[1], env, df)
                right = self._eval_node(node[2], env, df)
                if not isinstance(left, pd.Series):
                    left = pd.Series(float(left), index=df.index)
                if not isinstance(right, pd.Series):
                    right = pd.Series(float(right), index=df.index)
                if op == ">":
                    return (left > right).astype(float)
                if op == "<":
                    return (left < right).astype(float)
                if op == ">=":
                    return (left >= right).astype(float)
                if op == "<=":
                    return (left <= right).astype(float)
                if op == "=":
                    return (left == right).astype(float)
                if op == "<>":
                    return (left != right).astype(float)
            if op == "AND":
                left = self._eval_node(node[1], env, df)
                right = self._eval_node(node[2], env, df)
                if not isinstance(left, pd.Series):
                    left = pd.Series(float(left), index=df.index)
                if not isinstance(right, pd.Series):
                    right = pd.Series(float(right), index=df.index)
                return ((left != 0) & (right != 0)).astype(float)
            if op == "OR":
                left = self._eval_node(node[1], env, df)
                right = self._eval_node(node[2], env, df)
                if not isinstance(left, pd.Series):
                    left = pd.Series(float(left), index=df.index)
                if not isinstance(right, pd.Series):
                    right = pd.Series(float(right), index=df.index)
                return ((left != 0) | (right != 0)).astype(float)
        if isinstance(node, (int, float)):
            return pd.Series(float(node), index=df.index)
        raise RuntimeError(f"Cannot evaluate node: {node}")
