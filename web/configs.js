'use strict';
// Configs: switch running-configs, kept when they change, compared.
(() => {
  let D = null, busy = false, poll = 5000, open = null;
  const ticked = new Set();
  const VIA = { ssh: 'SSH', console: 'Console cable', off: 'Off' };
  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/configs', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    finally { busy = false; }
  }
  function draw() {
    const d = D, now = d.now, L = d.login;
    poll = d.devices.some(x => x.busy || x.queued) ? 1500 : 5000;
    $('#cf-login').innerHTML = !d.paramiko ? '<span class="dot err"></span>python3-paramiko missing' : L.user ? `<span class="dot on"></span>${esc(L.user)}${L.password_set ? '' : ' · no password'}` : '<span class="dot warn"></span>not set';
    const kept = d.devices.filter(x => x.versions.length);
    setSeg('#cf-n', kept.length, 2);
    const ch = d.devices.filter(x => x.versions.length > 1 && now - x.versions[0].t < 86400).length;
    setSeg('#cf-ch', ch, 2); $('#cf-wch').classList.toggle('off', !ch);
    setSeg('#cf-v', d.devices.reduce((a, x) => a + x.versions.length, 0), 3);
    const fails = d.devices.filter(x => x.last && !x.last.ok).length;
    setSeg('#cf-f', fails, 2); $('#cf-wf').classList.toggle('alarm', fails > 0); $('#cf-wf').classList.toggle('off', !fails);
    const run = d.devices.find(x => x.busy);
    $('#cf-lamp').innerHTML = run ? `<span class="dot live"></span>pulling ${esc(run.name)}…` : `<span class="dot ${fails ? 'warn' : kept.length ? 'on' : ''}"></span>${d.every ? 'every ' + d.every + ' h' : 'by hand'}`;
    setHTML('#cf-list', d.devices.map(x => {
      const l = x.last, v = x.versions[0];
      const how = isAdmin() ? `<select data-via="${esc(x.key)}" style="width:auto">${Object.entries(VIA).filter(([k]) => x.console ? k !== 'ssh' : k !== 'console').map(([k, n]) => `<option value="${k}"${x.via === k ? ' selected' : ''}>${n}</option>`).join('')}</select>` : esc(VIA[x.via]);
      return `<tr${open === x.key ? ' class="sel"' : ''}><td class="l"><b>${esc(x.name)}</b>${x.host ? ` <span class="sub mono">${esc(x.host)}</span>` : ''}${x.neighbor ? ' <span class="chip">probe’s switch</span>' : ''}</td><td class="l">${how}</td>`
        + `<td class="l">${x.busy ? '<span class="dot live"></span> pulling…' : x.queued ? 'queued' : l ? (l.ok ? `<span class="dot on"></span> ${esc(gAgo(l.t))}${l.changed ? ' · <b>changed</b>' : ''}` : `<span class="dot err"></span> <span class="bad">${esc(l.error || 'failed')}</span>`) : '<span class="sub">never</span>'}</td>`
        + `<td>${x.versions.length || '—'}</td><td>${v ? esc(gAgo(v.t)) : '—'}</td>`
        + `<td><span class="keys">${x.versions.length ? `<button class="key ro-ok" data-open="${esc(x.key)}">History</button>` : ''}<button class="key admin-only" data-pull="${esc(x.key)}"${x.busy ? ' disabled' : ''}>Pull</button></span></td></tr>`;
    }).join(''));
    const dev = open && d.devices.find(x => x.key === open);
    $('#cf-hcard').hidden = !dev;
    if (dev) {
      $('#cf-htitle').textContent = `History · ${dev.name}`;
      setHTML('#cf-hist', dev.versions.map((v, i) => `<tr><td class="admin-only"><input type="checkbox" data-tick="${v.file}"${ticked.has(v.file) ? ' checked' : ''}></td>`
        + `<td class="l">${esc(fmtWhen(v.t))}${i === 0 ? ' <span class="chip ok">current</span>' : ''}</td>`
        + `<td class="l">${i === dev.versions.length - 1 ? '<span class="sub">first pull</span>' : `<span style="color:#7fe08f">+${v.plus}</span> <span style="color:#ff9b9b">−${v.minus}</span>`}</td>`
        + `<td class="l">${v.label ? `<span class="chip warn">${esc(v.label)}</span>` : ''}</td><td class="l">${esc(v.by || '')}</td>`
        + `<td><span class="keys admin-only"><button class="key" data-view="${v.file}">View</button><a class="key" style="text-decoration:none" href="/api/x/configs/download?dev=${encodeURIComponent(dev.key)}&f=${v.file}">Download</a><button class="key" data-label="${v.file}">Label</button></span></td></tr>`).join(''));
      $('#cf-diff').disabled = ticked.size !== 2;
    }
    if (!$('#f-cfg')._filled) { $('#cf-every').value = d.every; $('#cf-onchg').checked = d.on_change; $('#f-cfg')._filled = true; }
    // the saved login: shared, or one switch's own
    const fors = '<option value="">All switches (shared)</option>' + d.devices.filter(x => x.host).map(x => `<option value="${esc(x.host)}">${esc(x.name)} · ${esc(x.host)}${(L.per || {})[x.host] ? ' (own login)' : ''}</option>`).join('');
    const fv = $('#cf-for').value; setHTML('#cf-for', fors); $('#cf-for').value = fv || '';
    const per = $('#cf-for').value ? (L.per || {})[$('#cf-for').value] : null;
    if ($('#cf-user')._for !== $('#cf-for').value && document.activeElement !== $('#cf-user')) { $('#cf-user').value = per ? per.user : $('#cf-for').value ? '' : L.user; $('#cf-user')._for = $('#cf-for').value; }
    $('#cf-pw').placeholder = (per ? per.password_set : L.password_set) ? '•••••••• (saved: type to change)' : $('#cf-for').value && !per ? 'uses the shared login' : 'not saved';
    $('#cf-en').placeholder = L.enable_set ? '•••••••• (saved: type to change)' : 'not saved';
    $('#cf-forget').hidden = !per;
    fillLogin('pl', L, '');
  }
  const showText = (html) => { const o = $('#cf-out'); o.hidden = false; o.innerHTML = html; o.scrollIntoView({ block: 'nearest' }); };
  $('#cf-list').addEventListener('change', async e => {
    const s = e.target.closest('[data-via]');
    if (s) { await api('/api/x/configs/via', { dev: s.dataset.via, via: s.value }).catch(err => alert(err.message)); refresh(); }
  });
  $('#cf-list').addEventListener('click', async e => {
    const o = e.target.closest('[data-open]');
    if (o) { open = open === o.dataset.open ? null : o.dataset.open; ticked.clear(); $('#cf-out').hidden = true; draw(); return; }
    const p = e.target.closest('[data-pull]');
    if (p) {
      p.disabled = true; p.textContent = 'Pulling…';
      try { const r = await api('/api/x/configs/pull', { dev: p.dataset.pull, ...loginBody('pl') }); const x = r.result; if (!x.ok) alert(x.error); $('#pl-pw').value = ''; $('#pl-en').value = ''; }
      catch (err) { alert(err.message); }
      refresh();
    }
  });
  $('#cf-hist').addEventListener('change', e => {
    const t = e.target.closest('[data-tick]');
    if (!t) return;
    if (t.checked) { ticked.add(t.dataset.tick); if (ticked.size > 2) ticked.delete([...ticked][0]); } else ticked.delete(t.dataset.tick);
    draw();
  });
  $('#cf-hist').addEventListener('click', async e => {
    const v = e.target.closest('[data-view]'), lb = e.target.closest('[data-label]');
    if (v) {
      const r = await fetch(`/api/x/configs/view?dev=${encodeURIComponent(open)}&f=${v.dataset.view}`); const j = await r.json();
      showText(r.ok ? esc(j.text) : esc(j.error));
    }
    if (lb) {
      const label = prompt('Label for this version (empty to clear), e.g. Before the event:');
      if (label !== null) { await api('/api/x/configs/label', { dev: open, f: lb.dataset.label, label }).catch(err => alert(err.message)); refresh(); }
    }
  });
  $('#cf-diff').addEventListener('click', async () => {
    const [a, b] = [...ticked].sort();
    const r = await fetch(`/api/x/configs/diff?dev=${encodeURIComponent(open)}&a=${a}&b=${b}`); const j = await r.json();
    if (!r.ok) return showText(esc(j.error));
    showText(j.lines.length ? j.lines.map(l => `<span class="${l.startsWith('@@') ? 'h' : l.startsWith('+') ? 'a' : l.startsWith('-') ? 'd' : ''}">${esc(l)}</span>`).join('\n') : 'No differences.');
  });
  $('#f-login').addEventListener('submit', async e => {
    e.preventDefault();
    try { await api('/api/x/cli/save', { host: $('#cf-for').value, user: $('#cf-user').value, password: $('#cf-pw').value, enable: $('#cf-en').value }); $('#cf-pw').value = ''; $('#cf-en').value = ''; $('#cf-user')._for = null; flash($('#cf-lmsg'), 'Saved', true); refresh(); }
    catch (err) { flash($('#cf-lmsg'), err.message); }
  });
  $('#cf-for').addEventListener('change', () => { $('#cf-user')._for = null; if (D) draw(); });
  $('#cf-forget').addEventListener('click', async () => {
    try { await api('/api/x/cli/save', { host: $('#cf-for').value, remove: true }); $('#cf-user')._for = null; flash($('#cf-lmsg'), 'This switch uses the shared login again', true); refresh(); }
    catch (err) { flash($('#cf-lmsg'), err.message); }
  });
  $('#f-pulllogin').addEventListener('submit', e => e.preventDefault());
  $('#f-cfg').addEventListener('submit', async e => {
    e.preventDefault();
    try { await api('/api/x/configs/settings', { every: +$('#cf-every').value, on_change: $('#cf-onchg').checked }); flash($('#cf-smsg'), 'Saved', true); }
    catch (err) { flash($('#cf-smsg'), err.message); }
  });
  $('#cf-all').addEventListener('click', async () => { await api('/api/x/configs/pull_all').catch(err => alert(err.message)); flash($('#cf-smsg'), 'Pulling every switch', true); setTimeout(refresh, 800); });
  PAGES.configs = { show: refresh };
  const tick = () => { if (view === 'configs' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
