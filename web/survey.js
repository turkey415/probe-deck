'use strict';
// Jack survey: plug into every seat, record each jack.
(() => {
  let D = null, busy = false, poll = 3000;
  const LV = { good: 'on', check: 'warn', bad: 'err' };
  const natKey = s => String(s).replace(/\d+/g, n => n.padStart(6, '0'));
  async function refresh() {
    if (busy) return;
    busy = true;
    try { const r = await fetch('/api/x/survey', { cache: 'no-store' }); if (r.ok) { D = await r.json(); draw(); } }
    catch { /* the probe reboots between jacks on PoE */ }
    finally { busy = false; }
  }
  function draw() {
    const d = D, c = d.conf, st = d.state, recs = d.records;
    poll = c.armed ? 1500 : 4000;
    $('#sv-name').textContent = c.title;
    $('#sv-lamp').innerHTML = c.armed ? '<span class="dot live"></span>surveying' : '<span class="dot"></span>paused';
    $('#sv-arm').textContent = c.armed ? 'Pause' : 'Start surveying';
    $('#sv-arm').className = 'btn ' + (c.armed ? 'danger' : 'primary');
    $('#sv-thisl').hidden = c.armed;
    $('#sv-nextv').textContent = c.next;
    $('#sv-wnext').classList.toggle('off', !c.armed);
    $('#sv-step').textContent = d.busy ? 'Recording…' : c.armed ? (st.step || 'Ready: plug into the next jack.') : 'Paused. Start surveying, or record this jack by hand.';
    $('#sv-jack').innerHTML = d.jack.port ? `<span class="dot on"></span>on ${esc(ifShort(d.jack.port))}${d.jack.switch ? ' · ' + esc(d.jack.switch.split('.')[0]) : ''}` : '<span class="dot"></span>no switch port yet';
    const n = lv => recs.filter(r => r.level === lv).length;
    setSeg('#sv-n', recs.length, 3); setSeg('#sv-good', n('good'), 3); setSeg('#sv-chk', n('check'), 3); setSeg('#sv-bad', n('bad'), 3);
    $('#sv-wchk').classList.toggle('off', !n('check')); $('#sv-wbad').classList.toggle('alarm', n('bad') > 0); $('#sv-wbad').classList.toggle('off', !n('bad'));
    const only = $('#sv-onlybad').checked;
    const rows = recs.filter(r => !only || r.level !== 'good').sort((a, b) => natKey(a.label).localeCompare(natKey(b.label)));
    setHTML('#sv-list', rows.map(r => {
      const sp = r.speed_test;
      return `<tr class="lv-${r.level}"><td class="l"><b>${esc(r.label)}</b><div class="sub">${esc(fmtWhen(r.t))}${r.how !== 'auto' ? ' · ' + esc(r.how) : ''}</div></td>`
        + `<td class="l">${r.port ? `<span class="mono">${esc(ifShort(r.port))}</span>` : '—'}<div class="sub">${esc((r.switch || '').split('.')[0])}</div></td>`
        + `<td>${esc(r.vlan ?? '—')}</td><td class="l">${r.speed ? r.speed + ' ' + esc((r.duplex || '').slice(0, 4)) : '—'}</td><td>${r.plug_ip != null ? r.plug_ip + ' s' : '—'}</td>`
        + `<td class="l">${r.gw_ms != null ? r.gw_ms.toFixed(2) + ' ms' : '—'}${r.gw_loss ? ` <span class="bad">${r.gw_loss}%</span>` : ''}</td>`
        + `<td class="l"><span class="dot ${lampClass(r.dns_ok)}"></span> <span class="dot ${lampClass(r.inet_ok)}"></span></td>`
        + `<td class="l">${sp ? (sp.error ? `<span class="bad">${esc(sp.error)}</span>` : `${sp.down ? Math.round(sp.down / 1e6) : '—'} / ${sp.up ? Math.round(sp.up / 1e6) : '—'}`) : '<span class="sub">—</span>'}</td>`
        + `<td class="l why"><span class="dot ${LV[r.level]}"></span> ${r.why.length ? esc(r.why.join(' · ')) : 'good'}</td>`
        + `<td><span class="keys admin-only"><button class="key" data-relabel="${r.id}" data-l="${esc(r.label)}" title="Rename">✎</button><button class="key" data-del="${r.id}" title="Delete">✕</button></span></td></tr>`;
    }).join('') || `<tr><td colspan="10" class="sub">${recs.length ? 'No problems.' : 'No seats yet.'}</td></tr>`);
    // speed test choices: off, the Perf-Deck stations, or what's saved
    const peers = (S && S.peers) || [];
    const opts = [['', 'Off']].concat(peers.map(p => [p.ip, `${p.name} · ${p.ip} (Perf-Deck)`]));
    if (c.speed_to && !opts.some(o => o[0] === c.speed_to)) opts.push([c.speed_to, c.speed_to]);
    const html = opts.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join('');
    if ($('#sv-speed')._h !== html) { const keep = $('#sv-speed')._filled ? $('#sv-speed').value : c.speed_to; $('#sv-speed').innerHTML = html; $('#sv-speed')._h = html; $('#sv-speed').value = keep; }
    if (!$('#f-survey')._filled) { $('#sv-title').value = c.title; $('#sv-next').value = c.next; $('#sv-speed').value = c.speed_to; $('#sv-speed')._filled = true; $('#f-survey')._filled = true; }
    else if (document.activeElement !== $('#sv-next')) $('#sv-next').value = c.next;
  }
  $('#f-survey').addEventListener('submit', async e => {
    e.preventDefault();
    const sp = $('#sv-speed').value, peer = ((S && S.peers) || []).find(p => p.ip === sp);
    try { await api('/api/x/survey/settings', { title: $('#sv-title').value, next: $('#sv-next').value, speed_to: sp, speed_port: peer && peer.port ? peer.port : 5201 }); flash($('#sv-smsg'), 'Saved', true); refresh(); }
    catch (err) { flash($('#sv-smsg'), err.message); }
  });
  $('#sv-arm').addEventListener('click', async () => {
    try { await api('/api/x/survey/arm', { on: !D.conf.armed, this_one: $('#sv-this').checked }); refresh(); } catch (e) { alert(e.message); }
  });
  $('#sv-rec').addEventListener('click', async () => {
    const label = prompt('Seat label for this jack:', D ? D.conf.next : '');
    if (!label) return;
    $('#sv-rec').disabled = true;
    try { await api('/api/x/survey/record', { label }); } catch (e) { alert(e.message); }
    $('#sv-rec').disabled = false; refresh();
  });
  $('#sv-list').addEventListener('click', async e => {
    const d = e.target.closest('[data-del]'), r = e.target.closest('[data-relabel]');
    if (d && confirm('Delete this seat?')) { await api('/api/x/survey/delete', { id: d.dataset.del }).catch(err => alert(err.message)); refresh(); }
    if (r) { const l = prompt('Seat label:', r.dataset.l); if (l) { await api('/api/x/survey/relabel', { id: r.dataset.relabel, label: l }).catch(err => alert(err.message)); refresh(); } }
  });
  $('#sv-onlybad').addEventListener('change', () => D && draw());
  $('#sv-new').addEventListener('click', async () => {
    const title = prompt('Name of the new survey:', 'Hall B');
    if (title === null) return;
    const next = prompt('First seat label:', 'A-01');
    if (next === null) return;
    try { await api('/api/x/survey/new', { title, next }); $('#f-survey')._filled = false; refresh(); } catch (e) { alert(e.message); }
  });
  PAGES.survey = { show: refresh };
  const tick = () => { if (view === 'survey' && ME) refresh(); setTimeout(tick, poll); };
  setTimeout(tick, poll);
})();
