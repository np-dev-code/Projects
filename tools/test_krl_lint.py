"""Tests for krl_lint.py. Run: python -m unittest discover -s tools -p "test_*.py" """

import os
import tempfile
import textwrap
import unittest

import krl_lint

HEADER = """\
;------------------------------------------------
; Name    : {name}
; Purpose : test
;------------------------------------------------
"""


def routine(sig, name, body):
    return f"{sig}\n{HEADER.format(name=name)}{textwrap.dedent(body)}"


class LintTest(unittest.TestCase):
    def lint(self, files, sps_entries=None):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for name, text in files.items():
                p = os.path.join(d, name)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
                paths.append(p)
            findings = krl_lint.Linter(sorted(paths), sps_entries).run()
            return [(os.path.basename(f.path), f.line, f.code) for f in findings]

    def codes(self, files, sps_entries=None):
        return sorted(c for _, _, c in self.lint(files, sps_entries))

    SIGNALS = textwrap.dedent("""\
        DEFDAT Sig PUBLIC
        GLOBAL SIGNAL in_word $IN[2000] TO $IN[2015]
        GLOBAL SIGNAL out_word $OUT[2000] TO $OUT[2015]
        GLOBAL SIGNAL bit_a $IN[3000]
        GLOBAL CONST INT LIMIT=5
        ENDDAT
        """)

    def test_clean_module_has_no_findings(self):
        src = routine("DEF Mod( )", "Mod", """\
            DECL INT n
            n = in_word
            out_word = n
            END
            """)
        self.assertEqual(self.codes({"Sig.dat": self.SIGNALS, "Mod.src": src}), [])

    def test_duplicate_case(self):
        src = routine("DEF Mod(x:IN)", "Mod", """\
            DECL INT x
            SWITCH x
            CASE 1
               x = 1
            CASE 2, 1
               x = 2
            ENDSWITCH
            END
            """)
        self.assertEqual(self.codes({"Mod.src": src}), ["E001"])

    def test_nested_switch_labels_are_separate(self):
        src = routine("DEF Mod(x:IN)", "Mod", """\
            DECL INT x
            SWITCH x
            CASE 1
               SWITCH x
               CASE 1
                  x = 3
               ENDSWITCH
            CASE 2
               x = 2
            ENDSWITCH
            END
            """)
        self.assertEqual(self.codes({"Mod.src": src}), [])

    def test_assignment_to_input_signal_and_const(self):
        src = routine("DEF Mod( )", "Mod", """\
            in_word = 1
            $IN[5] = TRUE
            LIMIT = 6
            IF in_word == 1 THEN
               out_word = 1
            ENDIF
            END
            """)
        self.assertEqual(self.codes({"Sig.dat": self.SIGNALS, "Mod.src": src}),
                         ["E002", "E002", "E003"])

    def test_non_ascii(self):
        src = routine("DEF Mod( )", "Mod", "; 240 °C\nEND\n")
        self.assertEqual(self.codes({"Mod.src": src}), ["E004"])

    def test_duplicate_global_across_modules(self):
        a = routine("GLOBAL DEF Foo( )", "Foo", "END\n")
        b = routine("GLOBAL DEF foo( )", "foo", "END\n")
        dat_a = "DEFDAT A PUBLIC\nGLOBAL INT nCount = 0\nENDDAT\n"
        dat_b = "DEFDAT B PUBLIC\nGLOBAL INT NCOUNT = 1\nENDDAT\n"
        self.assertEqual(self.codes({"A.src": a, "B.src": b, "A.dat": dat_a, "B.dat": dat_b}),
                         ["E005", "E005"])

    def test_wait_reachable_from_sps(self):
        sps = routine("DEF MySps( )", "MySps", "Helper()\nEND\n")
        lib = routine("GLOBAL DEF Helper( )", "Helper", "WAIT SEC 1\nEND\n") + "\n" + \
            routine("GLOBAL DEF NotFromSps( )", "NotFromSps", "WAIT SEC 1\nPTP HOME\nEND\n")
        found = self.lint({"MySps.src": sps, "Lib.src": lib})
        self.assertEqual([c for _, _, c in found], ["E006"])

    def test_sps_entry_override(self):
        main = routine("DEF Main( )", "Main", "LIN P1\nEND\n")
        self.assertEqual(self.codes({"Main.src": main}), [])
        self.assertEqual(self.codes({"Main.src": main}, ["Main"]), ["E006"])

    def test_missing_and_mismatched_header(self):
        src = "DEF Mod( )\nEND\n\n" + routine("DEF Other( )", "Wrong", "END\n")
        self.assertEqual(self.codes({"Mod.src": src}), ["W101", "W102"])

    def test_unused_local(self):
        src = routine("DEF Mod(p:IN)", "Mod", """\
            DECL INT p
            DECL INT used, unused
            used = p
            END
            """)
        found = self.lint({"Mod.src": src})
        self.assertEqual([c for _, _, c in found], ["W103"])

    def test_duplicate_single_bit_signal(self):
        dat = "DEFDAT S PUBLIC\nGLOBAL SIGNAL a $IN[10]\nGLOBAL SIGNAL b $IN[10]\n" \
              "GLOBAL SIGNAL w $IN[10] TO $IN[25]\nENDDAT\n"
        self.assertEqual(self.codes({"S.dat": dat}), ["W104"])

    def test_semicolon_inside_string_is_not_a_comment(self):
        self.assertEqual(krl_lint.strip_comment('CWRITE(x,"a;b") ; note'), 'CWRITE(x,"a;b") ')


if __name__ == "__main__":
    unittest.main()
