'use strict';
// Switch logs: a syslog receiver for the switches.
(() => {
  const SEVN = ['emerg', 'alert', 'crit', 'err', 'warning', 'notice', 'info', 'debug'];
  const sevDot = s => s <= 3 ? 'err' : s === 4 ? 'warn' : s === 5 ? 'on' : '';
  const chart = new Chart('#lg-chart', { ago: true, minTop: 5, axis: v => parseFloat(v.toPrecision(3)) + '' });
  let D = null, busy = false;

  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const qs = new URLSearchParams({ sev: $('#lg-sev').value, host: $('#lg-host').value, q: $('#lg-q').value.trim(), merge: $('#lg-merge').checked ? 1 : 0 });
      const r = await fetch('/api/x/logs?' + qs, { cache: 'no-store' });
      if (r.ok) { D = await r.json(); draw(); }
    } finally { busy = false; }
  }

  function draw() {
    const d = D, port = d.port === 514 ? '' : ` transport udp port ${d.port}`;
    $('#lg-lamp').innerHTML = !d.on ? '<span class="dot"></span>off' : d.listening ? `<span class="dot on"></span>listening on UDP ${d.port}`
      : `<span class="dot err"></span>${esc(d.error || 'not listening')}`;
    setHTML('#lg-cfg', esc(`logging host ${d.probe_ip || '<probe IP>'}${port}\nlogging trap informational\nservice timestamps log datetime msec localtime\nlogging origin-id hostname`));
    $('#lg-last').innerHTML = d.last ? `<span class="dot on"></span>last ${esc(ago(d.last.t))}` : '<span class="dot"></span>nothing heard yet';
    setSeg('#lg-hour', d.hour, 4);
    setSeg('#lg-errs', d.errors, 3);
    $('#lg-errw').classList.toggle('alarm', d.unread > 0);
    $('#lg-errs2').textContent = !d.errors ? 'severity 0–3 · last hour' : d.unread ? `${d.unread} new · last hour` : `all read${d.read_t ? ' · ' + ago(d.read_t) : ''}`;
    $('#lg-read').disabled = !d.unread;
    const today = d.senders.filter(s => d.now - s.last < 86400);
    setSeg('#lg-nsw', today.length, 2);
    setHTML('#lg-senders', d.senders.map(s => `<tr class="${s.ignored ? 'ign' : ''}"><td class="l wrap" title="${esc(s.ip)}">${esc(s.name)}${s.ignored ? ' <span class="chip warn">ignored</span>' : ''}`
      + `${s.name !== s.ip ? `<div class="sub mono">${esc(s.ip)}</div>` : ''}${!s.known ? `<div class="sub">${esc(s.what || 'not a switch on the Switches page')}</div>` : ''}</td>`
      + `<td>${s.n}</td><td>${s.n ? `<span class="dot ${sevDot(s.worst)}"></span> ${SEVN[s.worst]}` : '—'}</td><td>${s.last ? esc(ago(s.last)) : '—'}</td>`
      + `<td><span class="keys admin-only">${s.ignored ? `<button class="key" data-unign="${esc(s.ip)}" title="Take its messages again">Listen again</button>`
        : `<button class="key" data-ign="${esc(s.ip)}" title="Drop everything new from this sender">Ignore</button>`}`
      + `${s.n ? `<button class="key" data-forget="${esc(s.ip)}" title="Delete its messages">✕</button>` : ''}</span></td></tr>`).join('')
      || '<tr><td colspan="5" class="sub">None yet. Paste the lines on the left into a switch.</td></tr>');
    $('#lg-ign2').hidden = !d.ignored.length;
    $('#lg-ign2').textContent = `Ignoring ${d.ignored.length} sender${d.ignored.length === 1 ? '' : 's'}${d.dropped_ignored ? ` · ${d.dropped_ignored} messages dropped since the probe started` : ''}.`;
    // filters: keep the picked switch and day
    const hosts = '<option value="">All switches</option>' + d.senders.map(s => `<option value="${esc(s.ip)}">${esc(s.name)}</option>`).join('');
    const hv = $('#lg-host').value; setHTML('#lg-host', hosts); $('#lg-host').value = hv;
    const days = d.days.map(x => `<option>${esc(x)}</option>`).join('') || '<option></option>';
    const dv = $('#lg-day').value; setHTML('#lg-day', days); if (dv && d.days.includes(dv)) $('#lg-day').value = dv;
    $('#lg-dl').href = '/api/x/logs/download?day=' + encodeURIComponent($('#lg-day').value || '');
    const ser = [['Errors', C.s[7], 0], ['Warnings', C.s[3], 1], ['Other', C.s[0], 2]].map(([name, color, i]) => ({ name, color, pts: d.per_min.map((m, k) => [k * 60 + 30, m[i]]) }));
    legend('#lg-lg', ser); chart.set(ser, 3600);
    setHTML('#lg-list', d.msgs.map(m => `<div class="ev src-${m.src}"><span class="k" title="${esc(new Date(m.t * 1000).toLocaleString())}">${esc(fmtWhen(m.t))}</span><span class="dot ${sevDot(m.sev)}" title="${SEVN[m.sev]}"></span>`
      + `<span class="who" title="${esc(m.ip || '')}">${esc(m.who)}</span><span>${m.code ? `<span class="code">${esc(m.code)}</span>` : ''}${esc(m.msg)}</span></div>`).join('')
      || `<div class="empty">${d.hour || d.senders.length ? 'Nothing matches.' : 'No messages yet.'}</div>`);
    if (!$('#f-logs')._filled) { $('#lg-on').checked = d.on; $('#lg-port').value = d.port; $('#lg-keep').value = d.keep_days; $('#f-logs')._filled = true; }
  }

  for (const id of ['#lg-sev', '#lg-host', '#lg-merge']) $(id).addEventListener('change', refresh);
  $('#lg-day').addEventListener('change', () => { $('#lg-dl').href = '/api/x/logs/download?day=' + encodeURIComponent($('#lg-day').value); });
  let qt; $('#lg-q').addEventListener('input', () => { clearTimeout(qt); qt = setTimeout(refresh, 300); });
  $('#lg-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#lg-cfg').textContent); flash($('#lg-copymsg'), 'Copied', true); }
    catch { getSelection().selectAllChildren($('#lg-cfg')); flash($('#lg-copymsg'), 'Selected: press Ctrl+C', true); }
  });
  $('#f-logs').addEventListener('submit', async e => {
    e.preventDefault();
    try { await api('/api/x/logs/settings', { on: $('#lg-on').checked, port: +$('#lg-port').value, keep_days: +$('#lg-keep').value }); flash($('#lg-msg'), 'Saved', true); refresh(); }
    catch (err) { flash($('#lg-msg'), err.message); }
  });
  $('#lg-clear').addEventListener('click', async () => {
    if (!confirm('Delete every switch log on the probe?')) return;
    try { await api('/api/x/logs/clear'); flash($('#lg-msg'), 'Cleared', true); refresh(); } catch (err) { flash($('#lg-msg'), err.message); }
  });
  $('#lg-senders').addEventListener('click', async e => {
    const f = e.target.closest('[data-forget]'), g = e.target.closest('[data-ign]'), u = e.target.closest('[data-unign]');
    try {
      if (f && confirm(`Delete every message from ${f.dataset.forget}? New ones still arrive (use Ignore to drop those too).`)) await api('/api/x/logs/forget', { ip: f.dataset.forget });
      if (u) await api('/api/x/logs/unignore', { ip: u.dataset.unign });
      if (g && confirm(`Ignore ${g.dataset.ign}? New messages from it are dropped (the ones already kept stay; ✕ deletes them).`)) await api('/api/x/logs/ignore', { ip: g.dataset.ign });
    } catch (err) { alert(err.message); }
    refresh();
  });
  $('#lg-read').addEventListener('click', async () => { await api('/api/x/logs/read').catch(err => alert(err.message)); refresh(); });
  PAGES.logs = { show: refresh };
  setInterval(() => { if (view === 'logs' && ME) refresh(); }, 3000);
})();
