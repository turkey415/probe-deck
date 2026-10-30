'use strict';
// Switch intake: watch a boot on the console, the health report, then preload a config.
(() => {
  let D = null, busy = false, poll = 4000, prepT = null, lastPrep = null;
  const LV = { ok: 'on', warn: 'warn', bad: 'err', info: '' };
  const ORDER = { bad: 0, warn: 1, info: 2, ok: 3 };
  const STAGE_C = ['--s1', '--s2', '--s3', '--s4'];
  const fmtKB = kb => kb >= 1048576 ? (kb / 1048576).toFixed(1) + ' GB' : Math.round(kb / 1024) + ' MB';

  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/intake', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    catch { /* the probe may be busy */ }
    finally { busy = false; }
  }
  const login = () => ({ user: $('#ik-user').value.trim(), password: $('#ik-pw').value, enable: $('#ik-en').value });
  const err = (m) => { $('#ik-err').textContent = m; $('#ik-err').hidden = !m; };

  function draw() {
    const d = D, r = d.run, j = d.job, f = (r && r.facts) || {}, h = (r && r.health) || {};
    poll = d.armed && r && !r.ready || d.busy || (j && j.running) ? 1000 : 4000;
    // console + control
    const c = d.console || {};
    $('#ik-con').innerHTML = c.open ? `<span class="dot on"></span> Console cable on ${esc((c.port || '').replace('/dev/', ''))} at ${esc(c.baud)} baud`
      : `<span class="dot err"></span> ${esc(c.error && c.error !== 'no cable' ? c.error : 'No console cable: plug one into the probe')}`;
    const watching = d.armed && r && !r.ready;
    $('#ik-arm').hidden = watching; $('#ik-stop').hidden = !watching;
    $('#ik-arm').disabled = !!d.busy || !c.open;
    $('#ik-lamp').innerHTML = !r ? '<span class="dot"></span>idle'
      : watching ? (r.phase === 'waiting' ? '<span class="dot live"></span>waiting for a boot' : `<span class="dot live"></span>booting · ${esc(r.secs)} s`)
      : d.busy ? `<span class="dot live"></span>${esc(d.busy)}…` : `<span class="dot ${LV[r.level] || ''}"></span>${esc(r.ready ? ({ ok: 'up · passed', warn: 'up · check it', bad: 'up · failed' }[r.level] || 'up') : 'stopped')}`;
    $('#ik-hint').textContent = !r ? (d.conf && d.conf.auto_arm ? 'Watching starts by itself when a boot shows on the console, or press the button and power the switch on.' : 'Press the button, then power the switch on.')
      : watching && r.phase === 'waiting' ? 'Power the switch on now (or reload it). Don’t type in the console until it’s up.'
      : watching ? 'Reading the boot. Hands off the console.' : (r.phase === 'checks' || d.busy === 'checks') ? 'Running health checks over the console…' : '';
    const needLogin = r && r.ready === 'login';
    $('#ik-loginc').classList.toggle('hl', !!needLogin);
    for (const b of ['#ik-checks', '#ik-erase', '#ik-reload']) $(b).disabled = !r || !r.ready || !!d.busy || !!(j && j.running);
    // settings (once)
    if (d.conf && !$('#f-intake')._filled) {
      $('#f-intake')._filled = true;
      $('#ik-auto').checked = d.conf.auto_arm; $('#ik-autochk').checked = d.conf.auto_checks; $('#ik-baud').checked = d.conf.auto_baud;
      $('#ik-rec').value = d.conf.recommended || ''; $('#ik-pid').value = d.conf.psirt_id || '';
      $('#ik-psec').placeholder = d.conf.psirt_secret_set ? 'saved (type to change)' : 'not set';
    }
    // verdict + wells
    const lv = r ? r.level : '';
    $('#ik-verdict').innerHTML = !r ? '<span class="dot"></span>no switch yet' : `<span class="dot ${LV[lv] || (watching ? 'live' : '')}"></span>${esc(r.ready_text || (watching ? 'booting' : ''))}`;
    const word = !r ? '—' : watching ? 'BOOT' : { ok: 'PASS', warn: 'CHECK', bad: 'FAIL' }[lv] || '—';
    const well = (k, v, sub, cls = '', big = 22) => `<div class="well ${cls}"><span class="k">${k}</span><div class="big-port" style="font-size:${big}px" title="${esc(v)}">${esc(v || '—')}</div><span class="un">${esc(sub || '')}</span></div>`;
    setHTML('#ik-wells', [
      well('Result', word, r ? `${r.n_bad || 0} fail · ${r.n_warn || 0} warn` : '', !r ? 'off' : lv === 'bad' ? 'alarm' : '', 28),
      well('Model', f.model || h.model, f.cpu ? `rev ${f.version_id || '?'} · ${f.cpu}` : '', f.model ? '' : 'off', 18),
      well('Serial', f.serial || h.serial, f.base_mac ? 'MAC ' + f.base_mac : '', f.serial ? '' : 'off', 18),
      well('IOS', f.ios || h.ios, f.bootloader ? 'boot ' + f.bootloader : '', f.ios ? '' : 'off', 18),
      well('Memory', f.ram_kb ? fmtKB(f.ram_kb) : '', h.flash ? `flash ${Math.round(h.flash.total / 1048576)} MB` : '', f.ram_kb ? '' : 'off'),
    ].join(''));
    // timeline: one bar segment per boot stage, as long as it took
    const st = (r && r.stages) || {}, names = (r && r.stage_names) || [];
    const marks = names.map(([k, n]) => [k, n, st[k]]).filter(x => x[2] != null);
    const end = r ? (st.ready != null ? st.ready : r.secs) : 0;
    const segs = [];
    for (let i = 0; i < marks.length; i++) {
      const t0 = marks[i][2], t1 = i + 1 < marks.length ? marks[i + 1][2] : (marks[i][0] === 'ready' ? null : end);
      if (t1 == null) continue;
      segs.push({ name: marks[i][1], k: marks[i][0], secs: Math.max(0, t1 - t0) });
    }
    const tot = segs.reduce((a, s) => a + s.secs, 0) || 1;
    const badArea = r ? new Set(r.findings.filter(x => x.lv === 'bad').map(x => x.area)) : new Set();
    const segBad = s => (s.k === 'post' && badArea.has('Self test')) || (s.k === 'load' && badArea.has('Image'));
    setHTML('#ik-tl', segs.map((s, i) => `<i title="${esc(s.name)}: ${s.secs.toFixed(1)} s" style="width:${(s.secs / tot * 100).toFixed(2)}%;background:${segBad(s) ? 'var(--crit)' : css(STAGE_C[i % 4])}"></i>`).join(''));
    setHTML('#ik-tlkey', segs.length ? segs.map((s, i) => `<span><i class="sw" style="background:${segBad(s) ? 'var(--crit)' : css(STAGE_C[i % 4])}"></i>${esc(s.name)} ${s.secs.toFixed(1)} s</span>`).join('')
      + `<span style="margin-left:auto">${st.ready != null ? 'ready after ' + st.ready.toFixed(1) + ' s' : r && r.rx ? r.secs + ' s so far' : ''}</span>` : '<span class="sub">The boot timeline shows here.</span>');
    $('#ik-ready').textContent = r && r.ready ? r.ready_text : '';
    // findings
    const fs = r ? [...r.findings].sort((a, b) => ORDER[a.lv] - ORDER[b.lv]) : [];
    setHTML('#ik-finds', fs.map(x => `<div class="ev" title="${esc(x.line)}"><span class="k">${esc(x.area)}</span><span class="dot ${LV[x.lv]}"></span><span>${esc(x.text)}</span></div>`).join('')
      || `<div class="empty">${watching ? 'Nothing yet.' : 'Findings show here after a boot.'}</div>`);
    $('#ik-fcount').textContent = fs.length ? `${fs.filter(x => x.lv === 'bad').length} fail · ${fs.filter(x => x.lv === 'warn').length} warn · ${fs.filter(x => x.lv === 'ok').length} ok` : '';
    setHTML('#ik-notes', r && r.notes.length ? `<ul class="steps" style="margin-top:12px">${r.notes.map(n => `<li class="sub">${esc(n)}</li>`).join('')}</ul>` : '');
    // POST
    setHTML('#ik-post', r && r.post.length ? r.post.map(p => `<tr><td class="l">${esc(p.name)}</td><td><span class="chip ${/^pass/i.test(p.status) ? 'ok' : 'bad'}">${esc(p.status)}</span></td></tr>`).join('')
      : '<tr><td class="sub">No self-test lines yet.</td></tr>');
    // hardware
    const kv = (k, v, color) => `<div><dt>${esc(k)}</dt><dd${color ? ` style="color:${color}"` : ''} title="${esc(v)}">${esc(v ?? '—')}</dd></div>`;
    const env = h.env || [];
    setHTML('#ik-hw', [
      kv('Temperature', h.temp_c != null ? h.temp_c + ' °C' : null, h.temp_c >= 60 ? 'var(--warn)' : null),
      kv('Fans / power', env.length ? (env.some(e => e.lv === 'bad') ? 'FAULT' : env.some(e => e.lv === 'warn') ? 'warning' : 'OK') : null, env.some(e => e.lv === 'bad') ? 'var(--crit)' : null),
      kv('Last reset', f.last_reset), kv('Uptime', h.uptime), kv('Config register', h.confreg, h.confreg && !/^0x2102$/i.test(h.confreg) ? 'var(--warn)' : null),
      kv('Saved config', h.startup_config == null ? null : h.startup_config ? 'yes (old owner)' : 'none (blank)', h.startup_config ? 'var(--warn)' : null),
      kv('Password recovery', f.pw_recovery), kv('HW revision', f.hw_rev),
    ].join(''));
    const bar = (label, used, total, unit, warnAt) => {
      const pct = total ? Math.min(100, used / total * 100) : 0;
      return `<div class="sub" style="display:flex;justify-content:space-between"><span>${esc(label)}</span><span>${esc(unit)}</span></div>`
        + `<div class="st-bar" style="margin:3px 0 10px"><i style="width:${pct.toFixed(1)}%;background:${pct >= warnAt ? 'var(--warn)' : 'var(--accent)'}"></i></div>`;
    };
    setHTML('#ik-bars', (h.poe ? bar('PoE budget used', h.poe.used, h.poe.avail, `${h.poe.used} of ${h.poe.avail} W`, 85) : '')
      + (h.flash ? bar('Flash used', h.flash.total - h.flash.free, h.flash.total, `${Math.round(h.flash.free / 1048576)} MB free of ${Math.round(h.flash.total / 1048576)} MB`, 95) : '')
      + (h.inventory && h.inventory.length ? `<div class="tablewrap"><table><tbody>${h.inventory.map(i => `<tr><td class="l">${esc(i.pid)}</td><td class="l sub">${esc(i.descr)}</td><td class="mono">${esc(i.sn)}</td></tr>`).join('')}</tbody></table></div>` : ''));
    // IOS
    const io = r && r.iosinfo;
    $('#ik-ioslamp').innerHTML = io ? `<span class="dot ${LV[io.lv] || ''}"></span>${esc(io.version)}` : '';
    setHTML('#ik-ios', !io ? '<p class="sub" style="margin:0">The version shows once the IOS banner prints.</p>'
      : `<p style="margin:0 0 8px">${esc(io.advice || '')}</p>`
      + (f.image ? `<p class="sub mono" style="margin:0 0 8px">${esc(f.image)}</p>` : '')
      + (io.psirt_error ? `<div class="note crit">Cisco advisories: ${esc(io.psirt_error)}</div>` : '')
      + (io.advisories ? (io.advisories.length ? `<div class="tablewrap"><table><thead><tr><th class="l">Severity</th><th class="l">Advisory</th><th class="l">First fixed</th></tr></thead><tbody>`
        + io.advisories.slice(0, 25).map(a => `<tr><td class="l"><span class="chip ${/Critical|High/.test(a.sir) ? 'bad' : a.sir === 'Medium' ? 'warn' : ''}">${esc(a.sir)}</span></td>`
          + `<td class="l wrap"><a href="${esc(a.url)}" target="_blank" rel="noopener" style="color:#8fb8ff">${esc(a.title)}</a></td><td class="l mono">${esc((a.fixed || []).join(', '))}</td></tr>`).join('')
        + `</tbody></table></div>${io.advisories.length > 25 ? `<p class="sub">…and ${io.advisories.length - 25} more.</p>` : ''}` : '<p class="ok-msg" style="margin:0">Cisco lists no advisories for this version.</p>')
        : '<p class="sub" style="margin:0">Add a Cisco openVuln API key in Settings to list security advisories for this version.</p>'));
    // preload job
    drawJob(d);
    // raw log
    $('#ik-tail').textContent = r && r.tail ? r.tail : 'No boot seen yet.';
    const pre = $('#ik-tail'); if (watching) pre.scrollTop = pre.scrollHeight;
    $('#ik-dl').hidden = !(r && r.rx && !watching);
    if (r) $('#ik-dl').href = `/api/x/intake/log/${r.id}`;
    // history
    setHTML('#ik-hist', d.history.map(x => `<tr><td>${esc(fmtWhen(x.t))}</td><td class="l">${esc(x.model || '?')}</td><td class="l mono">${esc(x.serial || '?')}</td><td class="l mono">${esc(x.ios || '')}</td>`
      + `<td class="l"><span class="dot ${LV[x.level] || ''}"></span> ${esc({ ok: 'pass', warn: 'check', bad: 'fail' }[x.level] || '')}${x.n_bad || x.n_warn ? ` (${[x.n_bad && x.n_bad + ' fail', x.n_warn && x.n_warn + ' warn'].filter(Boolean).join(', ')})` : ''}</td>`
      + `<td class="l"><a class="btn sm" href="/api/x/intake/text/${x.id}">Report</a> <a class="btn sm" href="/api/x/intake/log/${x.id}">Boot log</a>`
      + `${isAdmin() ? ` <button class="btn ghost sm" data-forget="${x.id}">Forget</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="6" class="sub">No switches yet.</td></tr>');
    $('#ik-open').disabled = !(r && r.configurable) || !!d.busy || !!(j && j.running);
    maybePop(d);
  }

  function drawJob(d) {
    const j = d.job;
    if (!j) { $('#ik-plamp').innerHTML = ''; setHTML('#ik-job', '<p class="sub" style="margin:0">When the switch is up, pick a template, load a file or paste a config.</p>'); return; }
    const pct = j.total ? Math.round(j.done / j.total * 100) : 0;
    $('#ik-plamp').innerHTML = j.running ? `<span class="dot live"></span>${esc(j.step)}` : j.saved ? '<span class="dot on"></span>saved to the switch'
      : j.error ? '<span class="dot err"></span>stopped' : j.diff != null ? `<span class="dot ${j.errors.length ? 'warn' : 'on'}"></span>review, not saved yet` : `<span class="dot"></span>${esc(j.step)}`;
    let h = '';
    if (j.total) h += `<div class="sub" style="display:flex;justify-content:space-between"><span>${esc(j.step)}</span><span>${j.done} / ${j.total} lines</span></div><div class="st-bar" style="margin:3px 0 12px"><i style="width:${pct}%;background:var(--accent)"></i></div>`;
    if (j.error) h += `<div class="note crit">${esc(j.error)}</div>`;
    if (j.errors && j.errors.length) h += `<div class="note crit" style="margin-bottom:10px"><b>${j.errors.length} line${j.errors.length > 1 ? 's' : ''} the switch didn't take:</b>`
      + `<div class="tablewrap"><table><tbody>${j.errors.map(e => `<tr><td class="mono">${e.n}</td><td class="l mono">${esc(e.line)}</td><td class="l bad">${esc(e.said)}</td></tr>`).join('')}</tbody></table></div></div>`;
    else if (j.diff != null) h += '<p class="ok-msg" style="margin:0 0 10px">Every line was accepted.</p>';
    if (j.diff != null && !j.running) {
      h += `<p class="sub" style="margin:0 0 6px">${j.changed || 0} lines changed in the running config. Check them, then save.</p>`;
      h += `<pre class="cfg diff" style="max-height:340px;overflow:auto">${esc(j.diff || '(no change)').split('\n').map(l => l.startsWith('+') && !l.startsWith('+++') ? `<span class="a">${l}</span>` : l.startsWith('-') && !l.startsWith('---') ? `<span class="d">${l}</span>` : l.startsWith("@@") ? `<span class="h">${l}</span>` : l).join('\n')}</pre>`;
      if (!j.saved) h += `<div class="keys" style="margin-top:10px"><button class="btn primary" id="ik-wr">Save to the switch (write memory)</button><button class="btn" id="ik-again">Preload again…</button></div>`;
    }
    if (j.running && j.step && j.step.startsWith('line')) h += '<div class="keys"><button class="btn" id="ik-cancel">Stop sending</button></div>';
    if (j.reloading) h += '<p class="sub">The switch is reloading. The next boot is being watched.</p>';
    setHTML('#ik-job', h);
    const wr = $('#ik-wr'); if (wr) wr.onclick = async () => { try { await api('/api/x/intake/save', login()); refresh(); } catch (e) { alert(e.message); } };
    const ag = $('#ik-again'); if (ag) ag.onclick = () => openPre();
    const cn = $('#ik-cancel'); if (cn) cn.onclick = () => api('/api/x/intake/cancel').then(refresh);
  }

  // ---------- the preload window ----------
  function maybePop(d) {
    const r = d.run;
    if (!isAdmin() || !r || !r.configurable || r.ready === 'login' || r.phase !== 'ready' || d.busy || d.job || $('#d-preload').open) return;
    if (lsGet('pd-intake-popped', '') === r.id) return;
    lsSet('pd-intake-popped', r.id);
    openPre();
  }
  async function openPre() {
    const d = D;
    if (!d) return;
    const r = d.run || {}, f = r.facts || {};
    $('#pl-who').textContent = [f.model, f.serial, f.ios && 'IOS ' + f.ios, r.ready_text].filter(Boolean).join(' · ');
    setHTML('#pl-tpl', '<option value="">— pick a template —</option>' + d.templates.map(t => `<option value="${esc(t.name)}">${esc(t.name)} · ${t.lines} lines</option>`).join(''));
    const src = d.templates.length ? 'tpl' : 'paste';
    document.querySelector(`input[name=pl-src][value=${src}]`).checked = true;
    srcShow();
    $('#pl-msg').textContent = ''; $('#pl-check').innerHTML = '';
    if (!$('#d-preload').open) $('#d-preload').showModal();
    if ($('#pl-text').value.trim()) prepare();
  }
  function srcShow() {
    const v = document.querySelector('input[name=pl-src]:checked').value;
    $('#pl-tplrow').hidden = v !== 'tpl'; $('#pl-filerow').hidden = v !== 'file';
    if (v === 'paste') $('#pl-text').focus();
  }
  for (const i of document.querySelectorAll('input[name=pl-src]')) i.addEventListener('change', srcShow);
  $('#pl-tpl').addEventListener('change', async () => {
    const n = $('#pl-tpl').value;
    if (!n) return;
    try { const t = await api('/api/x/intake/template_get', { name: n }); $('#pl-text').value = t.text; $('#pl-save').value = ''; prepare(); }
    catch (e) { $('#pl-msg').textContent = e.message; }
  });
  $('#pl-tdel').addEventListener('click', async () => {
    const n = $('#pl-tpl').value;
    if (!n || !confirm(`Delete the template “${n}”?`)) return;
    await api('/api/x/intake/template_delete', { name: n }).catch(() => {});
    await refresh(); openPre();
  });
  $('#pl-file').addEventListener('change', () => {
    const file = $('#pl-file').files[0];
    if (!file) return;
    if (file.size > 65536) { $('#pl-msg').textContent = 'That file is over 64 KB.'; return; }
    file.text().then(t => { $('#pl-text').value = t; $('#pl-save').value = file.name.replace(/\.[^.]+$/, '').slice(0, 60); prepare(); });
  });
  $('#pl-text').addEventListener('input', () => { clearTimeout(prepT); prepT = setTimeout(prepare, 600); });
  const varVals = () => Object.fromEntries([...document.querySelectorAll('#pl-vars input')].map(i => [i.dataset.v, i.value]));
  async function prepare() {
    const text = $('#pl-text').value;
    if (!text.trim()) { $('#pl-check').innerHTML = ''; setHTML('#pl-vars', ''); return; }
    try {
      const p = await api('/api/x/intake/prepare', { text, vars: varVals() });
      lastPrep = p;
      const keep = varVals();
      setHTML('#pl-vars', p.vars.map(v => `<label class="f" style="flex:1 1 160px">{{${esc(v)}}} <input type="text" data-v="${esc(v)}" value="${esc(keep[v] ?? p.values[v] ?? '')}" autocomplete="off" spellcheck="false"></label>`).join(''));
      for (const i of document.querySelectorAll('#pl-vars input')) i.oninput = () => { clearTimeout(prepT); prepT = setTimeout(prepare, 500); };
      const w = p.warnings, rows = w.map(x => `<div class="note${x.lv === 'bad' ? ' crit' : ''}" style="${x.lv === 'info' ? 'border-left-color:var(--muted)' : ''}">${x.n ? `<span class="mono">line ${x.n}</span> · ` : ''}${esc(x.text)}${x.n && p.lines[x.n - 1] ? `<br><span class="mono sub">${esc(p.lines[x.n - 1])}</span>` : ''}</div>`);
      $('#pl-check').innerHTML = `<span class="sub">${p.n} lines to send, about ${p.est_s < 60 ? p.est_s + ' s' : Math.round(p.est_s / 60) + ' min'} at this baud rate.${p.notes.length ? ' ' + p.notes.map(esc).join('. ') + '.' : ''}</span>` + rows.join('');
      $('#pl-msg').textContent = '';
    } catch (e) { $('#pl-msg').textContent = e.message; }
  }
  $('#pl-go').addEventListener('click', async () => {
    const text = $('#pl-text').value;
    if (!text.trim()) { $('#pl-msg').textContent = 'Paste a config, load a file or pick a template.'; return; }
    if (lastPrep && lastPrep.warnings.some(w => w.lv === 'bad') && !confirm('There are problems flagged in red. Send anyway?')) return;
    try {
      await api('/api/x/intake/preload', { text, vars: varVals(), save_as: $('#pl-save').value.trim(), stop_on_error: $('#pl-stop').checked, ...login() });
      $('#d-preload').close(); refresh();
    } catch (e) { $('#pl-msg').textContent = e.message; }
  });

  // ---------- buttons ----------
  $('#ik-arm').addEventListener('click', async () => { err(''); try { await api('/api/x/intake/arm'); refresh(); } catch (e) { err(e.message); } });
  $('#ik-stop').addEventListener('click', async () => { await api('/api/x/intake/stop').catch(() => {}); refresh(); });
  $('#ik-open').addEventListener('click', () => openPre());
  $('#ik-checks').addEventListener('click', async () => { err(''); try { await api('/api/x/intake/checks', login()); refresh(); } catch (e) { err(e.message); } });
  $('#ik-erase').addEventListener('click', async () => {
    if (!confirm('Erase this switch? It runs write erase, deletes vlan.dat and reloads without saving. The old config is gone for good.')) return;
    err(''); try { await api('/api/x/intake/erase', login()); refresh(); } catch (e) { err(e.message); }
  });
  $('#ik-reload').addEventListener('click', async () => {
    if (!confirm('Reload the switch without saving? Changes since the last write memory are dropped.')) return;
    err(''); try { await api('/api/x/intake/reload', login()); refresh(); } catch (e) { err(e.message); }
  });
  $('#ik-hist').addEventListener('click', async e => {
    const id = e.target.dataset && e.target.dataset.forget;
    if (id && confirm('Forget this report?')) { await api('/api/x/intake/forget', { id }).catch(() => {}); refresh(); }
  });
  $('#f-intake').addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('/api/x/intake/settings', { auto_arm: $('#ik-auto').checked, auto_checks: $('#ik-autochk').checked, auto_baud: $('#ik-baud').checked,
        recommended: $('#ik-rec').value, psirt_id: $('#ik-pid').value.trim(), psirt_secret: $('#ik-psec').value });
      $('#ik-psec').value = ''; $('#f-intake')._filled = false; flash($('#ik-smsg'), 'Saved', true); refresh();
    } catch (er) { flash($('#ik-smsg'), er.message); }
  });
  $('#ik-pclear').addEventListener('click', async () => { await api('/api/x/intake/settings', { psirt_clear: true }).catch(() => {}); $('#f-intake')._filled = false; refresh(); });

  PAGES.intake = { show: refresh };
  const tick = () => {
    // on this page: refresh. Anywhere else: open the preload window when a switch is ready (the sidebar status says so)
    const a = S && S.apps && S.apps.intake;
    if (ME && (view === 'intake' || (a && a.pop && isAdmin() && lsGet('pd-intake-popped', '') !== a.pop))) refresh();
    setTimeout(tick, poll);
  };
  setTimeout(tick, poll);
})();
