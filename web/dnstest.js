'use strict';
// DNS test: a 30-second spot check of which DNS server answers fastest from this jack.
(() => {
  let D = null, busy = false, poll = 5000;
  // Each server keeps its colour, picked from its address.
  const hue = str => { let h = 2166136261; for (const c of String(str)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0) % 360; };
  const ms = v => v == null ? '—' : v < 10 ? v.toFixed(1) : Math.round(v) + '';
  const provider = r => r.local ? 'local' : r.name.replace(/\s*\d+$/, '').toLowerCase();
  const usable = r => r.med != null && (r.loss_pct ?? 0) <= 2 && !r.hijack;

  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/dnstest', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }

  function verdict(rows, running) {
    const ok = rows.filter(usable);
    if (!rows.length) return '<div class="sub">Press Go to find out.</div>';
    if (!ok.length) return `<div class="sub">${running ? 'Waiting for answers…' : 'No server answered well enough to recommend. Check the probe reaches the internet (Overview).'}</div>`;
    const best = ok[0], backup = ok.find(r => provider(r) !== provider(best));
    const local = rows.find(r => r.local);
    const pick = (r, label) => `<div class="dt-pick" style="--h:${hue(r.ip)}"><span class="k">${label}</span><div class="ip">${esc(r.ip)}</div>`
      + `<span class="nm">${esc(r.name)}</span> <span class="ms">${ms(r.med)} ms typical · ${ms(r.p90)} ms slow 10%</span></div>`;
    let note = '';
    if (local && best !== local && usable(local)) {
      const d = local.med - best.med;
      note = d > 3 ? `This network's own DNS (${esc(local.ip)}) answers in ${ms(local.med)} ms, ${ms(d)} ms slower than ${esc(best.name)}.`
        : `This network's own DNS (${esc(local.ip)}) is about as fast (${ms(local.med)} ms): keeping it is fine.`;
    } else if (local && best === local) note = `This network's own DNS is the fastest: keep it, and use ${backup ? esc(backup.name) + ' as the second server' : 'a public one as a backup'}.`;
    else if (local) note = `This network's own DNS (${esc(local.ip)}) ${local.hijack ? 'answers names that don’t exist' : 'lost or refused queries'}.`;
    const hij = rows.filter(r => r.hijack);
    if (hij.length) note += ` ${hij.map(r => esc(r.name)).join(', ')} made up an answer for a name that doesn't exist (ISPs do this to show ads); avoid ${hij.length === 1 ? 'it' : 'them'}.`;
    return pick(best, running ? 'Fastest so far' : 'Primary') + (backup ? pick(backup, 'Second (another provider)') : '')
      + (note ? `<div class="dt-note sub" style="color:var(--text)">${note}</div>` : '');
  }

  function draw() {
    const d = D, run = d.run, running = !!(run && run.running);
    poll = running ? 1000 : 5000;
    $('#dt-secs').textContent = d.secs;
    $('#dt-lamp').innerHTML = running ? '<span class="dot live"></span>testing…' : run?.error ? `<span class="dot err"></span>${esc(run.error)}`
      : run ? `<span class="dot on"></span>${esc(ago(run.done || run.t))}` : '<span class="dot"></span>not run yet';
    $('#dt-go').hidden = running; $('#dt-stop').hidden = !running;
    $('#dt-prog').hidden = !running;
    if (running) {
      const left = Math.max(0, run.secs - d.elapsed);
      $('#dt-bar').style.width = `${Math.min(100, 100 * d.elapsed / run.secs)}%`;
      $('#dt-left').textContent = left > 0 ? `${Math.ceil(left)} s left` : 'finishing…';
    }
    $('#dt-will').textContent = `${d.will_test.length} servers: ${d.will_test.map(s => s.name).join(', ')}.`;
    $('#dt-where').textContent = run ? [run.where ? `at ${run.where}` : '', `${ago(run.t)} by ${run.by}`].filter(Boolean).join(' · ') : '';
    setHTML('#dt-verdict', verdict(d.rows, running));
    const okRows = d.rows.filter(r => r.med != null), top = Math.max(1, ...okRows.map(r => r.p90 ?? r.med));
    const best = d.rows.find(usable);
    $('#dt-count').textContent = d.rows.length ? `${d.rows.reduce((a, r) => a + r.sent, 0)} queries` : '';
    setHTML('#dt-rows', d.rows.map((r, i) => {
      const notes = [r.local ? '<span class="chip">this network</span>' : '', r.hijack ? '<span class="chip bad">makes up answers</span>' : '',
        ...Object.entries(r.rcodes || {}).map(([k, n]) => `<span class="chip warn">${esc(k)} ×${n}</span>`), r.err ? `<span class="sub">${esc(r.err)}</span>` : ''].filter(Boolean).join(' ');
      const w = r.med == null ? 0 : Math.max(2, 100 * r.med / top);
      return `<tr class="${r === best ? 'best' : ''}" style="--h:${hue(r.ip)}"><td>${r.med == null ? '—' : i + 1}</td>`
        + `<td class="l sv"><b>${esc(r.name)}</b><span class="sub mono">${esc(r.ip)}</span></td>`
        + `<td class="l"><div class="bar"><span class="trk"><i style="width:${w.toFixed(1)}%"></i></span><span>${ms(r.med)} ms</span></div></td>`
        + `<td>${ms(r.p90)}</td><td>${ms(r.min)}</td><td>${ms(r.cold)}</td><td>${ms(r.ping)}</td>`
        + `<td style="${(r.loss_pct ?? 0) > 2 ? 'color:var(--crit)' : ''}">${r.loss_pct == null ? '—' : r.loss_pct + '%'}</td><td class="l">${notes}</td></tr>`;
    }).join('') || '<tr><td colspan="9" class="sub">No results yet.</td></tr>');
    setHTML('#dt-hist', d.history.map(h => {
      const b = h.rows.find(r => r.ip === h.best);
      return `<div class="dt-hist"><span class="sub">${esc(fmtWhen(h.t))}</span><span>${b ? `<b style="color:hsl(${hue(b.ip)} 75% 72%)">${esc(b.name)}</b> <span class="mono">${esc(b.ip)}</span> · ${ms(b.med)} ms` : 'no pick'}</span>`
        + `<span class="sub">${esc(h.where || '')}</span></div>`;
    }).join('') || '<div class="sub">None yet.</div>');
    if (!$('#f-dnstest')._filled) {
      $('#dts-servers').value = d.servers; $('#dts-names').value = d.names; $('#dts-secs').value = d.secs; $('#dts-dhcp').checked = d.dhcp;
      $('#f-dnstest')._filled = true;
    }
  }

  $('#dt-go').addEventListener('click', async () => { try { await api('/api/x/dnstest/go'); flash($('#dt-msg'), 'Started', true); } catch (e) { flash($('#dt-msg'), e.message); } refresh(); });
  $('#dt-stop').addEventListener('click', async () => { await api('/api/x/dnstest/stop').catch(() => {}); setTimeout(refresh, 300); });
  $('#dt-forget').addEventListener('click', async () => { if (confirm('Forget the earlier runs?')) { await api('/api/x/dnstest/forget').catch(() => {}); refresh(); } });
  $('#f-dnstest').addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('/api/x/dnstest/settings', { servers: $('#dts-servers').value, names: $('#dts-names').value, secs: +$('#dts-secs').value, dhcp: $('#dts-dhcp').checked });
      flash($('#dts-msg'), 'Saved', true); $('#f-dnstest')._filled = false; refresh();
    } catch (err) { flash($('#dts-msg'), err.message); }
  });
  PAGES.dnstest = { show: refresh };
  const tick = () => { if (view === 'dnstest' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
