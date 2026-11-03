"""Optional offline tests for the reusable summarizer; no FPGA required."""
import copy
import json
from pathlib import Path
import unittest

from compare_pairs import summarize


class PairedTimingTests(unittest.TestCase):
    def setUp(self):
        self.example = json.loads(Path(__file__).with_name('example.json').read_text(encoding='utf-8'))

    def test_totals_not_average_of_ratios(self):
        result = summarize(self.example)
        self.assertEqual(result['pairs'], 4)
        self.assertEqual(result['speedup_ratio_of_totals'], 2.0)
        self.assertEqual(result['time_reduction_pct'], 50.0)
        self.assertEqual(len(result['per_round']), 2)

    def test_shuffled_rows_do_not_change_totals(self):
        self.example['measurements'].reverse()
        self.assertEqual(summarize(self.example)['speedup_ratio_of_totals'], 2.0)

    def test_missing_and_duplicate_pairs_fail(self):
        original = self.example['measurements']
        for rows in (original[:-1], original + [original[0]]):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                summarize(rows)

    def test_nonpositive_nonfinite_and_boolean_timings_fail(self):
        for value in (0, -1, float('nan'), float('inf'), True, '2'):
            data = copy.deepcopy(self.example)
            data['measurements'][0]['pipeline_ms'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                summarize(data)

    def test_unsuccessful_report_fails(self):
        for key in ('completed', 'pass', 'hardware_session_safe'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                summarize(dict(self.example, **{key: False}))
        with self.assertRaises(ValueError):
            summarize(dict(self.example, errors=['incomplete run']))

    def test_phase_filter_is_explicit(self):
        rows = [dict(row, phase='measured') for row in self.example['measurements']]
        rows += [dict(rows[0], phase='warmup')]
        self.assertEqual(summarize(rows, phase_field='phase')['pairs'], 4)
        with self.assertRaises(ValueError):
            summarize(rows)
        rows[-1].pop('phase')
        with self.assertRaises(ValueError):
            summarize(rows, phase_field='phase')

    def test_other_variants_and_bad_identity_fail(self):
        rows = copy.deepcopy(self.example['measurements'])
        rows[0]['variant'] = 'third'
        with self.assertRaises(ValueError):
            summarize(rows)
        rows[0]['variant'] = 'cpu'
        rows[0]['case'] = []
        with self.assertRaises(ValueError):
            summarize(rows)

    def test_custom_schema(self):
        records = [{'sample': 'a', 'implementation': 'old', 'duration': 12},
                   {'sample': 'a', 'implementation': 'new', 'duration': 8}]
        result = summarize(records, 'old', 'new', variant_field='implementation',
                           time_field='duration', pair_fields=('sample',))
        self.assertEqual(result['speedup_ratio_of_totals'], 1.5)
        self.assertNotIn('per_round', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
