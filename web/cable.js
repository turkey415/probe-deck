'use strict';
// Cable test: the switch's TDR, pair by pair.
// A login typed on the page (Cable test, Configs): the saved user fills in, passwords stay empty.
function fillLogin(pre, L, key) {
  const u = $('#' + pre + '-user');
  if (!L || u._for === key || document.activeElement === u) return;
  const per = (L.per || {})[key] || {};
  u.value = per.user || L.user || ''; u._for = key;
  $('#' + pre + '-pw').placeholder = per.password_set || L.password_set ? 'saved (type to use another)' : 'not saved';
  $('#' + pre + '-en').placeholder = L.enable_set ? 'saved' : 'not saved';
}
function loginBody(pre) {
  return { user: $('#' + pre + '-user').value.trim(), password: $('#' + pre + '-pw').value, enable: $('#' + pre + '-en').value, remember: $('#' + pre + '-rem').checked };
}
(() => {
  let D = null, busy = false, poll = 5000;
  const LV = { ok: 'on', warn: 'warn', bad: 'err', error: 'err', unknown: '' };
  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/cable', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    catch { /* the probe's own link may be down mid-test */ }
    finally { busy = false; }
  }
  const pairWell = p => {
    const st = (p.status || '').toLowerCase(), good = st === 'normal', meh = st === 'not completed' || st === 'n/a';
    return `<div class="well${good ? '' : meh ? ' off' : ' alarm'}"><span class="k">Pair ${p.pair}</span><div class="big-port" style="font-size:24px">${esc(good ? 'OK' : p.status.toUpperCase())}</div>`
      + `<span class="un">${p.length != null ? `${p.length} m${p.tol ? ' ± ' + p.tol : ''}` : '—'}</span></div>`;
  };
  function draw() {
    const d = D, r = d.run;
    poll = r && r.running ? 1000 : 5000;
    // the switch list, keeping the pick
    const cur = $('#cb-dev').value;
    setHTML('#cb-dev', d.targets.map(t => `<option value="${esc(t.key)}">${esc(t.name)}${t.host ? ' · ' + esc(t.host) : ''}</option>`).join('') || '<option value="">no switches yet</option>');
    if (cur && d.targets.some(t => t.key === cur)) $('#cb-dev').value = cur;
    else if (d.own.host && d.targets.some(t => t.key === d.own.host)) $('#cb-dev').value = d.own.host;
    if (!$('#cb-port').value && d.own.port) $('#cb-port').value = ifShort(d.own.port);
    $('#cb-mine').hidden = !d.own.host;
    fillLogin('cb', d.login, $('#cb-dev').value);
    $('#cb-go').disabled = !!(r && r.running);
    $('#cb-lamp').innerHTML = !r ? '<span class="dot"></span>not run yet' : r.running ? `<span class="dot live"></span>${esc(r.step)}…`
      : `<span class="dot ${LV[r.level] || ''}"></span>${esc(r.switch)} ${esc(r.port)} · ${esc(fmtWhen(r.t))}`;
    setHTML('#cb-pairs', r && r.pairs ? r.pairs.map(pairWell).join('') : ['A', 'B', 'C', 'D'].map(x => `<div class="well off"><span class="k">Pair ${x}</span><div class="big-port" style="font-size:24px">—</div><span class="un">—</span></div>`).join(''));
    $('#cb-verdict').textContent = r ? (r.running ? (r.mine ? 'Testing the probe’s own jack: the probe drops off the network for a few seconds.' : '') : (r.verdict || '') + (r.speed ? ` Link speed during the test: ${r.speed}.` : '')) : 'Pick a switch and port, or press This jack.';
    const log = r && r.log ? r.log.map(([t, s]) => `${String(t).padStart(5)} s  ${s}`).join('\n') : '';
    $('#cb-log').hidden = !log; setHTML('#cb-log', esc(log));
    setHTML('#cb-hist', d.history.map(h => `<tr><td>${esc(fmtWhen(h.t))}</td><td class="l">${esc(h.switch)}</td><td class="l mono">${esc(h.port)}</td>`
      + `<td class="l">${(h.pairs || []).map(p => `<span class="chip ${p.status === 'Normal' ? 'ok' : /not completed|n\/a/i.test(p.status) ? '' : 'bad'}">${p.pair} ${p.length != null ? p.length + 'm' : ''}</span>`).join(' ')}</td>`
      + `<td class="l wrap"><span class="dot ${LV[h.level] || ''}"></span> ${esc(h.verdict || '')}</td></tr>`).join('') || '<tr><td colspan="5" class="sub">No tests yet.</td></tr>');
  }
  $('#cb-mine').addEventListener('click', () => { if (D && D.own.host) { $('#cb-dev').value = D.own.host; $('#cb-port').value = ifShort(D.own.port || ''); } });
  $('#f-cable').addEventListener('submit', async e => {
    e.preventDefault(); $('#cb-err').hidden = true;
    try { await api('/api/x/cable/start', { dev: $('#cb-dev').value, port: $('#cb-port').value.trim(), ...loginBody('cb') }); $('#cb-pw').value = ''; $('#cb-en').value = ''; refresh(); }
    catch (err) { $('#cb-err').textContent = err.message; $('#cb-err').hidden = false; }
  });
  $('#cb-clear').addEventListener('click', async () => { if (confirm('Clear the cable test history?')) { await api('/api/x/cable/clear').catch(() => {}); refresh(); } });
  $('#cb-dev').addEventListener('change', () => { $('#cb-user')._for = null; if (D) fillLogin('cb', D.login, $('#cb-dev').value); });
  PAGES.cable = { show: refresh };
  const tick = () => { if (view === 'cable' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
