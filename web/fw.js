'use strict';
// Firewall: the LAN's firewall over SNMP (fwmon.py), plus the temperature its cron job sends.
(() => {
  let D = null, busy = false;
  const POLL = 5000, SPAN = 3600;
  const LV = { ok: 'on', warn: 'warn', crit: 'err' };
  const charts = {
    cpu: new Chart('#fw-c-cpu', { ago: true, minTop: 100, axis: v => v + '%' }),
    st: new Chart('#fw-c-st', { ago: true, minTop: 10, axis: v => parseFloat(v.toPrecision(3)) + '' }),
    net: new Chart('#fw-c-net', { ago: true, minTop: 1e6, axis: axisBps }),
    temp: new Chart('#fw-c-temp', { ago: true, minTop: 80, axis: v => v + '°' }),
  };
  const lvl = (v, [warn, crit]) => v == null ? 'ok' : v >= crit ? 'crit' : v >= warn ? 'warn' : 'ok';
  const kv = rows => rows.filter(r => r).map(([k, v, t]) => `<div${t ? ` title="${esc(t)}"` : ''}><dt>${esc(k)}</dt><dd>${esc(v ?? '—')}</dd></div>`).join('');
  const well = (k, v, u, l) => `<div class="well${l === 'crit' ? ' alarm' : ''}"><span class="k">${k}</span><div class="big-port" style="font-size:22px;color:${l === 'crit' ? 'var(--crit)' : l === 'warn' ? 'var(--warn)' : ''}">${esc(v)}</div><span class="un">${esc(u)}</span></div>`;
  const n = v => v == null ? '—' : Math.round(v).toLocaleString();
  function fmtB(b) {
    if (b == null) return '—';
    const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']; let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return (i ? b.toFixed(b >= 100 ? 0 : 1) : Math.round(b)) + ' ' + u[i];
  }
  const ifLabel = i => i.alias || i.name;
  const BAD = ' title="The network driver reports a negative count here, so it can\'t be trusted"';

  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/fw', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }

  function draw() {
    const d = D, c = d.cur, cf = d.conf, t = d.temp, L = d.limits;
    const set = cf.host && cf.on;
    const lamp = !cf.host ? '<span class="dot"></span>not set up' : !cf.on ? '<span class="dot"></span>off'
      : d.level === 'crit' && d.error ? '<span class="dot err"></span>not answering'
      : `<span class="dot ${LV[d.level] || ''}"></span>${d.why.length ? esc(d.why.join(', ')) : 'all normal'}`;
    $('#fw-lamp').innerHTML = lamp; $('#fw-lamp2').innerHTML = lamp;
    $('#fw-title').textContent = c && c.sysname ? c.sysname.split('.')[0] : 'Firewall';
    const ban = $('#fw-banner');
    ban.hidden = !(set && d.error) && set;
    ban.textContent = !cf.host ? 'Type the firewall\'s address and SNMP community on the left.' : !cf.on ? 'Watching is off.' : d.error ? `${d.error}${d.ok_at ? ` Last good read ${fmtWhen(d.ok_at)}.` : ''}` : '';
    const stale = !c || !!d.error;
    $('#fw-meters').parentElement.classList.toggle('dark', stale);

    // settings form (filled once)
    if (!$('#f-fw')._filled) {
      $('#fw-host').value = cf.host; $('#fw-on').checked = cf.on; $('#f-fw')._filled = true;
    }
    $('#fw-temp').checked = t.on;
    $('#fw-comm').placeholder = cf.community_set ? 'saved · leave blank to keep it' : cf.switch_community ? 'blank uses the Switches community' : 'e.g. public';
    $('#fw-commhint').textContent = cf.community_set ? 'Saved. Type a new one to change it.' : cf.switch_community ? 'Blank uses the one set on the Switches page.' : '';

    // temperature
    const tl = !t.on ? '<span class="dot"></span>off' : t.error ? `<span class="dot err"></span>${esc(t.error)}`
      : t.fresh ? `<span class="dot ${LV[lvl(t.max, L.temp)]}"></span>${t.max.toFixed(0)} °C` : t.at ? '<span class="dot warn"></span>stopped arriving' : '<span class="dot warn"></span>waiting for the firewall';
    $('#fw-tlamp').innerHTML = tl;
    $('#fw-tport').textContent = t.port;
    const ip = d.probe_ip || 'PROBE-IP';
    $('#fw-cron').textContent = `/sbin/sysctl -q dev.cpu hw.acpi.thermal | /usr/bin/grep -E 'temperature|tjmax' | /usr/bin/nc -u -w1 ${ip} ${t.port}`;
    $('#fw-thow').hidden = !t.on;
    setHTML('#fw-tinfo', !t.on ? '' : kv([
      ['Last reading', t.at ? `${fmtWhen(t.at)} (${Math.round(d.now - t.at)} s ago)` : 'none yet'],
      t.from ? ['From', t.from] : null,
      ...t.sensors.map(s => [s.label, `${s.c.toFixed(1)} °C`, s.name]),
      t.tjmax ? ['CPU limit (TjMax)', `${t.tjmax.toFixed(0)} °C`, 'Where the CPU starts slowing itself down'] : null,
      ...t.ignored.map(x => [`Ignored from ${x.ip}`, `${x.n} packet${x.n === 1 ? '' : 's'}, last ${fmtWhen(x.at)}`, 'Not one of the firewall\'s addresses']),
    ]));

    // meters
    const lc = l => LEVEL_COLOR[l];
    const mem = c ? c.mem : {};
    const tMax = t.tjmax || 100;
    const meters = [
      gNeedle({ title: 'CPU', value: c ? c.cpu : null, max: 100, readout: c && c.cpu != null ? Math.round(c.cpu) + '' : '', unit: '% busy', warn: L.cpu[0], crit: L.cpu[1], color: lc(lvl(c && c.cpu, L.cpu)), cells: 3 }),
      t.on ? gNeedle({ title: 'CPU temp', value: t.fresh ? t.max : null, max: Math.max(100, tMax), readout: t.fresh ? Math.round(t.max) + '' : '', unit: '°C', warn: L.temp[0], crit: L.temp[1], color: lc(lvl(t.fresh ? t.max : null, L.temp)), cells: 3 }) : '',
      gNeedle({ title: 'Memory', value: c ? c.mem_pct : null, max: 100, readout: c && c.mem_pct != null ? Math.round(c.mem_pct) + '' : '', unit: '% used', warn: L.mem[0], crit: L.mem[1], color: lc(lvl(c && c.mem_pct, L.mem)), cells: 3 }),
      gNeedle({ title: 'States', value: c ? c.states : null, max: c && c.limit ? c.limit : 100000, log: true, readout: c && c.states != null ? (c.states >= 10000 ? Math.round(c.states / 1000) + 'k' : String(c.states)) : '',
        unit: c && c.st_pct != null ? `${c.st_pct < 1 ? c.st_pct.toFixed(2) : c.st_pct.toFixed(0)}% of limit` : 'in use', warn: c && c.limit ? c.limit * L.states[0] / 100 : undefined, crit: c && c.limit ? c.limit * L.states[1] / 100 : undefined,
        color: lc(lvl(c && c.st_pct, L.states)), cells: 5 }),
    ];
    const wan = c ? c.ifaces.filter(i => i.up && i.rx_bps != null).sort((a, b) => b.rx_bps - a.rx_bps)[0] : null;
    if (wan) meters.push(gNeedle({ title: `${ifLabel(wan)} down`, value: wan.rx_bps / 1e6, max: wan.speed || 1000, log: true, readout: parseFloat((wan.rx_bps / 1e6).toPrecision(3)) + '', unit: 'Mbps', color: 'var(--accent)', cells: 4 }));
    setHTML('#fw-meters', meters.join(''));
    if (!c) {
      for (const id of ['#fw-wells', '#fw-cores', '#fw-cpukv', '#fw-stbar', '#fw-stkv']) setHTML(id, '');
      setHTML('#fw-if', `<tr><td colspan="12" class="sub">${set ? 'Reading…' : 'Not set up yet.'}</td></tr>`);
      for (const k in charts) { legend('#fw-lg-' + k, []); charts[k].set([], SPAN); }
      return;
    }
    const blocked = c.ifaces.reduce((a, i) => a + (i.block_ps || 0), 0);
    setHTML('#fw-wells', [
      well('Uptime', fmtDur(c.uptime), c.uptime ? 'since ' + fmtWhen(c.t - c.uptime) : ''),
      well('Load', c.load[0] == null ? '—' : c.load[0].toFixed(2), `5 min ${c.load[1] == null ? '—' : c.load[1].toFixed(2)} · 15 min ${c.load[2] == null ? '—' : c.load[2].toFixed(2)} · ${c.cores.length} cores`),
      well('New states', c.st_ins_ps == null ? '…' : String(c.st_ins_ps), 'a second'),
      well('Blocked', String(Math.round(blocked * 10) / 10), 'packets in a second'),
      well('Dropped', n(c.drops_recent), 'for lack of states or memory, last 10 min', c.drops_recent ? 'crit' : 'ok'),
    ].join(''));

    // CPU
    $('#fw-cpusub').textContent = `${c.cores.length} cores`;
    setHTML('#fw-cores', c.cores.map((p, i) => gauge(`CPU ${i}`, (p || 0) / 100, p == null ? '—' : p + '%', lvl(p, L.cpu))).join(''));
    setHTML('#fw-cpukv', kv([['Busy now', c.cpu == null ? '—' : c.cpu + '%', 'Over the last 5 s, all cores'], ['Each core', 'average of the last minute'],
      ['Memory', `${fmtB(mem.total - mem.avail)} of ${fmtB(mem.total)} in use`], ['Cache', fmtB(mem.cache)],
      ['Swap', mem.swap_total ? `${fmtB(mem.swap_total - mem.swap_avail)} of ${fmtB(mem.swap_total)} in use` : 'none']]));

    // state table
    $('#fw-stsub').textContent = c.limit ? `${n(c.states)} of ${n(c.limit)}` : '';
    setHTML('#fw-stbar', c.limit ? gauge('IN USE', c.states / c.limit, `${c.st_pct < 1 ? c.st_pct.toFixed(2) : c.st_pct.toFixed(1)}%`, lvl(c.st_pct, L.states)) : '');
    const k = c.counters || {};
    setHTML('#fw-stkv', kv([['Filtering', c.pf_on ? 'On' : 'Off'], ['New states', c.st_ins_ps == null ? '—' : c.st_ins_ps + ' a second'],
      ['Lookups', c.lookups_ps == null ? '—' : n(c.lookups_ps) + ' a second', 'State table searches: about one per packet'],
      ['Dropped for lack of memory', n(k.mem), 'Packets dropped because the state table or memory was full. Should stay 0.'],
      ['Bad packets', n((k.badoff || 0) + (k.frag || 0) + (k.short || 0)), 'Bad offsets, fragments and short packets, since boot'],
      ['Normalized', n(k.norm), 'Packets the scrub rules cleaned up, since boot']]));

    // interfaces
    const used = c.ifaces.filter(i => i.up || i.alias), unused = c.ifaces.length - used.length;
    $('#fw-ifsub').textContent = unused ? `${unused} unused port${unused === 1 ? '' : 's'} hidden` : '';
    setHTML('#fw-if', used.map(i => `<tr><td class="l mono">${esc(i.name)}</td><td class="l">${esc(i.alias || '—')}</td>
      <td class="l"><span class="dot ${i.up ? 'on' : 'err'}"></span> ${i.up ? 'up' : 'down'}</td>
      <td>${i.up && i.speed ? (i.speed >= 1000 ? i.speed / 1000 + ' Gb/s' : i.speed + ' Mb/s') : '—'}</td>
      <td>${fmtBps(i.rx_bps)}</td><td>${fmtBps(i.tx_bps)}</td><td>${fmtB(i.rx)}</td><td>${fmtB(i.tx)}</td>
      <td${i.err_new ? ' style="color:var(--crit)"' : ''}${i.errors == null ? BAD : ''}>${n(i.errors)}</td><td${i.drops ? ' style="color:var(--warn)"' : ''}${i.drops == null ? BAD : ''}>${n(i.drops)}</td>
      <td>${i.block_ps == null ? '—' : i.block_ps + '/s'}</td><td>${n(i.in_block)}</td></tr>`).join('') || '<tr><td colspan="12" class="sub">No interfaces.</td></tr>');

    // charts
    const h = d.hist, ago = x => d.now - x;
    const ser = (name, color, idx) => ({ name, color, pts: h.map(r => [ago(r[0]), r[idx]]) });
    const names = used.filter(i => i.up).map(i => i.name).slice(0, 6);
    const lab = Object.fromEntries(c.ifaces.map(i => [i.name, ifLabel(i)]));
    const sets = {
      cpu: [ser('CPU busy', C.s[0], 1), ser('Memory used', C.s[2], 3)],
      st: [ser('States', C.s[1], 2)],
      net: names.map((nm, i) => ({ name: lab[nm], color: C.s[i % 8], pts: h.map(r => [ago(r[0]), r[5][nm] ? r[5][nm][0] : null]) })),
      temp: t.on ? [ser('Hottest sensor °C', C.s[1], 4)] : [],
    };
    charts.st.o.minTop = Math.max(10, ...h.map(r => r[2] || 0)) * 1.2;
    for (const [key, v] of Object.entries(sets)) { legend('#fw-lg-' + key, v); charts[key].set(v, SPAN); }
  }

  $('#f-fw').addEventListener('submit', async e => {
    e.preventDefault(); $('#fw-err').hidden = true;
    try {
      await api('/api/x/fw/settings', { host: $('#fw-host').value.trim(), community: $('#fw-comm').value, on: $('#fw-on').checked });
      $('#fw-comm').value = ''; flash($('#fw-msg'), 'Saved', true); setTimeout(refresh, 1500);
    } catch (err) { $('#fw-err').textContent = err.message; $('#fw-err').hidden = false; }
  });
  $('#fw-temp').addEventListener('change', async e => { await api('/api/x/fw/settings', { temp: e.target.checked }).catch(err => alert(err.message)); refresh(); });
  $('#fw-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#fw-cron').textContent); flash($('#fw-copymsg'), 'Copied', true); }
    catch { getSelection().selectAllChildren($('#fw-cron')); flash($('#fw-copymsg'), 'Selected: press Ctrl+C', true); }
  });
  PAGES.fw = { show: refresh };
  const tick = () => { if (view === 'fw' && ME) refresh(); setTimeout(tick, POLL); };
  setTimeout(tick, POLL);
})();
