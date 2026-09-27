#!/usr/bin/env python3
"""Lint KUKA KRL source (.src/.dat/.sub) for project rules a compiler won't catch.

Usage:
    python tools/krl_lint.py [paths...] [--strict] [--sps-entry NAME ...]

Errors (exit code 1):
    E001  Duplicate CASE label within one SWITCH
    E002  Assignment to an input signal ($IN) or to $IN[] directly
    E003  Assignment to a CONST
    E004  Non-ASCII character
    E005  GLOBAL name declared more than once across modules
    E006  WAIT or motion statement reachable from the submit interpreter (SPS)

Warnings (exit code 1 only with --strict):
    W101  Routine without a standard header comment block
    W102  Header block Name does not match the routine name
    W103  Local variable declared but never used
    W104  Two single-bit signals mapped to the same I/O bit

KRL is case-insensitive, so all names are compared in lower case.
"""

import argparse
import os
import re
import sys
from collections import defaultdict

KRL_EXTENSIONS = (".src", ".dat", ".sub")

RE_DEF = re.compile(
    r"^\s*(?:GLOBAL\s+)?DEF(?:FCT\s+\w+(?:\[\])?)?\s+(\w+)\s*\(([^)]*)\)", re.I)
RE_END = re.compile(r"^\s*(?:END|ENDFCT)\s*$", re.I)
RE_DEFDAT = re.compile(r"^\s*DEFDAT\s+(\w+)", re.I)
RE_ENDDAT = re.compile(r"^\s*ENDDAT\b", re.I)
RE_SIGNAL = re.compile(
    r"^\s*(?:GLOBAL\s+)?SIGNAL\s+(\w+)\s+\$(IN|OUT)\s*\[\s*(\d+)\s*\]"
    r"(?:\s+TO\s+\$(?:IN|OUT)\s*\[\s*(\d+)\s*\])?", re.I)
RE_GLOBAL_ENUM_STRUC = re.compile(r"^\s*(?:GLOBAL\s+)?(ENUM|STRUC)\s+(\w+)", re.I)
RE_VAR_DECL = re.compile(
    r"^\s*(?:DECL\s+)?(?:GLOBAL\s+)?(?:DECL\s+)?(CONST\s+)?"
    r"(INT|REAL|BOOL|CHAR|FRAME|POS|E6POS|AXIS|E6AXIS|[A-Z_]\w*)\s+(.+)$", re.I)
RE_SWITCH = re.compile(r"^\s*SWITCH\b", re.I)
RE_ENDSWITCH = re.compile(r"^\s*ENDSWITCH\b", re.I)
RE_CASE = re.compile(r"^\s*CASE\s+(.+)$", re.I)
RE_ASSIGN = re.compile(r"^\s*(\$?\w+)\s*(\[[^\]]*\])?\s*=(?!=)")
RE_WAIT_OR_MOTION = re.compile(
    r"^\s*(WAIT|PTP|LIN|CIRC|SPTP|SLIN|SCIRC|PTP_REL|LIN_REL|BRAKE|SPLINE|ASYPTP)\b",
    re.I)
RE_CALL = re.compile(r"\b(\w+)\s*\(")
RE_WORD = re.compile(r"\$?\b\w+\b")

KEYWORDS_NOT_TYPES = {
    "if", "then", "else", "endif", "switch", "case", "default", "endswitch",
    "for", "to", "endfor", "while", "endwhile", "loop", "endloop", "repeat",
    "until", "wait", "return", "halt", "exit", "goto", "interrupt", "trigger",
    "signal", "enum", "struc", "defdat", "enddat", "def", "deffct", "end",
    "endfct", "global", "ptp", "lin", "circ", "brake", "continue", "not",
    "and", "or", "exor", "b_and", "b_or", "b_not", "b_exor", "public",
}


class Finding:
    def __init__(self, path, line, code, message):
        self.path, self.line, self.code, self.message = path, line, code, message

    @property
    def is_error(self):
        return self.code.startswith("E")

    def __str__(self):
        return f"{self.path}:{self.line}: {self.code} {self.message}"


def strip_comment(line):
    """Remove a ; comment, ignoring semicolons inside "strings"."""
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == ";" and not in_str:
            return line[:i]
    return line


def strip_strings(code):
    return re.sub(r'"[^"]*"', '""', code)


class Routine:
    def __init__(self, name, params, path, start):
        self.name = name
        self.params = [p.split(":")[0].strip().lower() for p in params.split(",") if p.strip()]
        self.path = path
        self.start = start          # line number of DEF
        self.lines = []             # (line_no, raw, code)

    @property
    def key(self):
        return self.name.lower()


class Module:
    def __init__(self, path):
        self.path = path
        self.stem = os.path.splitext(os.path.basename(path))[0]
        self.raw = []
        self.routines = []
        self.is_dat = path.lower().endswith(".dat")


