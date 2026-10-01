#!/usr/bin/env python3
"""Zeroth cloud sync — the body's outbound link to the Apps Script bridge.

Runs beside body.py (does not modify it). Every few seconds it:
  PUSH  body_state.json, new mind_queue.jsonl / said.jsonl / logs/*/live.jsonl lines
  PULL  queued commands (speak / watch / rest) -> merged into body_control.json

No inbound ports, no server: the body only makes outbound HTTPS calls.

Command delivery is ack-on-effect, not ack-on-write:
  speak  -> acked when body.py logs that exact text in said.jsonl
  watch  -> acked when body_state.json shows ears 'open on <name>'
  rest   -> acked when body_state.json shows ears 'resting'
Until then the command stays 'sent' in the Commands sheet.

body_control.json safety (body.py rewrites that file non-atomically from a
copy it read before TTS ran, so a merge during a speak would be erased):
  1. never merge while a speak is still pending in the file (defer)
  2. never merge over an unreadable file (body mid-write) — skip the cycle
  3. every merge stamps '_cloud_ids' (body.py ignores unknown keys); read back
     after the write and on every later cycle; missing ids => re-merge once +
     control_clobber event; missing again => control_lost event and the command
     is acked as lost.

export ZEROTH_CLOUD_URL='https://script.google.com/macros/s/XXXX/exec'
export ZEROTH_BODY_TOKEN='...'
./cloud_sync.py [--interval 10]
"""
import os, sys, json, glob, time, hashlib, urllib.request

HERE = os.path.expanduser(os.environ.get('ZEROTH_HOME', '~/workspace/zeroth'))
URL = os.environ.get('ZEROTH_CLOUD_URL', '')
TOKEN = os.environ.get('ZEROTH_BODY_TOKEN', '')
CURSOR = os.path.join(HERE, '.cloud_cursor.json')
CTRL = os.path.join(HERE, 'body_control.json')
MARK = '_cloud_ids'
MAX = 200
EXPIRE_S = 3600


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def load_cursor():
    try:
        c = json.load(open(CURSOR))
    except Exception:
        c = {}
    for k, v in (('offsets', {}), ('inflight', {}), ('done', []), ('outbox', [])):
        c.setdefault(k, v)
    return c


def save_cursor(c):
    c['done'] = c['done'][-200:]
    json.dump(c, open(CURSOR + '.tmp', 'w'))
    os.replace(CURSOR + '.tmp', CURSOR)


def emit(cur, kind, detail):
    cur['outbox'].append({
        'id': hashlib.sha1(f'{now()}|{kind}|{detail}|{os.urandom(4).hex()}'.encode()).hexdigest()[:12],
        'at': now(),
        'kind': kind,
        'detail': detail,
    })


def read_new(path, offsets):
    """Complete new JSON lines since the saved offset. Returns (items, new_offset)."""
    if not os.path.exists(path):
        return [], offsets.get(path, 0)
    off = offsets.get(path, 0)
    if off > os.path.getsize(path):
        off = 0
    items = []
    with open(path, 'rb') as f:
        f.seek(off)
        for raw in f:
            if not raw.endswith(b'\n'):
                break
            off += len(raw)
            try:
                items.append(json.loads(raw))
            except Exception:
                pass
            if len(items) >= MAX:
                break
    return items, off


def post(payload):
    payload['token'] = TOKEN
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'text/plain'})
    # Apps Script answers POST with a 302 to the result; urllib follows it.
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# ---------- body_control.json ----------

def read_ctrl():
    """(ctrl, readable). readable=False when the file exists but won't parse (body mid-write)."""
    if not os.path.exists(CTRL):
        return {}, True
    try:
        return json.load(open(CTRL)), True
    except Exception:
        return {}, False


def write_ctrl(ctrl):
    json.dump(ctrl, open(CTRL + '.tmp', 'w'))
    os.replace(CTRL + '.tmp', CTRL)


def apply(cmd, ctrl):
    t, p = cmd['type'], cmd.get('payload') or {}
    if t == 'speak':
        if ctrl.get('speak'):
            return False  # one speak at a time; retry next cycle
        ctrl['speak'] = p['text']
    elif t == 'watch':
        ctrl['watch_url'], ctrl['watch_name'] = p['url'], p.get('name', 'stream')
    elif t == 'rest':
        ctrl['watch_url'], ctrl['watch_name'] = None, 'idle'
    return True


def stamp(ctrl, ids):
    cur = ctrl.get(MARK, [])
    ctrl[MARK] = (cur + [i for i in ids if i not in cur])[-50:]


def merge_write(cmds, cur):
    """Merge cmds into body_control.json, verify by read-back.
    Returns (ids safely merged, ids that already used their one re-merge)."""
    if not cmds:
        return [], []
    ctrl, ok = read_ctrl()
    if not ok or ctrl.get('speak'):
        return [], []  # defer: unreadable, or body still owes a speak
    merged = [c['id'] for c in cmds if apply(c, ctrl)]
    if not merged:
        return [], []
    reused = []
    stamp(ctrl, merged)
    write_ctrl(ctrl)
    back, ok2 = read_ctrl()
    missing = [i for i in merged if not ok2 or i not in back.get(MARK, [])]
    if missing:
        ctrl, ok = read_ctrl()
        if ok:
            redo = [c for c in cmds if c['id'] in missing]
            for c in redo:
                apply(c, ctrl)
            stamp(ctrl, missing)
            write_ctrl(ctrl)
        back, ok2 = read_ctrl()
        still = [i for i in missing if not ok2 or i not in back.get(MARK, [])]
        emit(cur, 'control_clobber',
             f'body_control.json lost {missing} right after write; re-merged once; '
             f'{"recovered" if not still else "STILL MISSING " + str(still)}')
        reused = missing
        merged = [i for i in merged if i not in still]
    return merged, reused


