"""Safe evaluator for user-defined custom filters, e.g.  cagr_3y / std_dev   or   (cagr_5y - expense_ratio) * 2.
Never uses eval/exec: the expression is parsed to an AST and walked against a strict whitelist."""
import ast
import math
import operator as op

BIN = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Mod: op.mod, ast.Pow: op.pow}
UN = {ast.USub: op.neg, ast.UAdd: op.pos}
FUNCS = {"abs": abs, "min": min, "max": max, "sqrt": math.sqrt, "log": math.log}
MAX_LEN, MAX_NODES = 200, 60


class ExprError(ValueError):
    pass


def parse(expr, allowed):
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError("Expression is empty")
    if len(expr) > MAX_LEN:
        raise ExprError(f"Expression longer than {MAX_LEN} characters")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"Syntax error: {e.msg}")
    n = 0
    for node in ast.walk(tree):
        n += 1
        if n > MAX_NODES:
            raise ExprError("Expression too complex")
        if isinstance(node, (ast.Expression, ast.Load, ast.operator, ast.unaryop)):
            if isinstance(node, ast.operator) and type(node) not in BIN:
                raise ExprError("Operator not allowed")
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in BIN:
            continue
        if isinstance(node, ast.UnaryOp) and type(node.op) in UN:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            continue
        if isinstance(node, ast.Name):
            if node.id not in allowed and node.id not in FUNCS:
                raise ExprError(f"Unknown metric '{node.id}'")
            continue
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in FUNCS) or node.keywords:
                raise ExprError("Only abs, min, max, sqrt, log may be called")
            continue
        raise ExprError(f"'{type(node).__name__}' is not allowed")
    return tree.body


def evaluate(node, row):
    """Returns float or None (None if any referenced metric is missing or the maths is undefined)."""
    try:
        v = _ev(node, row)
    except (ZeroDivisionError, ValueError, OverflowError, TypeError):
        return None
    return None if v is None or isinstance(v, complex) or math.isnan(v) or math.isinf(v) else float(v)


def _ev(n, row):
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, ast.Name):
        v = row.get(n.id)
        if v is None:
            raise TypeError("missing")
        return v
    if isinstance(n, ast.UnaryOp):
        return UN[type(n.op)](_ev(n.operand, row))
    if isinstance(n, ast.BinOp):
        a, b = _ev(n.left, row), _ev(n.right, row)
        if isinstance(n.op, ast.Pow) and (abs(b) > 10 or abs(a) > 1e6):
            raise OverflowError
        return BIN[type(n.op)](a, b)
    if isinstance(n, ast.Call):
        args = [_ev(a, row) for a in n.args]
        if not args:
            raise TypeError
        return FUNCS[n.func.id](*args)
    raise TypeError
