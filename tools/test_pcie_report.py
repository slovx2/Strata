import copy
import json
from pathlib import Path
import unittest

from report_pcie_sweep import summarize


class ReportTests(unittest.TestCase):
    def row(self, domain, fraction, tps, tag='pcie_sweep'):
        row=copy.deepcopy(json.loads(Path(__file__).with_name('profile_counter_fixtures.json').read_text())[0])
        row.update(fixture=domain,pcie_frac=fraction,tag=tag)
        row['engine']['decode_ms']=row['engine']['generated']/tps*1000
        return row

    def test_domains_have_equal_weight_not_absolute_speed_weight(self):
        data={'rows':[self.row('fast',.55,100),self.row('fast',.2,200),
                      self.row('slow',.55,10),self.row('slow',.2,5)]}
        rows,groups,values,aggregate,best=summarize(data)
        tuned=next(x for x in aggregate if x['pcie_frac']==.2)
        self.assertAlmostEqual(tuned['relative_to_default_geomean'],1)
        self.assertEqual(tuned['minimum_domain_gain_pct'],-50)
        self.assertAlmostEqual(tuned['pooled_decode_tps'],2/(1/200+1/5))

    def test_warmups_excluded_and_repetitions_use_median(self):
        data={'rows':[self.row('a',.55,10),self.row('a',.2,20),
                      self.row('a',.2,21),self.row('a',.2,200),
                      self.row('a',.2,999,'warmup')]}
        rows,groups,values,aggregate,best=summarize(data)
        self.assertEqual(len(rows),4)
        self.assertAlmostEqual(values['a',.2],21)
        self.assertEqual(best,.2)


if __name__=='__main__':unittest.main()
