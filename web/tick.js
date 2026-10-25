'use strict';
// Tick test: game-rate UDP to a reflector (PorkChopCheck's test), and this probe's own reflector.
(() => {
  let D = null, busy = false, poll = 4000;
  const chart = new Chart('#tk-chart', { minTop: 1, axis: v => parseFloat(v.toPrecision(3)) + '' });
  const GC = { excellent: 'on', good: 'on', fair: 'warn', bad: 'err', info: '', wait: '' };
  const well = (k, v, u, g) => `<div class="well${g === 'bad' ? ' alarm' : ''}"><span class="k">${k}</span><div class="big-port" style="font-size:24px;color:${g === 'bad' ? 'var(--crit)' : g === 'fair' ? 'var(--warn)' : ''}">${esc(v)}</div><span class="un">${u}</span></div>`;

  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/tick', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }
  function draw() {
    const d = D, r = d.run, run = !!(r && r.running), rf = d.reflector;
    poll = run ? 700 : 4000;
    $('#tk-go').hidden = run; $('#tk-stop').hidden = !run;
    $('#tk-rlamp').innerHTML = rf.on ? '<span class="dot on"></span>on' : rf.error ? `<span class="dot err"></span>${esc(rf.error)}` : '<span class="dot"></span>off';
    $('#tk-rhow').innerHTML = rf.on ? `Other testers can use this probe as their far end: in PorkChopCheck, Tools › Tick test › Server <code>${esc(d.probe_ip || '?')}</code>.` : 'Turn it on to let other testers use this probe as their far end.';
    setHTML('#tk-rinfo', [['Port', `UDP ${rf.port}`], ['Packets returned', rf.count.toLocaleString()], ['Recent testers', rf.peers.map(p => p[0]).join(', ') || '—']]
      .map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join(''));
    setHTML('#tk-peers', d.perfdeck.map(p => `<button type="button" data-t="${esc(p.ip)}" title="Needs a reflector running there">${esc(p.name)}</button>`).join(''));
    if (!r) {
      $('#tk-lamp').innerHTML = '<span class="dot"></span>not run yet'; $('#tk-head').textContent = 'Point it at a reflector and press Start.';
      setHTML('#tk-wells', ''); setHTML('#tk-rate', ''); chart.set([], 30); $('#tk-detail').textContent = '';
    } else {
      const s = r.summary || {};
      $('#tk-lamp').innerHTML = run ? `<span class="dot live"></span>${r.rate}/s to ${esc(r.host)} · ${Math.round(d.now - r.started)} of ${r.secs} s`
        : `<span class="dot ${GC[r.grade] || ''}"></span>${esc(r.host)} · ${esc(fmtWhen(r.started))}`;
      $('#tk-head').textContent = r.headline || '—';
      $('#tk-head').style.color = r.grade === 'bad' ? 'var(--crit)' : r.grade === 'fair' ? 'var(--warn)' : '';
      const g = Object.fromEntries((r.ratings || []).map(x => [x.key, x.grade]));
      setHTML('#tk-wells', s.n ? [well('Loss', s.loss_pct.toFixed(2) + '%', `${s.lost} of ${s.n}`, g.loss), well('Lost in a row', String(s.burst), 'longest run', g.burst),
        well('Late', String(s.late), 'replies a tick late', g.late), well('Ping', s.p50 != null ? s.p50.toFixed(2) : '—', `ms typical · p99 ${s.p99 != null ? s.p99.toFixed(2) : '—'}`, g.ping),
        well('Jitter', s.jitter != null ? s.jitter.toFixed(2) : '—', 'ms', g.jitter)].join('') : '');
      const ser = [{ name: 'Reply time (ms)', color: C.s[0], pts: (r.points || []).map(p => [p[0], p[1]]) }];
      legend('#tk-lg', ser); chart.set(ser, r.secs);
      const c = r.counters || {}, h = r.holds || {};
      $('#tk-detail').textContent = [`${(c.sent || 0).toLocaleString()} sent, ${(c.recv || 0).toLocaleString()} back`, c.reorder ? `${c.reorder} out of order` : '', c.dup ? `${c.dup} twice` : '',
        h.n ? `reflector held packets ${h.p50 != null ? h.p50.toFixed(2) : '?'} ms typical, ${h.max != null ? h.max.toFixed(2) : '?'} ms at most (taken out of the times)` : r.hold_mode === 'none' ? 'the reflector is an old version: its own delay is counted' : '',
        r.pace_ms != null ? `this probe's sending was ${r.pace_ms} ms off schedule on average` : '', r.error ? 'error: ' + r.error : ''].filter(Boolean).join(' · ') + '. Red ticks on the chart are lost packets.';
      setHTML('#tk-rate', (r.ratings || []).map(x => `<tr><td class="l">${esc(x.label)}</td><td>${esc(x.value)}</td><td class="l"><span class="dot ${GC[x.grade]}"></span> ${esc(x.grade === 'info' ? 'shown, not graded' : x.grade)}</td><td class="l wrap sub">${esc(x.felt)}</td></tr>`).join(''));
    }
    setHTML('#tk-hist', d.history.map(h => { const s = h.summary || {}; return `<tr><td>${esc(fmtWhen(h.started))}</td><td class="l mono">${esc(h.host)}${h.port !== 27777 ? ':' + h.port : ''}</td><td>${h.rate}/s</td>`
      + `<td>${s.loss_pct != null ? s.loss_pct.toFixed(2) + '%' : '—'}</td><td>${s.burst ?? '—'}</td><td>${s.late ?? '—'}</td><td>${s.p50 != null ? s.p50.toFixed(2) + ' ms' : '—'}</td>`
      + `<td class="l"><span class="dot ${GC[h.grade] || ''}"></span> ${esc(h.headline || '')}</td></tr>`; }).join('') || '<tr><td colspan="8" class="sub">None yet.</td></tr>');
    if (!$('#f-tick')._filled) {
      const c = d.conf; $('#tk-target').value = c.target; $('#tk-secs').value = c.secs; $('#tk-strict').checked = c.strict; $('#tk-refl').checked = c.reflector;
      for (const x of document.querySelectorAll('input[name=tk-rate]')) x.checked = +x.value === c.rate;
      $('#f-tick')._filled = true;
    }
  }
  $('#tk-peers').addEventListener('click', e => { const b = e.target.closest('[data-t]'); if (b) $('#tk-target').value = b.dataset.t; });
  $('#f-tick').addEventListener('submit', async e => {
    e.preventDefault(); $('#tk-err').hidden = true;
    try { await api('/api/x/tick/start', { target: $('#tk-target').value, rate: +$('input[name=tk-rate]:checked').value, secs: +$('#tk-secs').value, strict: $('#tk-strict').checked }); refresh(); }
    catch (err) { $('#tk-err').textContent = err.message; $('#tk-err').hidden = false; }
  });
  $('#tk-stop').addEventListener('click', async () => { await api('/api/x/tick/stop').catch(() => {}); setTimeout(refresh, 1500); });
  $('#tk-refl').addEventListener('change', async e => { await api('/api/x/tick/settings', { reflector: e.target.checked }).catch(err => alert(err.message)); refresh(); });
  $('#tk-clear').addEventListener('click', async () => { if (confirm('Clear the tick test history?')) { await api('/api/x/tick/clear').catch(() => {}); refresh(); } });
  PAGES.tick = { show: refresh };
  const tick = () => { if (view === 'tick' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
