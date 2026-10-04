#pragma once
// Opt-in, bounded numeric-only trace. No token IDs, expert IDs, activations or text.
// Single-engine/single-GPU diagnostic mode, not a production performance benchmark.
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace strata::timeline {
using Clock = std::chrono::steady_clock;
using Time = Clock::time_point;
inline double us(Time t) { return std::chrono::duration<double, std::micro>(t.time_since_epoch()).count(); }
struct Span { const char* lane; const char* name; int layer; double begin, end; };
struct State {
    bool active = false;
    int request = 0, window = -1, T = 0, emitted = 0, pcie_mode = 0;
    double origin = 0, end = 0, fraction = 0, gpu_offset = 0, alignment_error = 0;
    unsigned long long* dma_dev = nullptr;
    std::vector<unsigned long long> gpu, dma;
    std::vector<int> kinds;
    std::vector<Span> spans;
    unsigned long long bytes[48] = {};
    int copies[48] = {};
};
inline State state;
inline const char* path() { static const char* p = std::getenv("STRATA_WINDOW_TIMELINE_PATH"); return p; }
inline void request() { if (path()) { ++state.request; state.window = -1; } }
inline void begin(int T, double fraction, Time start) {
    if (!path()) return;
    ++state.window;
    state.active = state.request <= 16 && (state.window == 5 || state.window == 20 || state.window == 40);
    if (!state.active) return;
    state.T = T; state.fraction = fraction; state.origin = us(start);
    state.spans.clear(); state.spans.reserve(400); state.gpu.clear(); state.dma.clear(); state.kinds.clear();
    state.gpu_offset = 0; state.alignment_error = 0;
    for (int i = 0; i < 48; ++i) { state.bytes[i] = 0; state.copies[i] = 0; }
}
inline void span(const char* lane, const char* name, int layer, Time a, Time b) {
    if (state.active) state.spans.push_back({lane, name, layer, us(a)-state.origin, us(b)-state.origin});
}
inline void finish(int emitted) {
    if (!state.active) return;
    state.end = us(Clock::now()) - state.origin; state.emitted = emitted;
    std::FILE* f = std::fopen(path(), "a");
    if (!f) { std::fprintf(stderr,"strata timeline: cannot open numeric trace\n"); state.active=false; return; }
    std::fprintf(f,"{\"request\":%d,\"window\":%d,\"T\":%d,\"emitted\":%d,\"pcie_mode\":%d,\"pcie_frac\":%.6f,\"duration_us\":%.3f,\"gpu_offset_us\":%.3f,\"alignment_error_us\":%.3f,\"spans\":[",
        state.request,state.window,state.T,emitted,state.pcie_mode,state.fraction,state.end,state.gpu_offset-state.origin,state.alignment_error);
    bool comma=false;
    for (const auto& s : state.spans) {
        std::fprintf(f,"%s[\"%s\",\"%s\",%d,%.3f,%.3f]",comma?",":"",s.lane,s.name,s.layer,s.begin,s.end); comma=true;
    }
    auto ints=[&](const char* name,const auto& v) {
        std::fprintf(f,"],\"%s\":[",name); bool c=false;
        for (auto x:v) { std::fprintf(f,"%s%llu",c?",":"",(unsigned long long)x); c=true; }
    };
    ints("gpu_ns",state.gpu); ints("copy_ns",state.dma); ints("layer_kind",state.kinds);
    ints("copy_bytes",state.bytes); ints("copy_count",state.copies);
    std::fprintf(f,"]}\n"); std::fclose(f); state.active=false;
}
} // namespace strata::timeline
