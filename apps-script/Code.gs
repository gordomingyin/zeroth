/**
 * ZEROTH CLOUD — the Google-side mailbox + nervous relay for the Zeroth body.
 *
 * Design rule: NO body logic lives here. The body (ears/mouth/hands/heartbeat)
 * stays in the repo and runs where it runs. This script only:
 *   - receives what the body pushes (heartbeat, events, transcripts, said, audio)
 *   - stores it in a Sheet (+ Drive for audio)
 *   - hands the body the commands the mind queued (speak / watch / rest)
 *   - serves an HTML status page + JSON API (everything has a link)
 *   - watches the heartbeat (trigger) and emails if the body goes silent
 *
 * Setup: paste this file + appsscript.json, run setup() once, then
 * Deploy > New deployment > Web app (Execute as: Me, Access: Anyone).
 */
const CFG = { STALE_S: 360, KEEP_ROWS: 5000, MAX_SPEAK: 1000, MAX_PUSH: 500 };

const SCHEMA = {
  State:       ['key', 'value', 'updated'],
  Events:      ['id', 'at', 'kind', 'detail', 'handled', 'handled_at'],
  Transcripts: ['at', 'watch', 'transcript', 'speech_ratio', 'mean_db', 'peak_db', 'brightness_hz'],
  Said:        ['at', 'text', 'ok'],
  Commands:    ['id', 'at', 'type', 'payload', 'status', 'acked_at'],
  Audio:       ['at', 'name', 'url', 'bytes']
};

const P_ = PropertiesService.getScriptProperties();

/* ───────────── setup (run once from the editor) ───────────── */

