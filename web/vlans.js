'use strict';
// VLAN walk: DHCP, gateway, DNS and internet on every VLAN of a trunk.
(() => {
  let D = null, busy = false, poll = 5000, shown = null;
  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/vlans', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }
  const okChip = (ok, text) => `<span class="chip ${ok ? 'ok' : 'bad'}">${esc(text)}</span>`;
  function row(r, live) {
    const d = r.dhcp;
    const dh = live && r.step && !d ? `<span class="dot live"></span> ${esc(r.step)}…` : d ? (d.acked ? okChip(true, `${d.secs} s`) : okChip(false, 'no ACK')) + (d.relay ? ` <span class="sub">relay ${esc(d.relay)}</span>` : '') : okChip(false, r.error || 'no answer');
    const addr = d ? `<span class="mono">${esc(d.ip)}/${d.prefix}</span><span class="sub"> from ${esc(d.server)}</span>` : '—';
    const gw = r.gw ? okChip(r.gw.got > 0, `${r.gw.ip} · ${r.gw.got}/${r.gw.of}${r.gw.ms != null ? ' · ' + r.gw.ms.toFixed(1) + ' ms' : ''}`) : d && d.acked && !d.router ? okChip(false, 'no router given') : live && r.step ? '…' : '—';
    const dns = r.dns && r.dns.length ? r.dns.map(x => okChip(x.ok, `${x.server}${x.ok ? ' · ' + x.ms + ' ms' : ' · ' + x.status}`)).join(' ') : d && d.acked && !(d.dns || []).length ? okChip(false, 'none given') : '—';
    const inet = r.inet ? okChip(r.inet.ok, r.inet.ok ? `${r.inet.ms} ms` : (r.inet.error || 'HTTP ' + r.inet.code)) : '—';
    const note = r.error && d ? `<div class="sub">${esc(r.error)}</div>` : '';
    return `<tr><td><b>${r.vlan}</b>${r.native ? ' <span class="chip">native</span>' : ''}</td><td class="l">${dh}</td><td class="l">${addr}${note}</td><td class="l">${gw}</td><td class="l wrap">${dns}</td><td class="l">${inet}</td></tr>`;
  }
  function draw() {
    const d = D, w = d.walk, run = !!(w && w.running);
    poll = run ? 1000 : 5000;
    const res = w ? w.results.slice() : [];
    if (run && w.live && !res.some(x => x === w.live || x.vlan === w.live.vlan)) res.push(w.live);
    const passed = res.filter(r => r.ok).length, nod = res.filter(r => !r.dhcp && !(run && r === w.live)).length;
    const bad = res.filter(r => r.dhcp && !r.ok && !(run && r.step)).length;
    setSeg('#vw-n', w ? res.length : '--', 2); $('#vw-n2').textContent = w ? `of ${w.vlans.length}` : 'walked';
    setSeg('#vw-ok', w ? passed : '--', 2); setSeg('#vw-nd', w ? nod : '--', 2); setSeg('#vw-bad', w ? bad : '--', 2);
    $('#vw-wnd').classList.toggle('alarm', !!(w && !run && nod)); $('#vw-wf').classList.toggle('alarm', bad > 0);
    $('#vw-lamp').innerHTML = !w ? '<span class="dot"></span>not run yet' : run ? `<span class="dot live"></span>VLAN ${w.cur ?? ''}: ${esc((w.live && w.live.step) || 'starting')}…`
      : `<span class="dot ${w.error ? 'err' : bad || nod ? 'warn' : 'on'}"></span>${esc(fmtWhen(w.t))} · ${w.secs} s`;
    $('#vw-sum').textContent = !w || run ? '' : w.error ? w.error : !w.trunk ? 'No VLAN answered DHCP. Is the port a trunk carrying these VLANs? (On an access port only the native VLAN works, untagged.)'
      : `${passed} of ${w.results.length} VLANs passed every test.`;
    $('#vw-when').textContent = w ? `${w.switch ? w.switch.split('.')[0] + ' ' : ''}${w.port ? ifShort(w.port) : ''}` : '';
    setHTML('#vw-list-t', res.map(r => row(r, run)).join('') || '<tr><td colspan="6" class="sub">Type the VLANs and start a walk.</td></tr>');
    $('#vw-go').hidden = run; $('#vw-stop').hidden = !run;
    setHTML('#vw-hint', d.hint.length ? `<button type="button" data-v="${esc(d.hint.join(','))}">The switch's VLANs: ${esc(d.hint.length > 12 ? d.hint.slice(0, 12).join(',') + '…' : d.hint.join(','))}</button>` : '');
    $('#vw-port').innerHTML = d.port ? `Now on ${esc(ifShort(d.port))}${d.switch ? ' of ' + esc(d.switch.split('.')[0]) : ''}, native VLAN ${esc(d.native ?? '?')}.${d.dtp ? ' The switch speaks DTP here, so it may be a trunk.' : ''}` : '';
    setHTML('#vw-hist', d.history.map(h => `<tr><td>${esc(fmtWhen(h.t))}</td><td class="l">${esc(h.port ? ifShort(h.port) : '—')}${h.switch ? ' · ' + esc(h.switch.split('.')[0]) : ''}</td><td>${h.vlans.length}</td>`
      + `<td>${h.results.filter(r => r.ok).length}</td><td>${h.results.filter(r => !r.dhcp).length}</td></tr>`).join('') || '<tr><td colspan="5" class="sub">None yet.</td></tr>');
    if (!$('#f-vlans')._filled) { $('#vw-list').value = d.vlans; $('#f-vlans')._filled = true; }
  }
  $('#vw-hint').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (b) $('#vw-list').value = b.dataset.v; });
  $('#f-vlans').addEventListener('submit', async e => {
    e.preventDefault(); $('#vw-err').hidden = true;
    try { await api('/api/x/vlans/start', { vlans: $('#vw-list').value }); refresh(); }
    catch (err) { $('#vw-err').textContent = err.message; $('#vw-err').hidden = false; }
  });
  $('#vw-stop').addEventListener('click', async () => { await api('/api/x/vlans/stop').catch(() => {}); refresh(); });
  PAGES.vlans = { show: refresh };
  const tick = () => { if (view === 'vlans' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
