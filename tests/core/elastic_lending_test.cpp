// KV and image loans share addresses but must never reclaim each other's chunks.
#include "strata/core/expert_cache.hpp"
#include "strata/core/vmm.hpp"
#include <cuda_runtime.h>
#include <algorithm>
#include <cstdio>
#include <string>
#include <vector>
using namespace strata::core;
#define REQUIRE(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); return 1; } } while(0)
int main() {
    if (cudaFree(nullptr) != cudaSuccess || !vmm_available()) return 77;
    const uint64_t g=vmm_granularity();
    ExpertCache::set_vmm(true);
    ExpertCache c;
    c.set_lendable(8*g);
    std::string err;
    REQUIRE(c.open(64,1,64,(int64_t)g,err));
    REQUIRE(c.lendable() && c.vmm_range());
    auto& r=*c.vmm_range();
    auto* base=r.base();
    REQUIRE(r.chunks()==64);
    std::vector<uint8_t> check(g);
    REQUIRE(cudaMemset(base+3*g,31,g)==cudaSuccess);
    REQUIRE(cudaMemset(base+5*g,73,g)==cudaSuccess);
    REQUIRE(cudaDeviceSynchronize()==cudaSuccess);
    VmmRange kv;
    REQUIRE(kv.reserve(2*g));
    auto h=r.unmap(5);
    REQUIRE(h!=0);
    REQUIRE(kv.map_range(0,1,[&]{auto v=h;h=0;return v;}));
    for (int repeat=0;repeat<3;++repeat) {
        REQUIRE(c.tail_first_slot(8*g)==56);
        REQUIRE(c.release_tail(8*g,err)==8*g);
        REQUIRE(c.released_bytes()==8*g && !r.mapped(63));
        REQUIRE(c.release_tail(8*g,err)==8*g); // idempotent image loan
        REQUIRE(!r.mapped(5));
        REQUIRE(c.remap_tail(err));
        REQUIRE(r.base()==base && r.mapped(63) && !r.mapped(5));
        REQUIRE(c.released_bytes()==0);
        REQUIRE(cudaMemcpy(check.data(),base+3*g,g,cudaMemcpyDeviceToHost)==cudaSuccess);
        REQUIRE(std::all_of(check.begin(),check.end(),[](uint8_t v){return v==31;}));
        REQUIRE(cudaMemcpy(check.data(),kv.base(),g,cudaMemcpyDeviceToHost)==cudaSuccess);
        REQUIRE(std::all_of(check.begin(),check.end(),[](uint8_t v){return v==73;}));
    }
    // A KV-owned hole in the proposed image region rejects before any release.
    auto tail=r.unmap(62);
    REQUIRE(tail!=0);
    REQUIRE(c.release_tail(8*g,err)==0);
    REQUIRE(!err.empty() && r.mapped(63) && !r.mapped(62));
    REQUIRE(c.released_bytes()==0 && c.remap_tail(err) && !r.mapped(62));
    REQUIRE(r.map_range(62,63,[&]{auto v=tail;tail=0;return v;}));
    REQUIRE(c.tail_first_slot(0)==-1 && c.tail_first_slot(UINT64_MAX)==-1);
    h=kv.unmap(0);
    REQUIRE(h && r.map_range(5,6,[&]{auto v=h;h=0;return v;}));
    REQUIRE(cudaMemcpy(check.data(),base+5*g,g,cudaMemcpyDeviceToHost)==cudaSuccess);
    REQUIRE(std::all_of(check.begin(),check.end(),[](uint8_t v){return v==73;}));
    REQUIRE(c.release_tail(8*g,err)==8*g);
    c.close(); // including an outstanding image loan
    REQUIRE(c.open(64,1,64,(int64_t)g,err));
    REQUIRE(c.released_bytes()==0 && c.vmm_range()->mapped_count()==64);
    c.close();
    ExpertCache::set_vmm(false);
    REQUIRE(cudaGetLastError()==cudaSuccess);
    std::puts("elastic_lending_test: ownership, bytes, refusal and reopen passed");
}
