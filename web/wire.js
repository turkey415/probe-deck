'use strict';
// The Overview's Time to online and Port details, and the Watchdog's "On the wire" (wire.py, online.py).
(() => {
  let busy = false;
  const LVL = { ok: 'on', info: '', warn: 'warn', bad: 'err' };
  async function get(u) { const r = await fetch(u, { cache: 'no-store' }); return r.ok ? r.json() : null; }

  function drawOnline(o) {
    const r = o && o.latest, w = $('#w-online');
    if (!r) { setSeg('#on-secs', '--.-', 4); $('#on-what').textContent = 'not measured yet'; $('#on-note').textContent = 'Measured when the link comes up.'; w.classList.add('off'); return; }
    w.classList.remove('off');
    const live = r.mode === 'live', secs = live ? (r.gw_s ?? r.ip_s) : (r.offer_s ?? r.ip_s);
    setSeg('#on-secs', secs == null ? (r.running ? '..' : '--.-') : secs.toFixed(1), 4);
    w.classList.toggle('alarm', !!r.slow);
    $('#on-what').textContent = r.running ? 'seconds · measuring…' : live ? `seconds to the gateway answering${r.slow ? ' · portfast off?' : ''}` : `seconds to a DHCP offer · from the boot logs${r.slow ? ' · portfast off?' : ''}`;
    const b = r.boot;
    const bootLine = b ? ` Boot, from power-on: the port came up at ${b.link_at} s, the address at ${b.ip_at ?? '—'} s, ${b.ready_at != null ? `, and Probe Deck started at ${b.ready_at} s` : ''}.` : '';
    $('#on-note').textContent = (live
      ? `Link up ${fmtWhen(r.t)}: address in ${r.ip_s ?? '—'} s, gateway in ${r.gw_s ?? '—'} s, internet in ${r.inet_s ?? '—'} s.${r.gave_up ? ' The gateway never answered.' : ''} Over ${o.portfast_secs} s means the switch port probably isn't set to portfast.`
      : `The probe is powered by this port (PoE), so it boots after the port is already up and can't time it live. These come from the boot logs: link up, then a DHCP offer ${r.offer_s ?? '—'} s and an address ${r.ip_s ?? '—'} s later. DHCP's own retry timer adds a few seconds (${r.retry_s ?? '?'} s before its first try this time), so it's a rough figure; 30 s or more still means portfast is off. For live timing, bounce the port or power the probe from USB-C.`) + bootLine;
  }

  function drawPort(p) {
    if (!p) return;
    $('#pd-via').textContent = p.via ? `from ${p.via} and DHCP` : 'waiting for CDP / LLDP';
    const g = p.gateway, dh = p.dhcp || {}, x = p.dot1x;
    const rows = [
      ['Data VLAN', p.data_vlan ?? '—'], ['Voice VLAN', p.voice_vlan ?? 'none'], ['PoE offered', p.poe || '—'], ['Duplex (switch)', p.duplex || '—'],
      ['802.1X', x ? (x.code === 'Request' ? `asked for${x.eap ? ' (' + x.eap + ')' : ''}` : x.code) : 'not asked'],
      ['Trunking (DTP)', p.dtp ? 'the switch offers it' : 'not offered'],
      ['Gateway', g ? `${g.kind || 'real MAC'}${g.vendor ? ' · ' + g.vendor : ''}` : '—'],
      ['Phone TFTP (150 / 66)', [...(dh.tftp_ips || []), dh.tftp_name].filter(Boolean).join(', ') || 'none'],
      ['Option 43', dh.opt43 ? dh.opt43.slice(0, 24) + (dh.opt43.length > 24 ? '…' : '') : 'none'], ['NTP (42)', (dh.ntp || []).join(', ') || 'none'],
    ];
    setHTML('#pd-kv', rows.map(([k, v]) => `<div><dt>${k}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`).join(''));
    const notes = [];
    if (x && x.code === 'Request') notes.push(['warn', 'This port asks for 802.1X: a PC without a login may be put on a restricted VLAN or get nothing.']);
    if (x && x.code === 'Failure') notes.push(['bad', '802.1X said Failure to something on this port.']);
    if (p.dtp) notes.push(['warn', 'The switch offers to form a trunk on this port (DTP): a PC could be talked into a trunk. Set access ports to "switchport mode access" and "switchport nonegotiate".']);
    setHTML('#pd-notes', notes.map(([l, t]) => `<div class="note${l === 'bad' ? ' crit' : ''}" style="margin-top:6px">${esc(t)}</div>`).join(''));
  }

  function drawWire(w) {
    if (!w) return;
    const [head, lvl] = w.headline || ['', 'ok'];
    $('#wr-lamp').innerHTML = !w.on ? '<span class="dot"></span>off' : w.error ? `<span class="dot err"></span>${esc(w.error)}` : w.paused ? '<span class="dot warn"></span>paused 30 s: a flood' : `<span class="dot ${LVL[lvl] || 'on'}"></span>${esc(head)}`;
    const by = {};
    for (const f of w.findings) (by[f.section] = by[f.section] || []).push(f);
    setHTML('#wr-list', Object.entries(by).map(([sec, fs]) => `<div class="wrsec"><span class="k">${esc(sec)}</span>${fs.map(f => `<div class="ev wr"><span class="dot ${LVL[f.level] || ''}"></span><span>${esc(f.text)}</span></div>`).join('')}</div>`).join('')
      || '<div class="empty">Listening…</div>');
  }

  async function refreshProbe() { const [o, w] = await Promise.all([get('/api/x/online'), get('/api/x/wire')]); drawOnline(o); if (w) drawPort(w.port); }
  async function refreshWatch() { drawWire(await get('/api/x/wire')); }
  $('#wr-restart').addEventListener('click', async () => { await api('/api/x/wire/restart').catch(() => {}); refreshWatch(); });
  const prevProbe = PAGES.probe, prevWatch = PAGES.watch;
  PAGES.probe = { show: () => { refreshProbe(); prevProbe && prevProbe.show(); } };
  PAGES.watch = { show: () => { refreshWatch(); prevWatch && prevWatch.show(); } };
  setInterval(() => { if (!ME) return; if (view === 'probe') refreshProbe(); if (view === 'watch') refreshWatch(); }, 5000);
})();