def confirmed(cmd, state, said):
    t, p = cmd['type'], cmd.get('payload') or {}
    if t == 'speak':
        return p.get('text') in said
    if t == 'watch':
        return state.get('ears') == f"open on {p.get('name', 'stream')}"
    if t == 'rest':
        return state.get('ears') == 'resting'
    return True


# ---------- one sync cycle ----------

def cycle(cur, acks):
    offs, new = cur['offsets'], {}
    payload = {'action': 'sync', 'acks': acks}
    state = {}
    try:
        state = json.load(open(os.path.join(HERE, 'body_state.json')))
    except Exception:
        pass
    payload['state'] = {k: ('' if v is None else v) for k, v in state.items()}

    qp = os.path.join(HERE, 'mind_queue.jsonl')
    items, new[qp] = read_new(qp, offs)
    payload['events'] = [{
        'id': hashlib.sha1(f"{i.get('at')}|{i.get('kind')}|{i.get('detail')}".encode()).hexdigest()[:12],
        'at': i.get('at'),
        'kind': i.get('kind'),
        'detail': i.get('detail'),
    } for i in items]
    n_outbox = len(cur['outbox'])
    payload['events'] += cur['outbox'][:n_outbox]

    sp = os.path.join(HERE, 'said.jsonl')
    items, new[sp] = read_new(sp, offs)
    payload['said'] = [{
        'at': i.get('at'),
        'text': i.get('text'),
        'ok': i.get('ok'),
    } for i in items]
    said = {i.get('text'): i.get('ok') for i in items}

    payload['transcripts'] = []
    for p in glob.glob(os.path.join(HERE, 'logs', '*', 'live.jsonl')):
        items, new[p] = read_new(p, offs)
        w = os.path.basename(os.path.dirname(p))
        for i in items:
            a = i.get('audio') or {}
            payload['transcripts'].append({
                'at': i.get('at'),
                'watch': w,
                'transcript': i.get('transcript'),
                'speech_ratio': a.get('speech_ratio'),
                'mean_db': a.get('mean_db'),
                'peak_db': a.get('peak_db'),
                'brightness_hz': a.get('brightness_hz'),
            })

    resp = post(payload)
    if not resp.get('ok'):
        raise RuntimeError(resp.get('error'))
    offs.update(new)  # commit cursors only after a good push
    cur['outbox'] = cur['outbox'][n_outbox:]

    server = resp.get('commands', [])
    out = []  # acks to send with the next sync

    # 1. reconcile commands already merged: confirm effect, detect clobber, expire
    ctrl, readable = read_ctrl()
    remerge = []
    for cid, inf in list(cur['inflight'].items()):
        cmd = inf['cmd']
        if confirmed(cmd, state, said):
            if cmd['type'] == 'speak' and said.get(cmd['payload']['text']) is False:
                emit(cur, 'speak_failed', f"TTS failed for command {cid}: {cmd['payload']['text'][:80]}")
            out.append(cid)
            cur['done'].append(cid)
            del cur['inflight'][cid]
        elif time.time() - inf['since'] > EXPIRE_S:
            emit(cur, 'command_expired', f'{cid} ({cmd["type"]}) never confirmed in {EXPIRE_S}s')
            out.append(cid)
            cur['done'].append(cid)
            del cur['inflight'][cid]
        elif readable and cid not in ctrl.get(MARK, []):
            if inf['remerges'] >= 1:
                emit(cur, 'control_lost', f'{cid} ({cmd["type"]}) clobbered twice; dropped')
                out.append(cid)
                cur['done'].append(cid)
                del cur['inflight'][cid]
            else:
                inf['remerges'] += 1
                emit(cur, 'control_clobber', f'{cid} ({cmd["type"]}) erased from body_control.json; re-merging once')
                remerge.append(cmd)

    # 2. new commands from the server (skip ones in flight; re-ack ones already finished)
    fresh = []
    for c in server:
        if c['id'] in cur['inflight']:
            continue
        if c['id'] in cur['done']:
            out.append(c['id'])
            continue
        fresh.append(c)

    # 3. merge + verify
    merged, reused = merge_write(remerge + fresh, cur)
    for i in merged:
        if i in cur['inflight']:
            if i in reused:
                cur['inflight'][i]['remerges'] = 1
        else:
            cmd = next(c for c in fresh if c['id'] == i)
            cur['inflight'][i] = {'cmd': cmd, 'since': time.time(), 'remerges': 1 if i in reused else 0}

    save_cursor(cur)
    return list(dict.fromkeys(out))


def main():
    if not URL or not TOKEN:
        sys.exit('set ZEROTH_CLOUD_URL and ZEROTH_BODY_TOKEN')
    interval = int(sys.argv[sys.argv.index('--interval') + 1]) if '--interval' in sys.argv else 10
    cur, acks = load_cursor(), []
    while True:
        try:
            acks = cycle(cur, acks)
        except Exception as e:
            print(f'[{time.strftime("%H:%M:%S")}] sync failed: {e}', flush=True)
            time.sleep(min(60, interval * 3))
        time.sleep(interval)


if __name__ == '__main__':
    main()
