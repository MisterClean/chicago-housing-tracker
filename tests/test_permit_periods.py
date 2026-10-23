"""Only the redistricting split and cross-period deduplication are in scope."""
import unittest

from scripts.import_permits import aggregate, conversion_tables, in_period


class PermitPeriodsTest(unittest.TestCase):
    def test_cutoff_day_belongs_only_to_new_boundaries(self) -> None:
        for cutoff in ("2015-05-18", "2023-05-15"):
            self.assertFalse(in_period(cutoff, "2010-01-01", cutoff))
            self.assertTrue(in_period(cutoff, cutoff, None))
        records = [
            ("2015-05-17", 10, 2, 5, "OLD PROJECT", "1"),
            ("2015-05-18", 10, 3, 7, "NEW PROJECT", "2"),
        ]
        before = aggregate([r for r in records if in_period(r[0], "2010-01-01", "2015-05-18")], 2)
        after = aggregate([r for r in records if in_period(r[0], "2015-05-18", None)], 2)
        self.assertEqual(before["units"][2]["2015"], 5)
        self.assertEqual(after["units"][3]["2015"], 7)
        self.assertNotIn(3, before["units"])
        self.assertNotIn(2, after["units"])

    def test_staged_conversion_is_deduplicated_before_period_split(self) -> None:
        records = [
            ("2023-05-14", 9, 2, 2, 1, "ONE ADDRESS"),
            ("2023-05-15", 9, 3, 2, 1, "ONE ADDRESS"),
            ("2023-05-15", 9, 3, 0, 12, "ANOTHER ADDRESS"),
            ("2023-05-15", 9, None, 0, 100, "NO STATED WARD"),
        ]
        sfh, mfh, count = conversion_tables(records, 2, start="2010-01-01", end="2023-05-15")
        self.assertEqual(count, 1)
        self.assertEqual(sfh[2]["2023"], 1)
        self.assertEqual(mfh[2]["2023"], -2)
        sfh, mfh, count = conversion_tables(records, 2, start="2023-05-15")
        self.assertEqual(count, 1)
        self.assertNotIn(2, sfh)
        self.assertEqual(mfh[3]["2023"], 12)


if __name__ == "__main__":
    unittest.main()
