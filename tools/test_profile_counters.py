"""Regression checks against two actual synthetic Song PC measurements."""
import copy
import json
from pathlib import Path
import unittest
from summarize_song_pc_profile import parse

ROWS=json.loads(Path(__file__).with_name('profile_counter_fixtures.json').read_text())

class CounterTests(unittest.TestCase):
    def test_all_misses_on_cpu_has_no_pcie_entries(self):
        r=parse(ROWS[0])
        self.assertEqual(r['routed_entries'],110400)
        self.assertEqual(r['pcie_routed_entries'],0)
        self.assertAlmostEqual(r['expert_hit_pct'],64.9429347826)
    def test_pcie_entries_are_not_omitted_from_denominator(self):
        r=parse(ROWS[1])
        self.assertGreater(r['native_hit_ratio_pct'],98)
        self.assertEqual(r['routed_entries'],109440)
        self.assertEqual(r['pcie_routed_entries'],29053)
        self.assertAlmostEqual(r['expert_hit_pct'],72.5758406433)
    def test_unknown_final_window_keeps_uncertainty(self):
        raw=copy.deepcopy(ROWS[1]);raw['diagnostic_lines']=[]
        r=parse(raw)
        self.assertTrue(r['expert_hit_pct_estimated'])
        lo,hi=r['expert_hit_pct_bounds']
        self.assertLessEqual(lo,72.5758406433)
        self.assertGreaterEqual(hi,72.5758406433)
    def test_stage_sums_and_decode_window_scope(self):
        for raw in ROWS:
            r=parse(raw)
            self.assertTrue(r['gpu_decode_only'])
            self.assertLess(abs(r['gpu_stage_sum_ms']-r['gpu_timeline_ms']),0.2)
        raw=copy.deepcopy(ROWS[0])
        raw['diagnostic_lines']=[x.replace('over 74 windows','over 75 windows') for x in raw['diagnostic_lines']]
        self.assertFalse(parse(raw)['gpu_decode_only'])

if __name__=='__main__':unittest.main()
