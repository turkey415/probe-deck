'use strict';
// mDNS browser: services announced on the LAN, by device (grouped by category) or by service type.
(() => {
  let D = null, busy = false, poll = 5000;
  let by = lsGet('pd-md-by', 'dev');
  const shut = new Set((lsGet('pd-md-shut', '') || '').split(',').filter(Boolean));   // folded categories
  const open = new Set();                                                           // expanded devices
  const devKey = d => d.host || '?' + d.name;
  // Stable colours: a hue from the name, so a service type or host keeps its colour on every visit.
  const hue = str => { let h = 2166136261; for (const c of String(str)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0) % 360; };
  const CATS = ['print', 'media', 'home', 'apple', 'computer', 'lab', 'other'];

  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/mdns', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }

  function addr(i) { return `${i.ips[0] || i.host || '?'}${i.port ? ':' + i.port : ''}`; }

  function webLink(i) {
    if (i.type !== '_http._tcp.local' && i.type !== '_https._tcp.local') return '';
    const ip = i.ips[0] || (i.host ? i.host + '.local' : '');
    if (!ip || !i.port) return '';
    const proto = i.type === '_https._tcp.local' ? 'https' : 'http';
    const path = ((i.txt || []).find(t => t.startsWith('path=')) || 'path=/').slice(5) || '/';
    const h = ip.includes(':') ? `[${ip}]` : ip;
    return ` <a href="${esc(`${proto}://${h}:${i.port}${path.startsWith('/') ? path : '/' + path}`)}" target="_blank" rel="noopener" class="md-open">open ↗</a>`;
  }

  function deviceRow(d) {
    const k = devKey(d), isOpen = open.has(k);
    const subs = [d.host && d.host !== d.name ? d.host + '.local' : '', d.model].filter(Boolean);
    const chips = [...new Map(d.services.map(s => [s.friendly, s])).keys()].map(f => `<span class="chip svc" style="--h:${hue(f)}">${esc(f)}</span>`).join('');
    const extra = d.ips.length > 1 ? ` <span class="sub">+${d.ips.length - 1}</span>` : '';
    const det = isOpen ? `<div class="det tablewrap"><table><tbody>${d.services.map(i => `<tr><td class="l"><b>${esc(i.friendly)}</b>${webLink(i)}<div class="sub">${esc(i.name)}</div></td>`
      + `<td class="l mono">${esc(addr(i))}</td><td class="l wrap sub mono">${esc((i.txt || []).join(' · ') || '—')}</td></tr>`).join('')}`
      + (d.ips.length > 1 ? `<tr><td class="l sub">All addresses</td><td class="l mono wrap" colspan="2">${d.ips.map(esc).join(', ')}</td></tr>` : '')
      + `</tbody></table></div>` : '';
    return `<div class="mddev"><button type="button" data-dev="${esc(k)}" aria-expanded="${isOpen}">`
      + `<span class="nm"><b><span class="dotc" style="--hc:hsl(${hue(k)} 70% 60%)"></span>${esc(d.name)}</b>${subs.length ? `<span class="sub">${esc(subs.join(' · '))}</span>` : ''}</span>`
      + `<span class="ad mono">${esc(d.ips[0] || '—')}${extra}</span><span class="sv">${chips}</span></button>${det}</div>`;
  }

  function drawDevices(q) {
    const devs = D.devices.filter(d => !q || [d.name, d.host, d.model, ...d.ips, ...d.services.flatMap(s => [s.friendly, s.type, s.name, ...(s.txt || [])])]
      .join(' ').toLowerCase().includes(q));
    const groups = new Map();
    for (const d of devs) { if (!groups.has(d.cat)) groups.set(d.cat, []); groups.get(d.cat).push(d); }
    return [...groups].map(([c, list]) => `<div class="mdcat${shut.has(c) && !q ? ' shut' : ''}" data-c="${esc(c)}"><button type="button" data-cat="${esc(c)}" aria-expanded="${!(shut.has(c) && !q)}">`
      + `<span class="car">▾</span>${esc(D.cats[c] || c)} <span class="sub">(${list.length})</span></button>`
      + `<div class="mddevs">${list.map(deviceRow).join('')}</div></div>`).join('');
  }

  function drawTypes(q) {
    const types = D.types.map(t => ({ ...t, items: t.items.filter(i => !q || [t.friendly, t.type, i.name, i.host, ...i.ips, ...(i.txt || [])].join(' ').toLowerCase().includes(q)) })).filter(t => t.items.length);
    return types.map(t => `<div class="mdtype" style="--h:${hue(t.friendly)}"><div class="mdh"><b>${esc(t.friendly)}</b> <span class="sub mono">${esc(t.type.replace('.local', ''))}</span> <span class="chip">${t.items.length}</span></div>`
      + `<div class="tablewrap"><table><tbody>${t.items.map(i => `<tr><td class="l">${esc(i.name)}${webLink({ ...i, type: t.type })}</td><td class="l mono">${esc(addr(i))}</td>`
        + `<td class="l sub">${esc(i.host)}</td><td class="l wrap sub mono" style="font-size:11.5px">${esc((i.txt || []).slice(0, 4).join(' · '))}</td></tr>`).join('')}</tbody></table></div></div>`).join('');
  }

  function draw() {
    const d = D;
    poll = d.running ? 1200 : 5000;
    $('#md-lamp').innerHTML = d.running ? '<span class="dot live"></span>asking…' : d.error ? `<span class="dot err"></span>${esc(d.error)}` : d.last_scan ? `<span class="dot on"></span>${esc(ago(d.last_scan))}` : '<span class="dot"></span>not yet';
    $('#md-go').disabled = d.running;
    const n = d.types.reduce((a, t) => a + t.items.length, 0);
    setHTML('#md-info', [['Devices', d.devices.length], ['Services', n], ['Service types', d.types.length], ['Devices answering', d.responders], ['Announcements heard', d.multicast_seen]]
      .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join(''));
    const present = new Set(d.devices.map(x => x.cat));
    setHTML('#md-legend', by === 'dev' ? CATS.filter(c => present.has(c)).map(c => `<span class="mdcat-l" style="--cc:var(--c-${c})">${esc(d.cats[c] || c)}</span>`).join('') : '');
    $('#md-count').textContent = `${d.devices.length} devices · ${n} services`;
    const q = $('#md-q').value.trim().toLowerCase();
    setHTML('#md-list', (by === 'type' ? drawTypes(q) : drawDevices(q))
      || `<div class="empty">${n ? 'Nothing matches.' : d.running ? 'Asking…' : 'Nothing announced yet.'}</div>`);
    $('#md-empty').textContent = d.empty.length ? `Types announced with nobody answering for them: ${d.empty.join(', ')}` : '';
  }

  for (const r of document.querySelectorAll('input[name=md-by]')) {
    r.checked = r.value === by;
    r.addEventListener('change', () => { by = r.value; lsSet('pd-md-by', by); D && draw(); });
  }
  $('#md-list').addEventListener('click', e => {
    if (e.target.closest('a')) return;
    const c = e.target.closest('[data-cat]'), v = e.target.closest('[data-dev]');
    if (c) { const k = c.dataset.cat; shut.has(k) ? shut.delete(k) : shut.add(k); lsSet('pd-md-shut', [...shut].join(',')); }
    else if (v) { const k = v.dataset.dev; open.has(k) ? open.delete(k) : open.add(k); }
    else return;
    D && draw();
  });
  $('#md-q').addEventListener('input', () => D && draw());
  $('#md-go').addEventListener('click', async () => { await api('/api/x/mdns/scan').catch(err => alert(err.message)); setTimeout(refresh, 500); });
  $('#md-reset').addEventListener('click', async () => { await api('/api/x/mdns/reset').catch(() => {}); await api('/api/x/mdns/scan').catch(() => {}); setTimeout(refresh, 500); });
  PAGES.mdns = { show: refresh };
  const tick = () => { if (view === 'mdns' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
