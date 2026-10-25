'use strict';
// Capture: tcpdump to a ring in RAM, download as .pcap.
(() => {
  let D = null, busy = false, poll = 4000;
  const PRESETS = [['DHCP', 'udp port 67 or udp port 68'], ['ARP', 'arp'], ['Broadcast', 'broadcast or multicast'], ['DNS', 'port 53'],
    ['CS2 / Source', 'udp portrange 27015-27020'], ['Spanning tree', 'stp'], ['CDP / LLDP', 'ether host 01:00:0c:cc:cc:cc or ether proto 0x88cc'], ['Not me', 'not host ' + (location.hostname || '')]];
  setHTML('#cp-presets', PRESETS.map(([n, f]) => `<button type="button" data-f="${esc(f)}">${esc(n)}</button>`).join(''));
  $('#cp-presets').addEventListener('click', e => { const b = e.target.closest('[data-f]'); if (b) $('#cp-filter').value = b.dataset.f; });

  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/capture', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }
  function draw() {
    const d = D, c = d.cur, run = !!(c && c.running);
    poll = run ? 1000 : 4000;
    $('#cp-lamp').innerHTML = run ? `<span class="dot live"></span>capturing${c.filter ? ': ' + esc(c.filter) : ' everything'}`
      : c ? (c.error ? `<span class="dot err"></span>${esc(c.error)}` : `<span class="dot on"></span>stopped ${esc(ago(c.stopped))}`) : '<span class="dot"></span>idle';
    $('#cp-go').hidden = run; $('#cp-stop').hidden = !run;
    const pk = c ? (c.summary && c.summary.captured != null ? c.summary.captured : c.packets) : null;
    setSeg('#cp-pk', pk == null ? '----' : pk, 6);
    $('#cp-pk2').textContent = c && c.summary && c.summary.dropped ? `${c.summary.dropped} dropped by the kernel` : c && !d.conf.live && run ? 'counted at the end' : 'captured';
    setSeg('#cp-mb', c ? (d.ring_bytes / 1e6).toFixed(1) : '--', 4);
    $('#cp-mb2').textContent = `MB of ${d.conf.ring_mb} (oldest dropped first)`;
    const secs = c ? Math.round(((run ? d.now : c.stopped) || d.now) - c.started) : null;
    setSeg('#cp-tm', secs == null ? '---' : secs, 4);
    $('#cp-tm2').textContent = c && c.secs ? `seconds of ${c.secs}` : 'seconds';
    $('#cp-dl').hidden = !(c && d.ring_bytes);
    $('#cp-keep').hidden = !(c && !run && d.ring_bytes && !c.kept);
    $('#cp-info').textContent = c ? `${c.why || ''}${c.by ? ' · started by ' + c.by : ''}${c.kept ? ' · kept as ' + c.kept : ''}` : '';
    $('#cp-livecard').hidden = !d.conf.live;
    setHTML('#cp-lines', esc(d.lines.join('\n')) || (run ? 'Waiting for packets…' : 'Start a capture to see packets here.'));
    setHTML('#cp-kept', d.kept.map(k => `<tr><td class="l mono">${esc(k.name)}</td><td>${(k.size / 1e6).toFixed(2)} MB</td><td>${esc(fmtWhen(k.t))}</td>`
      + `<td><span class="keys"><a class="key ro-ok" style="text-decoration:none" href="/api/x/capture/file/${encodeURIComponent(k.name)}">Download</a><button class="key admin-only" data-del="${esc(k.name)}">✕</button></span></td></tr>`).join('')
      || '<tr><td colspan="4" class="sub">None yet.</td></tr>');
    if (!$('#f-capset')._filled) { const f = d.conf; $('#cp-ring').value = f.ring_mb; $('#cp-snap').value = f.snaplen; $('#cp-live').checked = f.live; $('#cp-auto').checked = f.auto; $('#cp-asecs').value = f.auto_secs; $('#f-capset')._filled = true; }
  }
  $('#f-cap').addEventListener('submit', async e => {
    e.preventDefault();
    try { await api('/api/x/capture/start', { filter: $('#cp-filter').value, secs: +$('#cp-secs').value, name: $('#cp-name').value }); refresh(); }
    catch (err) { flash($('#cp-msg'), err.message); }
  });
  $('#cp-stop').addEventListener('click', async () => { await api('/api/x/capture/stop').catch(() => {}); setTimeout(refresh, 600); });
  $('#cp-keep').addEventListener('click', async () => { try { const r = await api('/api/x/capture/keep'); flash($('#cp-msg'), 'Kept as ' + r.name, true); refresh(); } catch (e) { flash($('#cp-msg'), e.message); } });
  $('#cp-kept').addEventListener('click', async e => { const b = e.target.closest('[data-del]'); if (b && confirm('Delete ' + b.dataset.del + '?')) { await api('/api/x/capture/delete', { name: b.dataset.del }).catch(() => {}); refresh(); } });
  $('#f-capset').addEventListener('submit', async e => {
    e.preventDefault();
    try { await api('/api/x/capture/settings', { ring_mb: +$('#cp-ring').value, snaplen: +$('#cp-snap').value, live: $('#cp-live').checked, auto: $('#cp-auto').checked, auto_secs: +$('#cp-asecs').value }); flash($('#cp-smsg'), 'Saved', true); refresh(); }
    catch (err) { flash($('#cp-smsg'), err.message); }
  });
  PAGES.capture = { show: refresh };
  const tick = () => { if (view === 'capture' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
