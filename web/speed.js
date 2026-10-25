'use strict';
// Speed test page: the internet test (Cloudflare). The LAN test (iperf3) is drawn by render() as before.
(() => {
  const chart = new Chart('#in-chart', { minTop: 1e7, axis: axisBps });
  let D = null, busy = false, poll = 4000;
  const mbps = b => b == null ? '----' : String(Math.round(b / 1e6));
  const msv = v => v == null ? '---' : v >= 100 ? String(Math.round(v)) : v.toFixed(1);
  const PH = { meta: 'finding Cloudflare', latency: 'measuring latency', down: 'downloading', up: 'uploading' };

  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const r = await fetch('/api/x/speed', { cache: 'no-store' });
      if (r.ok) { D = await r.json(); draw(); }
    } finally { busy = false; }
  }

  function draw() {
    const run = D.run, res = run.running ? null : (run.result || D.history[0]);
    poll = run.running ? 700 : 4000;
    $('#in-lamp').innerHTML = run.running ? `<span class="dot live"></span>${PH[run.phase] || 'running'}…`
      : res ? (res.ok ? `<span class="dot on"></span>${esc(fmtWhen(res.t))}` : `<span class="dot err"></span>${esc(res.error || 'failed')}`) : '<span class="dot"></span>not run yet';
    const live = run.live || {};
    const dn = run.running ? (live.phase === 'down' ? live.bps : null) : res && res.down, up = run.running ? (live.phase === 'up' ? live.bps : null) : res && res.up;
    setSeg('#in-down', mbps(dn), 4); setSeg('#in-up', mbps(up), 4);
    $('#in-wd').classList.toggle('off', dn == null); $('#in-wu').classList.toggle('off', up == null);
    setSeg('#in-idle', msv(res && res.idle_ms), 3); setSeg('#in-ld', msv(res && res.down_ms), 3); setSeg('#in-lu', msv(res && res.up_ms), 3);
    $('#in-jit').textContent = res && res.jitter_ms != null ? `ms · jitter ${res.jitter_ms} ms` : 'ms';
    const bloat = (ms) => res && res.idle_ms != null && ms != null && ms - res.idle_ms > 30;
    $('#in-wld').classList.toggle('alarm', !!bloat(res && res.down_ms)); $('#in-wlu').classList.toggle('alarm', !!bloat(res && res.up_ms));
    $('#in-isp').textContent = res && res.ok ? `${res.asn ? 'AS' + res.asn : 'Your provider'} · public IP ${res.ip || '?'} · Cloudflare ${res.colo || '?'}${res.city ? ', ' + res.city : ''}` +
      (bloat(res.down_ms) || bloat(res.up_ms) ? ' · latency rises under load (bufferbloat)' : '') : '';
    const pts = (run.running ? run.points : run.points || []);
    const ser = [['Download', C.s[0], 'down'], ['Upload', C.s[1], 'up']].map(([name, color, ph]) => ({ name, color, pts: pts.filter(p => p[0] === ph).map(p => [p[1], p[2]]) }));
    legend('#in-lg', ser); chart.set(ser, D.secs);
    $('#in-go').disabled = run.running;
    $('#in-go').textContent = run.running ? 'Testing…' : 'Test the internet';
    setHTML('#in-hist', D.history.map(h => `<tr><td>${esc(fmtWhen(h.t))}</td><td class="l">${esc(h.ok ? (h.asn ? 'AS' + h.asn : '—') + (h.colo ? ' · ' + h.colo : '') : h.error || 'failed')}</td>`
      + `<td>${h.down ? fmtBps(h.down) : '—'}</td><td>${h.up ? fmtBps(h.up) : '—'}</td><td>${h.idle_ms != null ? h.idle_ms + ' ms' : '—'}</td>`
      + `<td>${h.down_ms != null ? Math.max(h.down_ms, h.up_ms || 0) + ' ms' : '—'}</td><td class="l">${esc(h.port ? ifShort(h.port) + (h.switch ? ' · ' + h.switch.split('.')[0] : '') : '—')}</td></tr>`).join('')
      || '<tr><td colspan="7" class="sub">No tests yet.</td></tr>');
    if (!$('#f-inet')._filled) { $('#in-secs').value = D.secs; $('#in-streams').value = D.streams; $('#f-inet')._filled = true; }
  }

  $('#f-inet').addEventListener('submit', async e => {
    e.preventDefault();
    $('#in-err').hidden = true;
    try { await api('/api/x/speed/start', { secs: +$('#in-secs').value, streams: +$('#in-streams').value }); refresh(); }
    catch (err) { $('#in-err').textContent = err.message; $('#in-err').hidden = false; }
  });
  $('#in-clear').addEventListener('click', async () => { if (confirm('Clear the internet test history?')) { await api('/api/x/speed/clear').catch(() => {}); refresh(); } });
  PAGES.speed = { show: refresh };
  const tick = () => { if (view === 'speed' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
