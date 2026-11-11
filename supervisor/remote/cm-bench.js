// supervisor — micro-benchmark standard (piano multi-PC 1.2), uguale su ogni host: node cm-bench.js ROOT [SECONDS]
// CPU: un carico JavaScript fisso (JSON, ordinamento, hash FNV in puro JS, aritmetica) per SECONDS secondi, su un
// thread e su tutti i thread (worker). Non sha256 come diceva il piano: le istruzioni crittografiche di ARMv8 (e SHA-NI)
// gonfiano quel numero di 5 volte rispetto al lavoro vero (render, build, browser) — misurato il 27/09/2026.
// Disco: scrittura con fsync e lettura sequenziale di 256 MiB nella cartella di lavoro. Stampa una riga JSON di valori
// grezzi: i punteggi relativi (regia = 1,0) li calcola supervisor, solo fra misure fatte con lo stesso runtime.
"use strict";
const crypto = require("crypto"), fs = require("fs"), os = require("os"), path = require("path");
const { Worker, isMainThread, parentPort, workerData } = require("worker_threads");
const OBJ = Array.from({ length: 200 }, (_, i) => ({ id: i, name: "item-" + i, tags: ["a", "b", "c"], v: i * 1.5 }));
function unit() {  // una unita' di lavoro deterministica
  const o = JSON.parse(JSON.stringify(OBJ));
  let x = 12345; const a = new Array(2000);
  for (let i = 0; i < a.length; i++) { x = (x * 1103515245 + 12345) & 0x7fffffff; a[i] = x; }
  a.sort((p, q) => p - q);
  let h = 0x811c9dc5; const s = o.map((e) => e.name).join(",");
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  let f = 0; for (let i = 1; i < 2000; i++) f += Math.sqrt(i) * Math.sin(i);
  return (h ^ a[1000] ^ (f | 0)) & 1;
}
function spin(ms) { let n = 0, k = 0; const end = Date.now() + ms; while (Date.now() < end) { k ^= unit(); n++; } return n + (k & 0); }
if (!isMainThread) { parentPort.postMessage(spin(workerData.ms)); return; }
const root = process.argv[2] || os.tmpdir(), secs = Number(process.argv[3] || 5), ms = secs * 1000;
const threads = (os.availableParallelism ? os.availableParallelism() : os.cpus().length) || 1;
(async () => {
  const single = spin(ms) / secs;
  const counts = await Promise.all(Array.from({ length: threads }, () => new Promise((res, rej) => {
    const w = new Worker(__filename, { workerData: { ms } }); w.on("message", res); w.on("error", rej);
  })));
  const multi = counts.reduce((a, b) => a + b, 0) / secs;
  let wr = null, rd = null;
  try {
    fs.mkdirSync(root, { recursive: true });
    const f = path.join(root, ".cm-bench.tmp"), chunk = Buffer.alloc(1 << 20, 1), N = 256;
    let t = process.hrtime.bigint(); const fd = fs.openSync(f, "w");
    for (let i = 0; i < N; i++) fs.writeSync(fd, chunk); fs.fsyncSync(fd); fs.closeSync(fd);
    wr = N / (Number(process.hrtime.bigint() - t) / 1e9);
    t = process.hrtime.bigint(); const rfd = fs.openSync(f, "r"); const b = Buffer.alloc(1 << 20);
    while (fs.readSync(rfd, b, 0, b.length, null) > 0); fs.closeSync(rfd);
    rd = N / (Number(process.hrtime.bigint() - t) / 1e9); fs.unlinkSync(f);
  } catch (e) { /* disco non misurabile: null */ }
  const r2 = (x) => (x == null ? null : Math.round(x * 10) / 10);
  console.log(JSON.stringify({ runtime: "node " + process.version, seconds: secs, threads,
    cpu_single_raw: r2(single), cpu_multi_raw: r2(multi), disk_write_mb_s: r2(wr), disk_read_mb_s: r2(rd) }));
})();
