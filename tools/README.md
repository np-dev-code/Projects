# tools

## krl_lint.py

Checks KUKA KRL files (`.src`, `.dat`, `.sub`) for project rules a KRL
compiler does not enforce. It needs only Python 3, no KUKA software.

```bash
python tools/krl_lint.py                      # whole repository
python tools/krl_lint.py StatementFabrication # one folder
python tools/krl_lint.py . --strict           # warnings also fail
```

| Code | Severity | Rule |
|------|----------|------|
| E001 | error | Duplicate `CASE` label within one `SWITCH` |
| E002 | error | Assignment to an input signal (`$IN`) |
| E003 | error | Assignment to a `CONST` |
| E004 | error | Non-ASCII character |
| E005 | error | `GLOBAL` name declared more than once across modules |
| E006 | error | `WAIT` or motion statement reachable from the submit interpreter |
| W101 | warning | Routine without a standard header comment block |
| W102 | warning | Header `Name` does not match the routine |
| W103 | warning | Local variable declared but never used |
| W104 | warning | Two single-bit signals mapped to the same I/O bit |

KRL is case-insensitive, so names are compared in lower case.

**Submit interpreter (E006):** the main routine of every module whose name
ends in `Sps` (and `sps.sub`) is treated as running in the submit
interpreter, and every routine it calls, directly or indirectly, is checked.
Override with `--sps-entry NAME` (repeatable).

**Header block (W101/W102):** the first non-blank line after `DEF` must be a
comment bar (`;-----` or `;*****`). Routine headers use this layout:

```
;------------------------------------------------
; Name    : SetPidKp
; Purpose : Write the proportional band Pb (Kp)
; Params  : fi_pidType (IN) ENUM_PID_TYPE : PID channel
;           fi_nVal    (IN) INT           : 0..5000 = 0..500.0 %
; Returns : -
; Notes   : Autotune must not be running
;------------------------------------------------
```

The linter is a text-based checker, not a KRL compiler. Always build the
project in WorkVisual (or KUKA.OfficeLite) before deploying to a robot.

Tests: `python -m unittest discover -s tools -p "test_*.py"`

The GitHub workflow `.github/workflows/krl-lint.yml` runs the tests and the
linter (with `--strict`, so warnings also fail) on every push or pull
request that touches KRL files.
