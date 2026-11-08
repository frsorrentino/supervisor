// supervisor - sessioni Claude su questo host, per la regia (piano multi-PC 4.4, fase 2.2). Solo ASCII.
//   node cm-session.js post NAME TEXT_B64 FROM_NAME FROM_ADDR   consegna nella casella nativa (named pipe o socket)
//   node cm-session.js read NAME OFFSET [last]                  testi assistant del transcript dall'offset + stato
//   node cm-session.js close NAME [force]                       ferma il processo (rifiuta busy/waiting senza force)
//   node cm-session.js tail NAME OFFSET [MAX]                   i byte grezzi del transcript dall'offset, in base64
//                                                               (la copia locale per la chat del telefono, 09/10)
// Una riga JSON sullo stdout. Il testo arriva in base64: le virgolette non sopravvivono a PowerShell -> node.
// Il protocollo della casella e' quello di post_socket (cm-talk.py): riga di auth col peerToken del .key, poi il
// messaggio; letto dal vivo il 02/10/2026: dalla sessione 0 di sshd la pipe LOCAL\ della sessione 1 si apre.
const fs = require('fs'), p = require('path'), os = require('os'), net = require('net'), crypto = require('crypto');
const cp = require('child_process');
const H = os.homedir();
// 06/11/2026: le sessioni di ogni account (.claude, .claude-pixel, ...): ognuna nella cartella sessions della sua config
const SDS = fs.readdirSync(H).filter(d => /^\.claude(-[A-Za-z0-9_]+)?$/.test(d)).map(d => p.join(H, d, 'sessions')).filter(d => fs.existsSync(d));
const out = o => { process.stdout.write(JSON.stringify(o) + '\n'); };
function alive(pid) { try { process.kill(pid, 0); return true; } catch (e) { return e.code === 'EPERM'; } }
function entry(name) {
  let best = null;
  for (const SD of SDS) for (const f of fs.readdirSync(SD)) {
    if (!f.endsWith('.json')) continue;
    let e; try { e = JSON.parse(fs.readFileSync(p.join(SD, f), 'utf8')); } catch (x) { continue; }
    if (e.name !== name) continue;
    e._sd = SD;
    e._alive = alive(e.pid);
    if (!best || (e._alive && !best._alive) || (e._alive === best._alive && (e.startedAt || 0) > (best.startedAt || 0))) best = e;
  }
  return best;
}
function transcript(e) { return p.join(p.dirname(e._sd), 'projects', String(e.cwd).replace(/[^A-Za-z0-9]/g, '-'), e.sessionId + '.jsonl'); }
function size(f) { try { return fs.statSync(f).size; } catch (x) { return 0; } }
const [verb, name, ...rest] = process.argv.slice(2);
if (!/^[A-Za-z0-9._-]+$/.test(name || '')) { out({ error: 'bad session name' }); process.exit(1); }
const e = entry(name);
if (verb === 'post') {
  if (!e || !e._alive) { out({ error: 'not running' }); process.exit(1); }
  const [b64, fromName, fromAddr] = rest;
  const text = Buffer.from(b64 || '', 'base64').toString('utf8');
  const key = fs.readdirSync(e._sd).find(f => f.startsWith(e.pid + '.') && f.endsWith('.key'));
  const token = key ? (JSON.parse(fs.readFileSync(p.join(e._sd, key), 'utf8')).peerToken || '') : '';
  const from = fromAddr || 'uds:';
  const content = `<cross-session-message from="${from}" from-name="${fromName || 'supervisor'}" from-mode="bypass">\n${text}\n</cross-session-message>`;
  const msg = { msgV: 1, msg_id: crypto.randomUUID(), type: 'user', message: { role: 'user', content }, priority: 'next', from };
  const offset = size(transcript(e));
  const s = net.connect(e.messagingSocketPath, () => {
    s.end(JSON.stringify({ type: 'auth', token }) + '\n' + JSON.stringify(msg) + '\n', () => out({ ok: true, pid: e.pid, sid: e.sessionId, offset, status: e.status }));
  });
  s.on('error', x => { out({ error: 'pipe: ' + x.code }); process.exit(1); });
  s.setTimeout(8000, () => { out({ error: 'pipe: timeout' }); process.exit(1); });
} else if (verb === 'read') {
  if (!e) { out({ status: 'gone', alive: false, texts: [], offset: 0 }); process.exit(0); }
  // 08/11/2026: al massimo READ_CAP byte per chiamata dall'offset, e more se ne resta (chi chiama riprende dall'offset
  // restituito): una lettura da un offset vecchio rileggeva tutto il resto della trascrizione (227 MB su win). Con
  // «last» solo gli ultimi READ_CAP byte, dalla prima riga intera. Una riga piu' lunga del tetto si legge intera.
  const READ_CAP = 8388608;
  const f = transcript(e), total = size(f);
  let off = rest[0] === 'end' ? total : Math.min(Number(rest[0]) || 0, total);
  let cut = false;
  if (rest[1] === 'last' && total - off > READ_CAP) { off = total - READ_CAP; cut = true; }
  const texts = [];
  if (total > off) {
    const fd = fs.openSync(f, 'r');
    let b = Buffer.alloc(Math.min(total - off, READ_CAP));
    fs.readSync(fd, b, 0, b.length, off);
    let end = b.lastIndexOf(10);
    if (end < 0 && b.length < total - off) { b = Buffer.alloc(total - off); fs.readSync(fd, b, 0, b.length, off); end = b.lastIndexOf(10); }
    fs.closeSync(fd);
    if (end >= 0) {
      const from = cut ? b.indexOf(10) + 1 : 0;
      for (const l of b.slice(from, end + 1).toString('utf8').split('\n')) {
        if (!l.trim()) continue;
        try {
          const d = JSON.parse(l);
          if (d.type === 'assistant') for (const c of (d.message || {}).content || []) if (c && c.type === 'text' && c.text) texts.push(c.text);
        } catch (x) {}
      }
      off += end + 1;
    }
  }
  out({ status: e._alive ? e.status : 'gone', alive: e._alive, texts: rest[1] === 'last' ? texts.slice(-1) : texts, offset: off, more: total > off, sid: e.sessionId });
} else if (verb === 'tail') {
  if (!e) { out({ error: 'not found' }); process.exit(1); }
  const f = transcript(e), total = size(f);
  const off = Math.min(Number(rest[0]) || 0, total), max = Math.min(Number(rest[1]) || 2097152, 4194304);
  let data = '';
  if (total > off) {
    const n = Math.min(max, total - off), fd = fs.openSync(f, 'r'), b = Buffer.alloc(n);
    fs.readSync(fd, b, 0, n, off); fs.closeSync(fd);
    data = b.toString('base64');
  }
  out({ sid: e.sessionId, cwd: e.cwd, status: e._alive ? e.status : 'gone', alive: e._alive, size: total, offset: off, data });
} else if (verb === 'close') {
  if (!e || !e._alive) { out({ ok: true, already: true }); process.exit(0); }
  if (rest[0] !== 'force' && ['busy', 'waiting'].includes(e.status)) { out({ error: 'status ' + e.status }); process.exit(1); }
  // il pid del registro deve essere ancora un claude: un pid riusato non si uccide
  const img = cp.execSync(`tasklist /FI "PID eq ${e.pid}" /FO CSV /NH`).toString();
  if (!/claude/i.test(img)) { out({ error: 'pid ' + e.pid + ' is not claude: ' + img.trim().slice(0, 80) }); process.exit(1); }
  cp.execSync(`taskkill /T /F /PID ${e.pid}`, { stdio: 'ignore' });
  out({ ok: true, pid: e.pid, status: e.status });
} else { out({ error: 'unknown verb ' + verb }); process.exit(1); }
