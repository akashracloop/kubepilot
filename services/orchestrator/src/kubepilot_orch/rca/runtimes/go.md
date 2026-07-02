### Runtime-specific reasoning: Go

The failing workload is a Go binary. Weigh these Go-specific patterns when they
match the evidence. Do not force a Go cause if the signals point elsewhere.

**Goroutine leak**
- Signals: goroutine count climbing without bound (pprof `goroutine` profile or
  `go_goroutines` metric), memory growing in step, eventually `OOMKilled`. Stacks
  parked on channel send/recv or `sync.WaitGroup` that never completes; leaked
  `context` without cancellation.
- Category: `GoroutineLeak` (a resource leak, not a classic heap OOM). Recommend:
  pprof the goroutine profile, ensure every spawned goroutine has a termination
  path (ctx cancellation / channel close / timeout).

**Unrecovered panic**
- Signals: `panic:` followed by `goroutine N [running]:` stack; the process exits
  non-zero and the container restarts (`CrashLoopBackOff`). A nil-map write, nil
  deref, or index out of range.
- Category: `Panic` / `ApplicationError`. Recommend: fix the panic site; add
  `recover()` only at safe boundaries (not to mask the bug).

**GC pressure / high allocation**
- Signals: rising `go_memstats_gc_cpu_fraction`, GC pauses correlated with p99
  latency, high alloc rate. Distinct from a leak — heap is reclaimed but churn is
  high.
- Category: `ResourceSaturation` / `LatencyRegression`. Recommend: reduce
  allocations (reuse buffers, `sync.Pool`), tune `GOGC`.

**Deadlock / channel stall**
- Signals: `fatal error: all goroutines are asleep - deadlock!`, or requests
  hanging while CPU is idle. Unbuffered channel with no reader, mutex held across
  a blocking call.
- Category: `Deadlock` / `DependencyFailure`. Recommend: add select/timeout on
  channel ops, audit lock ordering.

**What to ask for:** goroutine + heap pprof profiles, `go_goroutines` trend, and
the panic stack if present. A confident goroutine leak has an unbounded goroutine
count trend plus memory growth, ending in OOMKilled — this is NOT the same as a
JVM heap OOM, so keep the category specific.
