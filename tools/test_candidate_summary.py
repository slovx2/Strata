"""Regression for request-level rates: native decode includes the first output."""
import unittest
from summarize_candidates import rate


def row(outputs, prompt_tokens, prompt_ms, decode_ms, tag='measure', io=None):
    return dict(tag=tag, emitted=outputs, wall_s=(prompt_ms+decode_ms)/1000,
                engine=dict(prompt_tokens=prompt_tokens, prompt_ms=prompt_ms, decode_ms=decode_ms),
                before={'disk':{}}, after={'disk':{}}, process_io_delta=io or {})


class CandidateSummary(unittest.TestCase):
    def test_full_output_count_and_weighted_time_exclude_warmup(self):
        rows=[row(4,10,100,100), row(8,20,400,100), row(100,100,1,1,'warmup')]
        result=rate(rows)
        self.assertEqual(result['requests'],2)
        self.assertEqual(result['decode_tps'],60)
        self.assertEqual(result['prefill_tps'],60)
        self.assertEqual(result['output_tokens'],12)

    def test_routing_denominator_includes_offloaded_assignments(self):
        r=row(4,10,100,100)
        r['engine'].update(hits=6,lookups=8,offloaded=2)
        result=rate([r])
        self.assertEqual(result['expert_hit_fraction'],.75)
        self.assertEqual(result['routed_vram_fraction'],.6)
        self.assertEqual(result['routed_cpu_fraction'],.2)
        self.assertEqual(result['routed_offload_fraction'],.2)

    def test_unavailable_io_is_not_zero(self):
        rows=[row(4,10,100,100,io={'read_bytes':0}),row(8,20,400,100)]
        self.assertIsNone(rate(rows)['process_read_bytes'])
        rows[1]['process_io_delta']={'read_bytes':0}
        self.assertEqual(rate(rows)['process_read_bytes'],0)


if __name__=='__main__':unittest.main()
