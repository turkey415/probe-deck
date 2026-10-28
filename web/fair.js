'use strict';
// CS2 fairness report: compare two teams' seats and flag the one that stands out.
(() => {
  let D = null, busy = false, poll = 5000, picked = null, filled = false;
  const f2 = v => v == null ? '—' : v < 10 ? v.toFixed(2) : v.toFixed(1);
  const mins = s => s % 60 ? `${s} seconds` : s === 60 ? '1 minute' : `${s / 60} minutes`;

  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/fair', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }

  // what each team box means, as you type
  const timers = {};
  function checkTeam(i) {
    clearTimeout(timers[i]);
    timers[i] = setTimeout(async () => {
      const text = $(`#fr-team${i}`).value.trim(), out = $(`#fr-who${i}`);
      if (!text) { out.textContent = ''; return; }
      try {
        const r = await api('/api/x/fair/check', { text });
        out.style.color = ''; out.textContent = `${r.members.length} PC${r.members.length === 1 ? '' : 's'}: ${r.members.map(m => m.label === m.ip ? m.ip : `${m.label}`).join(', ')}`;
      } catch (e) { out.style.color = 'var(--crit)'; out.textContent = e.message; }
    }, 400);
  }

  function draw() {
    const d = D;
    poll = d.running ? 1000 : 5000;
    if (!filled) {
      const g = '<option value="">+ Wake group</option>' + d.groups.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
      setHTML('#fr-g1', g); setHTML('#fr-g2', g);
      setHTML('#fr-server', '<option value="">none</option>' + d.servers.map(s => `<option value="${esc(s.key)}">${esc(s.name)} (${esc(s.key)})</option>`).join(''));
      if (d.servers.length) $('#fr-server').value = d.servers[0].key;
      $('#fr-secs').value = d.secs; filled = true;
    }
    $('#fr-secs-t').textContent = mins(+$('#fr-secs').value || d.secs);
    const run = d.run;
    $('#fr-lamp').innerHTML = d.running ? '<span class="dot live"></span>measuring…' : d.reports.length ? `<span class="dot on"></span>${d.reports.length} saved` : '<span class="dot"></span>none yet';
    $('#fr-go').hidden = d.running; $('#fr-stop').hidden = !d.running; $('#fr-prog').hidden = !d.running;
    if (d.running) {
      $('#fr-bar').style.width = `${Math.min(100, 100 * d.elapsed / run.secs)}%`;
      const left = Math.max(0, run.secs - d.elapsed);
      $('#fr-left').textContent = left > 0 ? `${Math.ceil(left)} s left` : 'finishing…';
    }
    const rep = d.running ? d.live : d.reports.find(r => r.t === picked) || d.reports[0];
    if (!d.running && rep) picked = rep.t;
    setHTML('#fr-reps', d.reports.map(r => {
      const v = r.verdict.level;
      return `<div class="fr-rep${!d.running && r.t === picked ? ' sel' : ''}" data-rep="${r.t}"><span class="dot ${v === 'ok' ? 'on' : v === 'warn' ? 'warn' : 'err'}"></span>`
        + `<span><b>${esc(r.teams.map(t => t.name).join(' vs '))}</b> <span class="sub">${esc(fmtWhen(r.t))} · ${Math.round(r.secs / 60 * 10) / 10} min</span></span>`
        + `<span class="keys"><a class="key ro-ok" style="text-decoration:none" href="/api/x/fair/download?t=${Math.floor(r.t)}" title="Download as text">↓</a>`
        + `<button class="key admin-only" data-fdel="${Math.floor(r.t)}" title="Delete this report">✕</button></span></div>`;
    }).join('') || '<div class="sub">None yet.</div>');
    if (!rep) { $('#fr-when').textContent = ''; setHTML('#fr-vs', ''); setHTML('#fr-rows', '<tr><td colspan="8" class="sub">No report yet.</td></tr>'); return; }
    $('#fr-when').textContent = `${d.running ? 'so far · ' : ''}${fmtWhen(rep.t)} · by ${rep.by || '?'}${rep.where ? ' · probe at ' + rep.where : ''}`;
    const v = rep.verdict, lv = { ok: 'FAIR', warn: 'CHECK', bad: 'NOT FAIR' }[v.level];
    $('#fr-verdict').className = `fr-verdict ${v.level}`;
    setHTML('#fr-verdict', `<span class="lv">${d.running ? 'SO FAR · ' : ''}${lv}</span>${esc(v.text || 'Waiting for answers…')}`
      + (rep.arp_error ? `<div class="sub">ARP timing wasn't available (it needs the probe's root service, not a test copy): ${esc(rep.arp_error)}</div>` : ''));
    const team = (t, i) => `<div class="t t${i + 1}"><b>${esc(t.name)}</b><div class="big">${f2(t.med)} <span style="font-size:13px">ms</span></div>`
      + `<span class="sub">jitter ${f2(t.jitter)} ms · worst loss ${t.loss ?? '—'}%</span><span class="sub">${t.answering} of ${t.n} answering${t.modes.length ? ' · ' + t.modes.join(' + ') : ''}`
      + `${t.med_clean != null && t.med_clean !== t.med ? ` · ${f2(t.med_clean)} ms without the flagged seats` : ''}</span></div>`;
    const sv = rep.server, si = sv?.icmp || {}, sa = sv?.a2s || {};
    setHTML('#fr-vs', rep.teams.map(team).join('') + (sv ? `<div class="t sv"><b>GAME SERVER</b><div class="big">${f2(si.p50)} <span style="font-size:13px">ms</span></div>`
      + `<span class="sub">ping · A2S ${f2(sa.p50)} ms · loss ${si.loss ?? '—'}%</span><span class="sub mono">${esc(sv.key)}</span></div>` : ''));
    const rows = [...rep.rows].sort((a, b) => (b.flags.length - a.flags.length) || (a.team - b.team) || a.label.localeCompare(b.label, undefined, { numeric: true }));
    const top = Math.max(0.5, ...rows.map(r => r.st?.p95 ?? 0));
    $('#fr-count').textContent = `${rows.length} seats · ${rows.filter(r => r.flags.length).length} stand out`;
    setHTML('#fr-rows', rows.map(r => {
      const s = r.st || {}, t = rep.teams[r.team];
      const w = s.p50 == null ? 0 : Math.max(2, 100 * s.p50 / top);
      return `<tr class="t${r.team + 1}${r.flags.length ? ' flag' : ''}"><td class="l"><b>${esc(r.label)}</b><div class="sub mono">${r.label !== r.ip ? esc(r.ip) + ' · ' : ''}${esc(r.mode)}</div></td>`
        + `<td class="l tm t${r.team + 1}">${esc(t?.name || '')}</td>`
        + `<td class="l"><div class="bar"><span class="trk"><i style="width:${w.toFixed(1)}%;background:${r.flags.length ? '#ff3b3b' : r.team ? '#f2a33a' : '#4fa3f7'}"></i></span><span>${f2(s.p50)} ms</span></div></td>`
        + `<td>${f2(s.p95)}</td><td>${f2(s.jitter)}</td><td style="${(s.loss || 0) > 0.5 ? 'color:var(--crit)' : ''}">${s.loss == null ? '—' : s.loss + '%'}</td>`
        + `<td class="l mono">${esc(r.port || '—')}</td><td class="l wrap" style="${r.flags.length ? 'color:#ff9b9b' : ''}">${esc(r.flags.join('; ') || '—')}</td></tr>`;
    }).join('') || '<tr><td colspan="8" class="sub">No seats.</td></tr>');
  }

  for (const i of [1, 2]) {
    $(`#fr-team${i}`).addEventListener('input', () => checkTeam(i));
    $(`#fr-g${i}`).addEventListener('change', e => {
      const g = e.target.value; if (!g) return;
      const ta = $(`#fr-team${i}`); ta.value = (ta.value.trim() ? ta.value.trim() + ', ' : '') + `group:${g}`;
      if ($(`#fr-name${i}`).value === `Team ${i}`) $(`#fr-name${i}`).value = g;
      e.target.value = ''; checkTeam(i);
    });
  }
  $('#fr-secs').addEventListener('input', () => { $('#fr-secs-t').textContent = mins(+$('#fr-secs').value || 120); });
  $('#f-fair').addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('/api/x/fair/start', { team1: $('#fr-team1').value, team2: $('#fr-team2').value, name1: $('#fr-name1').value, name2: $('#fr-name2').value,
        server: $('#fr-server').value, secs: +$('#fr-secs').value });
      flash($('#fr-msg'), 'Started', true);
    } catch (err) { flash($('#fr-msg'), err.message); }
    refresh();
  });
  $('#fr-stop').addEventListener('click', async () => { await api('/api/x/fair/stop').catch(() => {}); setTimeout(refresh, 1500); });
  $('#fr-reps').addEventListener('click', async e => {
    if (e.target.closest('a')) return;
    const del = e.target.closest('[data-fdel]');
    if (del) { if (confirm('Delete this report?')) { await api('/api/x/fair/forget', { t: del.dataset.fdel }).catch(err => alert(err.message)); refresh(); } return; }
    const r = e.target.closest('[data-rep]');
    if (r) { picked = +r.dataset.rep; draw(); }
  });
  PAGES.fair = { show: refresh };
  const tick = () => { if (view === 'fair' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