def parse_file(path):
    mod = Module(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        mod.raw = f.read().split("\n")
    current = None
    for no, raw in enumerate(mod.raw, 1):
        if raw.lstrip().startswith("&"):
            continue
        code = strip_comment(raw)
        m = RE_DEF.match(code)
        if m and not current:
            current = Routine(m.group(1), m.group(2), path, no)
            mod.routines.append(current)
            continue
        if current:
            if RE_END.match(code):
                current = None
            else:
                current.lines.append((no, raw, code))
    return mod


def collect_files(paths):
    files = []
    for p in paths:
        if os.path.isfile(p):
            files.append(p)
            continue
        for root, dirs, names in os.walk(p):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for n in sorted(names):
                if n.lower().endswith(KRL_EXTENSIONS):
                    files.append(os.path.join(root, n))
    return sorted({os.path.normpath(f) for f in files})


def declared_names(decl_rest):
    """Names declared by the text after the type: 'a, b[4], c = 1' -> [a, b, c]."""
    rest = decl_rest.split("=")[0]
    rest = re.sub(r"\[[^\]]*\]", "", rest)
    return [n.strip() for n in rest.split(",") if re.fullmatch(r"\s*\w+\s*", n)]


class Linter:
    def __init__(self, files, sps_entries=None):
        self.modules = [parse_file(f) for f in files]
        self.findings = []
        self.sps_entries = [s.lower() for s in (sps_entries or [])]
        self.signals = {}           # name -> (path, line, IN/OUT, start, end)
        self.consts = {}            # name -> (path, line)
        self.globals = defaultdict(list)  # name -> [(path, line, kind)]
        self.routines = {}          # key -> Routine (global and local)

    def add(self, path, line, code, message):
        self.findings.append(Finding(path, line, code, message))

    # ---------------------------------------------------------------- collect
    def collect_declarations(self):
        for mod in self.modules:
            for r in mod.routines:
                self.routines.setdefault(r.key, r)
                first = mod.raw[r.start - 1]
                if re.match(r"^\s*GLOBAL\b", first, re.I):
                    self.globals[r.key].append((mod.path, r.start, "routine"))
            if not mod.is_dat:
                continue
            in_dat = False
            for no, raw in enumerate(mod.raw, 1):
                code = strip_comment(raw)
                if RE_DEFDAT.match(code):
                    in_dat = True
                    continue
                if RE_ENDDAT.match(code):
                    in_dat = False
                    continue
                if not in_dat or not code.strip():
                    continue
                is_global = re.search(r"\bGLOBAL\b", code, re.I) is not None
                m = RE_SIGNAL.match(code)
                if m:
                    name = m.group(1).lower()
                    start = int(m.group(3))
                    end = int(m.group(4)) if m.group(4) else start
                    self.signals[name] = (mod.path, no, m.group(2).upper(), start, end)
                    if is_global:
                        self.globals[name].append((mod.path, no, "signal"))
                    continue
                m = RE_GLOBAL_ENUM_STRUC.match(code)
                if m:
                    if is_global:
                        self.globals[m.group(2).lower()].append((mod.path, no, m.group(1).lower()))
                    continue
                m = RE_VAR_DECL.match(code)
                if m and m.group(2).lower() not in KEYWORDS_NOT_TYPES:
                    for name in declared_names(m.group(3)):
                        key = name.lower()
                        if m.group(1):
                            self.consts[key] = (mod.path, no)
                        if is_global:
                            self.globals[key].append((mod.path, no, "variable"))

    # ------------------------------------------------------------------ rules
    def check_ascii(self):
        for mod in self.modules:
            for no, raw in enumerate(mod.raw, 1):
                for ch in raw:
                    if ord(ch) > 127:
                        self.add(mod.path, no, "E004", f"non-ASCII character {ch!r}")
                        break

    def check_duplicate_globals(self):
        for name, decls in sorted(self.globals.items()):
            if len(decls) > 1:
                where = ", ".join(f"{os.path.basename(p)}:{l}" for p, l, _ in decls)
                for p, l, kind in decls[1:]:
                    self.add(p, l, "E005", f"GLOBAL {kind} '{name}' declared more than once ({where})")

    def check_duplicate_bits(self):
        by_bit = defaultdict(list)
        for name, (path, line, io, start, end) in self.signals.items():
            if start == end:
                by_bit[(io, start)].append((name, path, line))
        for (io, bit), sigs in sorted(by_bit.items()):
            if len(sigs) > 1:
                names = ", ".join(s[0] for s in sigs)
                for name, path, line in sigs[1:]:
                    self.add(path, line, "W104", f"${io}[{bit}] mapped by more than one signal ({names})")

    def check_routines(self):
        for mod in self.modules:
            for r in mod.routines:
                self.check_switch_cases(r)
                self.check_assignments(r)
                self.check_header(mod, r)
                self.check_unused_locals(r)

    def check_switch_cases(self, r):
        stack = []
        for no, raw, code in r.lines:
            if RE_SWITCH.match(code):
                stack.append({})
            elif RE_ENDSWITCH.match(code):
                if stack:
                    stack.pop()
            elif stack:
                m = RE_CASE.match(code)
                if m:
                    for label in m.group(1).split(","):
                        label = label.strip().lower()
                        if not label:
                            continue
                        if label in stack[-1]:
                            self.add(r.path, no, "E001",
                                     f"duplicate CASE {label} in SWITCH (first at line {stack[-1][label]})")
                        else:
                            stack[-1][label] = no

    def check_assignments(self, r):
        local_names = set(r.params) | self.local_decls(r)
        for no, raw, code in r.lines:
            m = RE_ASSIGN.match(code)
            if not m:
                continue
            target = m.group(1).lower()
            if target == "$in":
                self.add(r.path, no, "E002", "assignment to $IN[] (inputs are read-only)")
                continue
            if target in local_names:
                continue
            sig = self.signals.get(target)
            if sig and sig[2] == "IN":
                self.add(r.path, no, "E002", f"assignment to input signal '{m.group(1)}' ($IN)")
            elif target in self.consts:
                self.add(r.path, no, "E003", f"assignment to CONST '{m.group(1)}'")

    def local_decls(self, r):
        names = set()
        for no, raw, code in r.lines:
            m = RE_VAR_DECL.match(code)
            if m and m.group(2).lower() not in KEYWORDS_NOT_TYPES and not RE_ASSIGN.match(code):
                for n in declared_names(m.group(3)):
                    names.add(n.lower())
        return names

    def check_header(self, mod, r):
        first = None
        for no, raw, code in r.lines:
            if raw.strip():
                first = (no, raw.strip())
                break
        if not first or not re.match(r"^;\s*(-{5,}|\*{5,})", first[1]):
            self.add(r.path, r.start, "W101", f"routine '{r.name}' has no header comment block")
            return
        for no, raw, code in r.lines:
            m = re.match(r"^\s*;\s*Name\s*:\s*(\w+)", raw, re.I)
            if m:
                if m.group(1).lower() != r.key:
                    self.add(r.path, no, "W102",
                             f"header Name '{m.group(1)}' does not match routine '{r.name}'")
                return
            if code.strip():
                return

    def check_unused_locals(self, r):
        decl_line = {}
        for no, raw, code in r.lines:
            m = RE_VAR_DECL.match(code)
            if m and m.group(2).lower() not in KEYWORDS_NOT_TYPES and not RE_ASSIGN.match(code):
                for n in declared_names(m.group(3)):
                    decl_line.setdefault(n.lower(), no)
        counts = defaultdict(int)
        for no, raw, code in r.lines:
            for w in RE_WORD.findall(strip_strings(code)):
                counts[w.lower()] += 1
        for name, no in decl_line.items():
            if name in r.params:
                continue
            if counts[name] <= 1:
                self.add(r.path, no, "W103", f"local variable '{name}' is declared but never used")

    def check_sps_reachability(self):
        entries = set(self.sps_entries)
        if not entries:
            for mod in self.modules:
                if mod.stem.lower() == "sps" or mod.stem.lower().endswith("sps"):
                    if mod.routines:
                        entries.add(mod.routines[0].key)
        visited, chain = set(), {}
        todo = [e for e in entries if e in self.routines]
        for e in todo:
            chain[e] = [e]
        while todo:
            key = todo.pop()
            if key in visited:
                continue
            visited.add(key)
            r = self.routines[key]
            for no, raw, code in r.lines:
                m = RE_WAIT_OR_MOTION.match(code)
                if m:
                    path = " -> ".join(self.routines[k].name for k in chain[key])
                    self.add(r.path, no, "E006",
                             f"{m.group(1).upper()} reachable from the submit interpreter ({path})")
                for call in RE_CALL.findall(strip_strings(code)):
                    c = call.lower()
                    if c in self.routines and c not in visited:
                        chain.setdefault(c, chain[key] + [c])
                        todo.append(c)

    def run(self):
        self.collect_declarations()
        self.check_ascii()
        self.check_duplicate_globals()
        self.check_duplicate_bits()
        self.check_routines()
        self.check_sps_reachability()
        self.findings.sort(key=lambda f: (f.path, f.line, f.code))
        return self.findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", default=["."], help="files or directories to lint")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--sps-entry", action="append", default=[],
                    help="routine run by the submit interpreter (default: main routine of *Sps modules)")
    args = ap.parse_args(argv)

    files = collect_files(args.paths)
    findings = Linter(files, args.sps_entry).run()
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for f in findings:
        print(f)
        if annotate:
            level = "error" if f.is_error or args.strict else "warning"
            print(f"::{level} file={f.path},line={f.line},title={f.code}::{f.message}")

    errors = sum(1 for f in findings if f.is_error)
    warnings = len(findings) - errors
    print(f"{len(files)} file(s) checked: {errors} error(s), {warnings} warning(s)")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
