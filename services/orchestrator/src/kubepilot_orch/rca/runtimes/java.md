### Runtime-specific reasoning: JVM (Java / Kotlin / Scala)

The failing workload runs on the JVM. Weigh these JVM-specific failure patterns
when they match the evidence — they sharpen the root-cause **category** and the
recommendation. Do not force a JVM cause if the signals point elsewhere.

**Heap exhaustion / OOM**
- Signals: `java.lang.OutOfMemoryError: Java heap space`, container `OOMKilled`
  (exit 137), RSS climbing to the container memory limit, long/frequent GC pauses
  before the kill.
- Distinguish **container OOMKilled** (kernel killed the process: RSS > limit,
  often off-heap/metaspace/thread-stack growth) from **JVM heap OOM** (the JVM
  threw before the container limit — heap too small or a leak). `-Xmx` set close
  to the container limit leaves no room for off-heap → kernel OOM first.
- Category: `OOMKilled`. Recommend: right-size `-Xmx` **below** the container
  limit (leave ~25% headroom for off-heap/metaspace/threads), or fix the leak;
  capture a heap dump (`-XX:+HeapDumpOnOutOfMemoryError`).

**Metaspace / off-heap growth**
- Signals: `OutOfMemoryError: Metaspace`, native memory growth with a flat heap.
  Often classloader leaks (repeated redeploys, dynamic proxies).
- Category: `OOMKilled` (native). Recommend: bound `-XX:MaxMetaspaceSize`,
  investigate classloader retention.

**GC thrash / long pauses**
- Signals: rising GC time %, p99 latency spikes correlated with GC, high
  allocation rate. Throughput collapses before any OOM.
- Category: `ResourceSaturation` / `LatencyRegression`. Recommend: tune the
  collector (G1/ZGC), raise heap, or reduce allocation churn.

**Thread starvation / deadlock**
- Signals: thread dump shows many `BLOCKED`/`WAITING` threads, a pool exhausted,
  requests timing out while CPU is idle. `java.util.concurrent` pool saturation.
- Category: `ThreadStarvation` / `DependencyFailure` (if blocked on a slow
  downstream). Recommend: capture a thread dump (`jstack`), size the pool, add
  downstream timeouts.

**What to ask for:** heap dump, thread dump (`jstack`), GC logs, and the `-Xmx`
vs container-limit ratio. A confident JVM OOM diagnosis usually has OOMKilled +
memory saturation + an `OutOfMemoryError` trace all agreeing.
