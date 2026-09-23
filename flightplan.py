"""Static extraction of a high-level-commander flight plan from a cflib script.

Walks the script's AST starting at main(), follows calls into functions defined
in the same file, and records takeoff / go_to / land / stop / sleep / print in
execution order. Nothing in the script is executed; only literal arithmetic on
known constants is evaluated.
"""

import ast
import math
import operator

HL_COMMANDS = {"takeoff", "land", "go_to", "stop"}

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
_CMPOPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_SAFE_CALLS = {
    "abs": abs, "min": min, "max": max, "round": round, "float": float,
    "int": int, "range": range, "len": len, "list": list,
}
_MATH = {n: getattr(math, n) for n in ("sin", "cos", "tan", "atan2", "sqrt", "radians", "degrees", "pi", "tau")}

MAX_OPS = 5000
MAX_LOOP = 1000


class Unknown(Exception):
    pass


def _eval(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _SAFE_CALLS:
            return _SAFE_CALLS[node.id]
        raise Unknown(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "math":
        if node.attr in _MATH:
            return _MATH[node.attr]
        raise Unknown(f"math.{node.attr}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
        return _UNOPS[type(node.op)](_eval(node.operand, env))
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare) and all(type(o) in _CMPOPS for o in node.ops):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval(e, env) for e in node.elts]
    if isinstance(node, ast.Subscript):
        return _eval(node.value, env)[_eval(node.slice, env)]
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    if isinstance(node, ast.Call) and not node.keywords:
        fn = _eval(node.func, env)
        if fn in _SAFE_CALLS.values() or fn in _MATH.values():
            return fn(*[_eval(a, env) for a in node.args])
    raise Unknown(ast.dump(node)[:60])


def _try_eval(node, env):
    try:
        return _eval(node, env)
    except Exception:
        return None


def _bind_args(fn_def, call, env):
    """Map a call's args onto a FunctionDef's parameters; unknowns are left unbound."""
    params = [a.arg for a in fn_def.args.args]
    defaults = fn_def.args.defaults
    local = {}
    for name, d in zip(params[len(params) - len(defaults):], defaults):
        v = _try_eval(d, env)
        if v is not None:
            local[name] = v
    for name, a in zip(params, call.args):
        v = _try_eval(a, env)
        if v is not None:
            local[name] = v
        else:
            local.pop(name, None)
    for kw in call.keywords:
        if kw.arg:
            v = _try_eval(kw.value, env)
            if v is not None:
                local[kw.arg] = v
    return local


class _Walker:
    def __init__(self, tree, filename):
        self.filename = filename
        self.funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.globals = {}
        for n in tree.body:
            if isinstance(n, ast.Assign):
                v = _try_eval(n.value, self.globals)
                for t in n.targets:
                    if isinstance(t, ast.Name) and v is not None:
                        self.globals[t.id] = v
        self.ops = []
        self.warnings = []
        self.t = 0.0
        self.stack = []

    def warn(self, node, msg):
        self.warnings.append(f"{self.filename}:{getattr(node, 'lineno', '?')}: {msg}")

    def emit(self, node, op, **kw):
        if len(self.ops) >= MAX_OPS:
            raise Unknown("plan too long")
        self.ops.append({"op": op, "t": round(self.t, 4), "line": node.lineno, **kw})

    def run_function(self, name, local):
        if name in self.stack:
            return
        self.stack.append(name)
        env = dict(self.globals)
        env.update(local)
        self.block(self.funcs[name].body, env)
        self.stack.pop()

    def block(self, stmts, env):
        for s in stmts:
            self.stmt(s, env)

    def stmt(self, s, env):
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
            self.call(s.value, env)
        elif isinstance(s, ast.Assign):
            if isinstance(s.value, ast.Call):
                self.call(s.value, env)
            v = _try_eval(s.value, env)
            for t in s.targets:
                if isinstance(t, ast.Name):
                    if v is None:
                        env.pop(t.id, None)
                    else:
                        env[t.id] = v
        elif isinstance(s, ast.AugAssign) and isinstance(s.target, ast.Name):
            try:
                env[s.target.id] = _BINOPS[type(s.op)](env[s.target.id], _eval(s.value, env))
            except Exception:
                env.pop(s.target.id, None)
        elif isinstance(s, ast.With):
            for item in s.items:
                if isinstance(item.context_expr, ast.Call):
                    self.call(item.context_expr, env)
            self.block(s.body, env)
        elif isinstance(s, ast.If):
            cond = _try_eval(s.test, env)
            if cond is None:
                self.warn(s, "could not evaluate if-condition; assuming True")
                cond = True
            self.block(s.body if cond else s.orelse, env)
        elif isinstance(s, ast.For):
            it = _try_eval(s.iter, env)
            if it is None:
                # e.g. `for entry in logger:` — a runtime loop, not part of the plan
                return
            for i, v in enumerate(it):
                if i >= MAX_LOOP:
                    self.warn(s, f"loop truncated at {MAX_LOOP} iterations")
                    break
                if isinstance(s.target, ast.Name):
                    env[s.target.id] = v
                self.block(s.body, env)
        elif isinstance(s, ast.Try):
            self.block(s.body, env)
            self.block(s.finalbody, env)

    def call(self, c, env):
        f = c.func
        # commander.go_to(...), cf.high_level_commander.takeoff(...), ...
        if isinstance(f, ast.Attribute) and f.attr in HL_COMMANDS:
            self.hl_command(f.attr, c, env)
        elif isinstance(f, ast.Attribute) and f.attr == "sleep" or isinstance(f, ast.Name) and f.id == "sleep":
            d = _try_eval(c.args[0], env) if c.args else None
            if d is None:
                self.warn(c, "sleep() with unknown duration ignored")
            else:
                self.emit(c, "sleep", duration=float(d))
                self.t += float(d)
        elif isinstance(f, ast.Name) and f.id == "print":
            parts = [_try_eval(a, env) for a in c.args]
            text = " ".join(str(p) for p in parts if p is not None)
            if text:
                self.emit(c, "print", text=text)
        elif isinstance(f, ast.Name) and f.id in self.funcs:
            self.run_function(f.id, _bind_args(self.funcs[f.id], c, env))
        for a in c.args:
            if isinstance(a, ast.Call):
                self.call(a, env)

    def hl_command(self, name, c, env):
        sig = {
            "takeoff": ["absolute_height_m", "duration_s"],
            "land": ["absolute_height_m", "duration_s"],
            "go_to": ["x", "y", "z", "yaw", "duration_s", "relative", "linear"],
            "stop": [],
        }[name]
        vals = {}
        for p, a in zip(sig, c.args):
            vals[p] = _try_eval(a, env)
        for kw in c.keywords:
            if kw.arg in sig:
                vals[kw.arg] = _try_eval(kw.value, env)
        src = ast.get_source_segment(self.source, c) or name
        if name == "stop":
            self.emit(c, "stop", src=src)
            return
        missing = [p for p in sig[:len(sig) if name != "go_to" else 5] if vals.get(p) is None]
        if missing:
            self.warn(c, f"{name}(): could not evaluate {', '.join(missing)}; skipped")
            return
        if name == "go_to":
            self.emit(c, "go_to", src=src, x=float(vals["x"]), y=float(vals["y"]), z=float(vals["z"]),
                      yaw=float(vals["yaw"]), duration=float(vals["duration_s"]),
                      relative=bool(vals.get("relative") or False), linear=bool(vals.get("linear") or False))
        else:
            self.emit(c, name, src=src, z=float(vals["absolute_height_m"]), duration=float(vals["duration_s"]))


def extract_plan(source, filename="flight.py"):
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return {"ops": [], "warnings": [f"{filename}:{e.lineno}: syntax error: {e.msg}"],
                "duration": 0.0, "uri": None, "error_line": e.lineno}
    w = _Walker(tree, filename)
    w.source = source
    uri = w.globals.get("URI")
    uri = uri if isinstance(uri, str) else None
    if "main" not in w.funcs:
        return {"ops": [], "warnings": [f"{filename}: no main() function found"], "duration": 0.0, "uri": uri}
    try:
        w.run_function("main", {})
    except Unknown as e:
        w.warnings.append(str(e))
    return {"ops": w.ops, "warnings": w.warnings, "duration": round(w.t, 4), "uri": uri}


if __name__ == "__main__":
    import json
    import sys
    with open(sys.argv[1]) as fh:
        print(json.dumps(extract_plan(fh.read(), sys.argv[1].split("/")[-1]), indent=2))
