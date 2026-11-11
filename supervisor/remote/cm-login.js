// supervisor - il login di un account di Claude Code su questo host senza nessuno davanti (06/11/2026,
// supervisor-win-altro-account-06-11). Solo ASCII.
//   node cm-login.js serve DIR CONFIG EMAIL
// Lanciato come attivita' pianificata (un processo nato da sshd muore con la connessione): avvia
// `claude auth login --email EMAIL` con CLAUDE_CONFIG_DIR=%USERPROFILE%\CONFIG (niente per .claude), scrive quello che
// stampa in DIR\out.txt (l'indirizzo «If the browser didn't open, visit: ...», che porta a una pagina col codice da
// incollare), aspetta DIR\code.txt e lo passa allo stdin di claude, poi scrive DIR\result.json {rc, at, status}
// con `claude auth status --json`. Dopo 15 minuti senza codice si ferma (rc null, reason timeout).
const fs = require('fs'), p = require('path'), os = require('os'), cp = require('child_process');
const [verb, dir, config, email] = process.argv.slice(2);
if (verb !== 'serve' || !dir || !/^\.claude(-[A-Za-z0-9_]+)?$/.test(config || '') || !/^[^\s"'&|<>^%]+@[^\s"'&|<>^%]+$/.test(email || '')) {
  process.stdout.write(JSON.stringify({ error: 'usage: serve DIR CONFIG EMAIL' }) + '\n');
  process.exit(2);
}
const env = Object.assign({}, process.env);
delete env.CLAUDECODE;
if (config !== '.claude') env.CLAUDE_CONFIG_DIR = p.join(os.homedir(), config); else delete env.CLAUDE_CONFIG_DIR;
const out = p.join(dir, 'out.txt'), codef = p.join(dir, 'code.txt'), res = p.join(dir, 'result.json');
const done = o => { fs.writeFileSync(res + '.tmp', JSON.stringify(Object.assign({ at: Math.floor(Date.now() / 1000) }, o))); fs.renameSync(res + '.tmp', res); };
fs.writeFileSync(out, '');
const c = cp.spawn('claude', ['auth', 'login', '--email', email], { env, shell: true, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
c.stdout.on('data', b => fs.appendFileSync(out, b));
c.stderr.on('data', b => fs.appendFileSync(out, b));
let sent = false;
const poll = setInterval(() => {
  if (sent || !fs.existsSync(codef)) return;
  const code = fs.readFileSync(codef, 'utf8').trim();
  fs.unlinkSync(codef);
  if (!code) return;
  sent = true;
  c.stdin.write(code + '\n');
}, 500);
const stop = setTimeout(() => { clearInterval(poll); try { c.kill(); } catch (x) {} done({ rc: null, reason: 'timeout' }); process.exit(0); }, 15 * 60 * 1000);
c.on('exit', rc => {
  clearInterval(poll); clearTimeout(stop);
  let status = null;
  // auth status esce 1 senza login: lo stdout si legge lo stesso
  try { status = JSON.parse(String(cp.spawnSync('claude', ['auth', 'status', '--json'], { env, shell: true, windowsHide: true, timeout: 60000 }).stdout)); } catch (x) { status = null; }
  done({ rc, sent, status });
  process.exit(0);
});
