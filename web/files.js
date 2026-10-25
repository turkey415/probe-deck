'use strict';
// Files: the probe's TFTP server and its files.
(() => {
  let D = null, busy = false, poll = 4000, pick = null;
  const mb = b => (b / 1e6).toFixed(b >= 1e8 ? 0 : 1);
  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/files', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }
  function draw() {
    const d = D, ip = d.probe_ip || '<probe IP>';
    poll = d.active.length ? 1000 : 4000;
    $('#fl-lamp').innerHTML = !d.tftp ? '<span class="dot"></span>off' : d.listening ? `<span class="dot on"></span>on${d.allow_write ? ' · writes allowed' : ' · read-only'}`
      : `<span class="dot err"></span>${esc(d.error || 'not listening')}`;
    const f = pick && d.files.find(x => x.name === pick) || d.files[0];
    const name = f ? f.name : 'c3560cx-universalk9-mz.152-7.E9.bin', sw = (d.neighbor || 'switch').split('.')[0];
    setHTML('#fl-cmds', esc(`copy tftp://${ip}/${name} flash:\n` + (f && f.md5 ? `verify /md5 flash:${name} ${f.md5}\n` : '') + `copy running-config tftp://${ip}/${sw}-confg`));
    setSeg('#fl-n', d.files.length, 3);
    setSeg('#fl-used', mb(d.files.reduce((a, x) => a + x.size, 0)), 5);
    setSeg('#fl-free', (d.free / 1e9).toFixed(1), 4);
    $('#fl-xfer').innerHTML = d.active.length ? d.active.map(a => `<span class="dot live"></span>${esc(a.ip)} ${a.op === 'read' ? '←' : '→'} ${esc(a.file || '')} ${a.size ? Math.round(a.bytes / a.size * 100) + '%' : mb(a.bytes) + ' MB'}`).join(' · ') : '';
    setHTML('#fl-list', d.files.map(x => `<tr${x.name === name ? ' class="sel"' : ''}><td class="l mono"><a href="#files" data-pick="${esc(x.name)}" style="color:inherit">${esc(x.name)}</a></td><td>${mb(x.size)} MB</td>`
      + `<td class="l mono" title="${esc(x.md5 || '')}">${x.md5 ? esc(x.md5.slice(0, 12)) + '…' : '<span class="sub">working…</span>'}</td><td>${esc(fmtWhen(x.t))}</td>`
      + `<td><span class="keys"><a class="key ro-ok" style="text-decoration:none" href="/api/x/files/file/${encodeURIComponent(x.name)}">Download</a><button class="key admin-only" data-del="${esc(x.name)}" title="Delete">✕</button></span></td></tr>`).join('')
      || '<tr><td colspan="5" class="sub">No files yet: upload one on the left.</td></tr>');
    setHTML('#fl-log', d.log.map(l => `<tr><td>${esc(fmtWhen(l.started))}</td><td class="l mono">${esc(l.ip)}</td><td class="l mono">${esc(l.file || '?')}</td><td class="l">${l.op === 'read' ? 'to the switch' : 'from the switch'}</td>`
      + `<td>${mb(l.bytes)} MB</td><td>${l.secs} s</td><td class="l">${l.ok ? '<span class="chip ok">done</span>' : `<span class="chip bad">${esc(l.error || 'failed')}</span>`}</td></tr>`).join('')
      || '<tr><td colspan="7" class="sub">None yet.</td></tr>');
    if (!$('#f-files')._filled) { $('#fl-on').checked = d.tftp; $('#fl-write').checked = d.allow_write; $('#f-files')._filled = true; }
  }
  function upload(file) {
    return new Promise((ok, bad) => {
      const x = new XMLHttpRequest();
      x.open('POST', '/api/x/files/upload?name=' + encodeURIComponent(file.name));
      x.setRequestHeader('Content-Type', 'application/octet-stream');
      x.upload.onprogress = e => { if (e.lengthComputable) $('#fl-bar i').style.width = (e.loaded / e.total * 100).toFixed(1) + '%'; };
      x.onload = () => { let j = {}; try { j = JSON.parse(x.responseText); } catch {} x.status === 200 ? ok(j) : bad(new Error(j.error || x.statusText)); };
      x.onerror = () => bad(new Error('Upload failed'));
      x.send(file);
    });
  }
  $('#fl-up').addEventListener('click', async () => {
    const files = [...$('#fl-file').files];
    if (!files.length) return flash($('#fl-umsg'), 'Pick a file first');
    $('#fl-bar').hidden = false; $('#fl-up').disabled = true;
    try {
      for (const f of files) {
        if (D && D.files.some(x => x.name === f.name) && !confirm(`Replace ${f.name}?`)) continue;
        $('#fl-bar i').style.width = '0';
        flash($('#fl-umsg'), 'Uploading ' + f.name, true);
        await upload(f);
      }
      flash($('#fl-umsg'), 'Uploaded', true); $('#fl-file').value = '';
    } catch (e) { flash($('#fl-umsg'), e.message); }
    finally { $('#fl-up').disabled = false; setTimeout(() => { $('#fl-bar').hidden = true; }, 1500); refresh(); }
  });
  $('#fl-list').addEventListener('click', async e => {
    const p = e.target.closest('[data-pick]');
    if (p) { e.preventDefault(); pick = p.dataset.pick; draw(); return; }
    const b = e.target.closest('[data-del]');
    if (b && confirm('Delete ' + b.dataset.del + '?')) { await api('/api/x/files/delete', { name: b.dataset.del }).catch(err => alert(err.message)); refresh(); }
  });
  $('#f-files').addEventListener('submit', async e => {
    e.preventDefault();
    try { await api('/api/x/files/settings', { tftp: $('#fl-on').checked, allow_write: $('#fl-write').checked }); flash($('#fl-smsg'), 'Saved', true); setTimeout(refresh, 800); }
    catch (err) { flash($('#fl-smsg'), err.message); }
  });
  PAGES.files = { show: refresh };
  const tick = () => { if (view === 'files' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
