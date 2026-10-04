"""Checks for attribution errors that would mislead the timeline reader."""
import copy
import unittest
from render_window_timeline import convert


def fixture():
    stamps=[0]*(48*33+4)
    origin=1000000
    for layer in range(48):
        base=layer*33
        for i in range(25):
            stamps[base+i]=origin+(layer*40+i)*1000
        stamps[base+25]=origin+(layer*40+20.25)*1000
    stamps[48*33]=origin+48*40*1000
    stamps[48*33+1]=stamps[48*33]+1000
    return dict(request=1,window=5,T=3,emitted=2,pcie_mode=2,pcie_frac=.25,
                duration_us=2000,gpu_offset_us=-1000,alignment_error_us=2,
                spans=[['CPU','expert_pool',0,5,24]],gpu_ns=stamps,
                copy_ns=[0]*96,copy_bytes=[1048576]*48,copy_count=[1]*48,
                layer_kind=[0]*48)


class TimelineTest(unittest.TestCase):
    def test_kernel_transfer_is_not_wait(self):
        result=convert(fixture(),{'fixture':'network_4096'})
        layer=[e for e in result['events'] if e['layer']==0]
        wait=next(e for e in layer if e['name']=='waitB: delivery flag')
        fetch=next(e for e in layer if e['category']=='gpucopy')
        self.assertAlmostEqual(wait['end']-wait['start'],.00025)
        self.assertAlmostEqual(fetch['end']-fetch['start'],.00075)
        self.assertAlmostEqual(result['copyMiB'],48)
        # Expanded transfer lane repeats the same interval; GPU accounting uses gpucopy only.
        self.assertAlmostEqual(result['sums']['gpucopy'],result['sums']['copy'])

    def test_missing_split_timestamp_rejected(self):
        r=fixture();r['gpu_ns'][25]=0
        with self.assertRaises(AssertionError):convert(r,{'fixture':'network_4096'})

    def test_reverse_timestamps_rejected(self):
        r=fixture();r['gpu_ns'][21]=r['gpu_ns'][20]-1
        with self.assertRaises(AssertionError):convert(r,{'fixture':'network_4096'})

    def test_nested_submit_not_counted_as_cpu_work(self):
        r=fixture();r['spans'].append(['Copy submit','enqueue_copies',0,6,8])
        result=convert(r,{'fixture':'network_4096'})
        self.assertAlmostEqual(result['sums']['cpu'],.019)
        self.assertAlmostEqual(result['sums']['submit'],.002)

    def test_dma_copy_uses_device_clock(self):
        r=fixture();r['pcie_mode']=0
        for layer in range(48):
            r['copy_ns'][layer*2:layer*2+2]=[1000000+layer*40000+1000,1000000+layer*40000+4000]
        result=convert(r,{'fixture':'network_4096'})
        self.assertNotIn('gpucopy',result['sums'])
        self.assertAlmostEqual(result['sums']['copy'],48*.003)


if __name__=='__main__':unittest.main()