function setup() {
  let id = P_.getProperty('SHEET_ID');
  if (!id) { id = SpreadsheetApp.create('Zeroth Cloud').getId(); P_.setProperty('SHEET_ID', id); }
  const ss = SpreadsheetApp.openById(id);
  Object.keys(SCHEMA).forEach(function(n) {
    const sh = ss.getSheetByName(n) || ss.insertSheet(n);
    if (sh.getLastRow() === 0) { sh.appendRow(SCHEMA[n]); sh.setFrozenRows(1); }
    sh.getRange(1, 1, sh.getMaxRows(), SCHEMA[n].length).setNumberFormat('@'); // keep everything as text
  });
  const dflt = ss.getSheetByName('Sheet1');
  if (dflt && ss.getSheets().length > 1) ss.deleteSheet(dflt);

  if (!P_.getProperty('FOLDER_ID')) P_.setProperty('FOLDER_ID', DriveApp.createFolder('Zeroth Audio').getId());
  ['BODY_TOKEN', 'MIND_TOKEN'].forEach(function(k) { if (!P_.getProperty(k)) P_.setProperty(k, newToken_()); });

  ScriptApp.getProjectTriggers().forEach(function(t) {
    const h = t.getHandlerFunction();
    if (h === 'watchdog' || h === 'trim') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('watchdog').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('trim').timeBased().everyDays(1).atHour(4).create();
  showSecrets();
}

/** Run from the editor to print URLs + tokens in the execution log. */
function showSecrets() {
  Logger.log('SHEET:    ' + SpreadsheetApp.openById(P_.getProperty('SHEET_ID')).getUrl());
  Logger.log('AUDIO:    ' + DriveApp.getFolderById(P_.getProperty('FOLDER_ID')).getUrl());
  Logger.log('WEB APP:  ' + (ScriptApp.getService().getUrl() || '(deploy as web app first)'));
  Logger.log('BODY_TOKEN (body only):  ' + P_.getProperty('BODY_TOKEN'));
  Logger.log('MIND_TOKEN (mind/Zeebo): ' + P_.getProperty('MIND_TOKEN'));
}

/** Rotate a token: rotateToken('BODY_TOKEN') or rotateToken('MIND_TOKEN'). */
function rotateToken(which) { P_.setProperty(which, newToken_()); showSecrets(); }

function newToken_() { return (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, ''); }

/* ───────────── web entry points ───────────── */

function doGet(e) {
  const req = e.parameter || {};
  const role = role_(req.token);
  if (req.action === 'ping' || (!req.action && !req.view)) return json_({ ok: true, service: 'zeroth-cloud', auth: role });
  if (!role) return json_({ ok: false, error: 'unauthorized' });
  if (req.view === 'status') return statusPage_(req.token);
  return json_(route_(req, role));
}

function doPost(e) {
  let req = {};
  try { req = JSON.parse((e.postData && e.postData.contents) || '{}'); }
  catch (err) { return json_({ ok: false, error: 'bad json' }); }
  const role = role_(req.token || (e.parameter && e.parameter.token));
  if (!role) return json_({ ok: false, error: 'unauthorized' });
  return json_(route_(req, role));
}

const ROUTES = {
  body: ['sync', 'audio'],
  mind: ['status', 'events', 'handle', 'command', 'transcripts', 'commands', 'said']
};

function route_(req, role) {
  const action = req.action;
  if (ROUTES[role].indexOf(action) < 0) return { ok: false, error: 'action not allowed for this token: ' + action };
  const mutating = ['sync', 'audio', 'handle', 'command'].indexOf(action) >= 0;
  const lock = LockService.getScriptLock();
  if (mutating) lock.waitLock(20000);
  try {
    switch (action) {
      case 'sync':        return sync_(req);
      case 'audio':       return audio_(req);
      case 'status':      return status_();
      case 'events':      return { ok: true, events: listEvents_(req) };
      case 'handle':      return handle_(req);
      case 'command':     return command_(req);
      case 'transcripts': return { ok: true, transcripts: tail_('Transcripts', num_(req.limit, 20)).filter(function(r) { return !req.watch || r.watch === req.watch; }) };
      case 'commands':    return { ok: true, commands: tail_('Commands', num_(req.limit, 20)) };
      case 'said':        return { ok: true, said: tail_('Said', num_(req.limit, 20)) };
    }
    return { ok: false, error: 'unknown action' };
  } catch (err) {
    return { ok: false, error: String(err) };
  } finally {
    if (mutating) lock.releaseLock();
  }
}

/* ───────────── body side: one round trip does push + pull ───────────── */

function sync_(req) {
  if (req.state && typeof req.state === 'object') {
    req.state.heartbeat_received = now_();
    setState_(req.state);
  }
  const seen = {};
  tail_('Events', 1000).forEach(function(r) { seen[r.id] = 1; });
  const evRows = [];
  (req.events || []).slice(0, CFG.MAX_PUSH).forEach(function(ev) {
    const id = String(ev.id || '');
    if (!id || seen[id]) return;
    seen[id] = 1;
    evRows.push([id, ev.at, ev.kind, ev.detail, 'false', '']);
  });
  append_('Events', evRows);

  append_('Transcripts', (req.transcripts || []).slice(0, CFG.MAX_PUSH).map(function(t) {
    return [t.at, t.watch, t.transcript, t.speech_ratio, t.mean_db, t.peak_db, t.brightness_hz];
  }));
  append_('Said', (req.said || []).slice(0, CFG.MAX_PUSH).map(function(s) { return [s.at, s.text, s.ok]; }));

  ackCommands_(req.acks || []);

  const pending = [];
  const s = sh_('Commands'), last = s.getLastRow();
  if (last > 1) {
    const vals = s.getRange(2, 1, last - 1, SCHEMA.Commands.length).getValues();
    vals.forEach(function(r, i) {
      if (r[4] === 'pending' || r[4] === 'sent') {
        pending.push({ id: r[0], type: r[2], payload: safeParse_(r[3]) });
        if (r[4] === 'pending') s.getRange(i + 2, 5).setValue('sent');
      }
    });
  }
  return { ok: true, commands: pending, server_time: now_() };
}

function ackCommands_(ids) {
  if (!ids.length) return;
  const want = {};
  ids.forEach(function(i) { want[i] = 1; });
  const s = sh_('Commands'), last = s.getLastRow();
  if (last < 2) return;
  const vals = s.getRange(2, 1, last - 1, 1).getValues();
  const t = now_();
  vals.forEach(function(r, i) {
    if (want[r[0]]) {
      s.getRange(i + 2, 5).setValue('acked');
      s.getRange(i + 2, 6).setValue(t);
    }
  });
}

/* ───────────── body side: audio upload ───────────── */

function audio_(req) {
  if (!req.b64) return { ok: false, error: 'no audio' };
  const blob = Utilities.newBlob(Utilities.base64Decode(req.b64), req.mime || 'audio/mpeg', req.name || 'audio.mp3');
  const file = DriveApp.getFolderById(P_.getProperty('FOLDER_ID')).createFile(blob);
  const url = file.getUrl();
  append_('Audio', [[now_(), file.getName(), url, String(blob.getBytes().length)]]);
  addEvent_('audio', file.getName() + ' ' + url);
  return { ok: true, url: url, name: file.getName(), bytes: blob.getBytes().length };
}

/* ───────────── mind side: events + commands ───────────── */

function listEvents_(req) {
  const rows = tail_('Events', num_(req.limit, 50));
  const all = String(req.unhandled || '') === 'false';
  return rows.filter(function(r) { return all || r.handled !== 'true'; });
}

function handle_(req) {
  const ids = String(req.ids || '').split(',').map(function(x) { return x.trim(); }).filter(function(x) { return x; });
  if (!ids.length) return { ok: false, error: 'no ids' };
  const want = {};
  ids.forEach(function(i) { want[i] = 1; });
  const s = sh_('Events'), last = s.getLastRow();
  let n = 0;
  if (last > 1) {
    const vals = s.getRange(2, 1, last - 1, 1).getValues();
    const t = now_();
    vals.forEach(function(r, i) {
      if (want[r[0]]) {
        s.getRange(i + 2, 5).setValue('true');
        s.getRange(i + 2, 6).setValue(t);
        n++;
      }
    });
  }
  return { ok: true, handled: n };
}

function command_(req) {
  const type = String(req.type || '');
  let payload;
  if (type === 'speak') {
    const text = String(req.text || '').slice(0, CFG.MAX_SPEAK);
    if (!text) return { ok: false, error: 'speak needs text' };
    payload = { text: text };
  } else if (type === 'watch') {
    if (!req.url) return { ok: false, error: 'watch needs url' };
    payload = { url: String(req.url), name: String(req.name || 'stream') };
  } else if (type !== 'rest') {
    return { ok: false, error: 'type must be speak | watch | rest' };
  } else {
    payload = {};
  }
  const id = Utilities.getUuid().replace(/-/g, '').slice(0, 12);
  append_('Commands', [[id, now_(), type, JSON.stringify(payload), 'pending', '']]);
  return { ok: true, id: id };
}

/* ───────────── mind side: status ───────────── */

function status_() {
  const st = getState_();
  const hb = st.heartbeat_received ? (Date.now() - Date.parse(st.heartbeat_received)) / 1000 : null;
  const cmds = tail_('Commands', 100).filter(function(r) { return r.status === 'pending' || r.status === 'sent'; }).length;
  const evs = tail_('Events', 1000).filter(function(r) { return r.handled !== 'true'; }).length;
  return {
    ok: true,
    alive: hb !== null && hb < CFG.STALE_S,
    heartbeat_age_s: hb === null ? null : Math.round(hb),
    open_events: evs,
    pending_commands: cmds,
    server_time: now_()
  };
}

function statusPage_(token) {
  const s = status_();
  const st = getState_();
  const q = '?token=' + encodeURIComponent(token);
  const rows = Object.keys(st).sort().map(function(k) {
    return '<tr><td>' + esc_(k) + '</td><td>' + esc_(st[k]) + '</td></tr>';
  }).join('');
  const html =
    '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="15">' +
    '<title>Zeroth Cloud</title><style>' +
    'body{background:#0E1217;color:#E6E6E6;font-family:monospace;padding:24px;max-width:900px;margin:0 auto}' +
    'h1{font-size:20px;letter-spacing:2px}' +
    '.alive{color:#2EA043}.silent{color:#E3B341}' +
    'table{border-collapse:collapse;width:100%;margin-top:16px}' +
    'td{border:1px solid #2a3139;padding:6px 10px;font-size:13px;word-break:break-all}' +
    'td:first-child{color:#26CDD4;white-space:nowrap}' +
    'a{color:#26CDD4}' +
    '.links{margin:16px 0;font-size:13px}' +
    '</style></head><body>' +
    '<h1>ZEROTH CLOUD — <span class="' + (s.alive ? 'alive' : 'silent') + '">' +
    (s.alive ? 'ALIVE' : 'SILENT') + '</span></h1>' +
    '<div>heartbeat age: ' + esc_(s.heartbeat_age_s) + 's &nbsp;|&nbsp; open events: ' +
    esc_(s.open_events) + ' &nbsp;|&nbsp; pending commands: ' + esc_(s.pending_commands) + '</div>' +
    '<div class="links">' +
    '<a href="' + q + '&action=status">json status</a> · ' +
    '<a href="' + q + '&action=events">events</a> · ' +
    '<a href="' + q + '&action=transcripts&limit=20">transcripts</a> · ' +
    '<a href="' + q + '&action=commands">commands</a> · ' +
    '<a href="' + q + '&action=said">said</a>' +
    '</div>' +
    '<table>' + rows + '</table>' +
    '</body></html>';
  return HtmlService.createHtmlOutput(html).setTitle('Zeroth Cloud');
}

/* ───────────── triggers ───────────── */

/** Every 5 min: if the heartbeat goes stale, log an event + email the owner once. */
function watchdog() {
  const st = getState_();
  const hb = st.heartbeat_received ? (Date.now() - Date.parse(st.heartbeat_received)) / 1000 : null;
  const stale = hb === null || hb > CFG.STALE_S;
  const alerted = st.watchdog_alerted === 'true';
  if (stale && !alerted) {
    addEvent_('body_silent', 'no heartbeat for ' + Math.round(hb === null ? -1 : hb) + 's');
    try {
      MailApp.sendEmail(Session.getActiveUser().getEmail(),
        'Zeroth body silent',
        'No heartbeat from the Zeroth body for ' + Math.round(hb === null ? -1 : hb) + 's.\n\nLast state: ' +
        JSON.stringify(st).slice(0, 1000));
    } catch (e) { /* mail not authorized; event is still logged */ }
    setState_({ watchdog_alerted: 'true' });
  } else if (!stale && alerted) {
    addEvent_('body_back', 'heartbeat recovered after silence');
    setState_({ watchdog_alerted: 'false' });
  }
}

/** Daily: keep only the newest KEEP_ROWS rows per sheet. */
function trim() {
  const ss = SpreadsheetApp.openById(P_.getProperty('SHEET_ID'));
  Object.keys(SCHEMA).forEach(function(n) {
    const sh = ss.getSheetByName(n);
    if (!sh) return;
    const last = sh.getLastRow();
    if (last - 1 > CFG.KEEP_ROWS) sh.deleteRows(2, last - 1 - CFG.KEEP_ROWS);
  });
}

/* ───────────── utilities ───────────── */

function role_(t) {
  if (!t) return null;
  if (t === P_.getProperty('BODY_TOKEN')) return 'body';
  if (t === P_.getProperty('MIND_TOKEN')) return 'mind';
  return null;
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function now_() {
  return Utilities.formatDate(new Date(), 'America/Phoenix', "yyyy-MM-dd'T'HH:mm:ss");
}

function num_(v, d) {
  v = parseInt(v, 10);
  return isNaN(v) ? d : v;
}

function safeParse_(s) {
  try { return JSON.parse(s); } catch (e) { return null; }
}

function esc_(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function sh_(n) {
  return SpreadsheetApp.openById(P_.getProperty('SHEET_ID')).getSheetByName(n);
}

function append_(n, rows) {
  if (!rows.length) return;
  const s = sh_(n);
  s.getRange(s.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
}

function tail_(n, k) {
  const s = sh_(n), last = s.getLastRow();
  if (last < 2) return [];
  const take = Math.min(k, last - 1);
  const head = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0];
  const vals = s.getRange(last - take + 1, 1, take, head.length).getValues();
  return vals.map(function(r) {
    const o = {};
    head.forEach(function(h, i) { o[String(h)] = r[i]; });
    return o;
  }).reverse();
}

function getState_() {
  const s = sh_('State'), last = s.getLastRow(), o = {};
  if (last > 1) s.getRange(2, 1, last - 1, 2).getValues().forEach(function(r) { o[String(r[0])] = r[1]; });
  return o;
}

function setState_(obj) {
  const t = now_();
  const s = sh_('State'), last = s.getLastRow();
  const idx = {};
  if (last > 1) s.getRange(2, 1, last - 1, 1).getValues().forEach(function(r, i) { idx[String(r[0])] = i + 2; });
  Object.keys(obj).forEach(function(k) {
    // null-fix: never store the literal string "null" (sheets are text-formatted)
    const row = [k, obj[k] === null || obj[k] === undefined ? '' : String(obj[k]), t];
    if (idx[k]) s.getRange(idx[k], 1, 1, 3).setValues([row]);
    else s.appendRow(row);
  });
}

function addEvent_(kind, detail) {
  const id = Utilities.getUuid().replace(/-/g, '').slice(0, 12);
  append_('Events', [[id, now_(), kind, detail, 'false', '']]);
}
