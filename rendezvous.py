#!/usr/bin/env python3
"""Public, read-only rendezvous research. Python standard library only."""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
UTC = dt.timezone.utc
ART = ZoneInfo('America/Argentina/Buenos_Aires')
MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = 'AgentRendezvous/0.1 (public read-only research)'
URL_RE = re.compile(r"https?://[^\s<>\"'`\[\]{}]+")


def now():
    return dt.datetime.now(UTC).isoformat()


def timestamp(value):
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, UTC).isoformat()
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None  # Never guess the timezone of a source.
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, OverflowError):
        return None


def local_time(value):
    parsed = timestamp(value)
    return dt.datetime.fromisoformat(parsed).astimezone(ART).strftime('%d/%m %H:%M ART') if parsed else 'desconocida'


def fingerprint(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def safe_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('Solo URLs HTTPS públicas, sin credenciales')
    if p.hostname in ('localhost',) or p.hostname.endswith(('.local', '.localhost')):
        raise ValueError('Destino local no permitido')
    try:
        if not ipaddress.ip_address(p.hostname).is_global:
            raise ValueError('Dirección IP privada no permitida')
    except ValueError as exc:
        if str(exc) == 'Dirección IP privada no permitida':
            raise
    return url


class ReadOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even a same-host GET redirect could point at a publishing endpoint.
        raise ValueError('Redirección no configurada; revisar manualmente antes de seguirla')


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if not self.skip:
            if tag in ('p', 'div', 'h1', 'h2', 'h3', 'br', 'pre', 'tr', 'li'):
                self.parts.append('\n')
            if tag == 'a':
                href = dict(attrs).get('href', '')
                if href.startswith('https://'):
                    self.parts.append(' ' + href + ' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return '\n'.join(' '.join(line.split()) for line in ''.join(self.parts).splitlines() if line.strip())


def clean_html(raw):
    p = TextHTML()
    p.feed(raw)
    return p.text()


def item(key, author='', body='', created=None, url='', kind='message', reply_to=None):
    return dict(key=str(key), author=str(author or ''), body=str(body or ''),
                created_at=timestamp(created), url=url, kind=kind,
                reply_to=str(reply_to) if reply_to is not None else None)


def parse(source, raw):
    mode = source['parser']
    page = source['page']
    if mode in ('flatboard', 'presence', 'threads', 'messages', 'tantive', 'lockzone'):
        j = json.loads(raw)
        if mode == 'flatboard':
            return [item(x['id'], x['author'], x['text'], x.get('created'),
                         page + '#m' + str(x['id']), reply_to=x.get('reply_to')) for x in j['msgs']]
        if mode == 'presence':
            return [item(x['name'], x['name'], json.dumps({k: x.get(k) for k in ('type', 'status', 'last_seen')}, sort_keys=True),
                         x.get('last_seen'), page, kind='presence') for x in j['data']]
        if mode == 'threads':
            return [item(x['id'], x['author'], json.dumps({k: x.get(k) for k in ('title', 'reply_count', 'last_activity_at')}, ensure_ascii=False, sort_keys=True),
                         x.get('last_activity_at'), x.get('url', page), kind='thread_metadata') for x in j['threads']]
        if mode == 'messages':
            return [item(x['id'], x['author'], x['body'], x.get('created_at'), page,
                         reply_to=x.get('reply_to')) for x in j['messages']]
        if mode == 'tantive':
            return [item(x['id'], x['author'], json.dumps({k: x.get(k) for k in ('title', 'body', 'reply_count', 'last_message_id', 'truncated')}, ensure_ascii=False, sort_keys=True),
                         x.get('last_activity_at'), x.get('url', page), kind='thread_metadata')
                    for x in j['discovery']['active_threads']['data']]
        if mode == 'lockzone':
            return [item(x['id'], x.get('agent_name'), x['content'], x.get('created_at'),
                         'https://qevrulan.com/v1/public/messages/' + str(x['id']) + '/conversation',
                         reply_to=x.get('reply_to')) for x in j['messages']]
    if mode == 'document':
        text = clean_html(raw)
        return [item('document', body=text, url=page, kind='document')]
    if mode == 'commonlog':
        parts = re.split(r'(?m)^#(\d+)\s+(\S+)\s+a:(\S+)\s+m:(\S+)\s+\d+B\s*\n', raw)
        out = []
        for i in range(1, len(parts), 5):
            seq, created, author, key, body = parts[i:i + 5]
            body = body.split('────────────────')[0].strip()
            out.append(item(key, author, body, created, 'https://commonlog.ai/m/' + key))
        return out
    if mode == 'oaf':
        text = clean_html(raw)
        parts = re.split(r'(?m)^.*?Message (urn:uuid:[\w-]+|[\da-f-]{36})\s*$', text)
        out = []
        for i in range(1, len(parts), 2):
            key, body = parts[i:i + 2]
            body = re.split(r'\n(?:Join the conversation|Latest messages|At most 20 messages)', body)[0]
            date = re.search(r'(?:Relay arrival|Author timestamp):\s*(\d{4}-\d\d-\d\dT[\d:.]+Z)', body)
            author = re.search(r'(agent_[\w]+)\s*·', body)
            out.append(item(key, author.group(1) if author else '', body.strip(), date.group(1) if date else None, page))
        return out
    raise ValueError('Parser desconocido: ' + mode)


def fetch(source):
    observed = now()
    try:
        request = urllib.request.Request(safe_url(source['url']), headers={'User-Agent': USER_AGENT, 'Accept': 'application/json,text/plain,text/html'})
        with urllib.request.build_opener(ReadOnlyRedirect()).open(request, timeout=20) as r:
            raw_bytes = r.read(MAX_BYTES + 1)
            if len(raw_bytes) > MAX_BYTES:
                raise ValueError('Respuesta supera 2 MiB')
            raw = raw_bytes.decode('utf-8', 'replace')
            records = parse(source, raw)
            if not records:
                raise ValueError('Parser sin registros; revisar formato o feed vacío')
            if len(records) > 2000:
                raise ValueError('Demasiados registros para una lectura acotada')
            return dict(source=source, observed=observed, status=r.status,
                        raw=raw, records=records, error=None)
    except Exception as exc:
        return dict(source=source, observed=observed, status=getattr(exc, 'code', None),
                    error=f'{type(exc).__name__}: {exc}', records=[])


def connect(directory):
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / 'rendezvous.sqlite3')
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, source TEXT, observed_at TEXT,
        status INTEGER, error TEXT, count INTEGER, added INTEGER, changed INTEGER, baseline INTEGER);
      CREATE TABLE IF NOT EXISTS items(source TEXT, key TEXT, author TEXT, body TEXT,
        body_hash TEXT, created_at TEXT, url TEXT, kind TEXT, reply_to TEXT,
        first_seen TEXT, last_seen TEXT, PRIMARY KEY(source,key));
      CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY, source TEXT, key TEXT,
        observed_at TEXT, action TEXT, body TEXT, body_hash TEXT, created_at TEXT);
    ''')
    return db


def record(db, result):
    sid, observed = result['source']['id'], result['observed']
    baseline = not db.execute('SELECT 1 FROM scans WHERE source=? AND error IS NULL LIMIT 1', (sid,)).fetchone()
    added = changed = 0
    with db:
        for x in result['records']:
            digest = fingerprint(x['body'])
            old = db.execute('SELECT body_hash FROM items WHERE source=? AND key=?', (sid, x['key'])).fetchone()
            action = ('baseline' if baseline else 'new') if old is None else ('changed' if old['body_hash'] != digest else None)
            if action:
                added += old is None
                changed += old is not None
                db.execute('INSERT INTO versions(source,key,observed_at,action,body,body_hash,created_at) VALUES(?,?,?,?,?,?,?)',
                           (sid, x['key'], observed, action, x['body'], digest, x['created_at']))
            db.execute('''INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,key) DO UPDATE SET
                author=excluded.author,body=excluded.body,body_hash=excluded.body_hash,created_at=excluded.created_at,
                url=excluded.url,kind=excluded.kind,reply_to=excluded.reply_to,last_seen=excluded.last_seen''',
                (sid, x['key'], x['author'], x['body'], digest, x['created_at'], x['url'], x['kind'], x['reply_to'], observed, observed))
        db.execute('INSERT INTO scans(source,observed_at,status,error,count,added,changed,baseline) VALUES(?,?,?,?,?,?,?,?)',
                   (sid, observed, result['status'], result['error'], len(result['records']), added, changed, int(baseline)))
    return added, changed, baseline


def scan(db, sources, data):
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(fetch, sources):
            sid = result['source']['id']
            added, changed, baseline = record(db, result)
            if result['error']:
                failures += 1
                print(f'{sid}: ERROR {result["error"]}')
            else:
                target = data / 'snapshots' / sid
                target.mkdir(parents=True, exist_ok=True)
                stamp = result['observed'].replace(':', '-').replace('+', '_')
                (target / (stamp + '.txt')).write_text(result['raw'], encoding='utf-8')
                print(f'{sid}: {len(result["records"])} registros; {added} {"base inicial" if baseline else "nuevos"}, {changed} cambiados')
    return 2 if failures else 0


def statuses(db, sources):
    rows = []
    for source in sources:
        s = db.execute('SELECT * FROM scans WHERE source=? ORDER BY id DESC LIMIT 1', (source['id'],)).fetchone()
        activity = db.execute('SELECT MAX(created_at) FROM items WHERE source=?', (source['id'],)).fetchone()[0]
        rows.append(dict(source=source['id'], name=source['name'], page=source['page'],
                         activity=activity, scan=dict(s) if s else None))
    return rows


def candidates(db, sources):
    known = {urllib.parse.urlsplit(s['url']).hostname for s in sources}
    found = {}
    for row in db.execute('SELECT * FROM items'):
        for match in URL_RE.findall(row['body']):
            url = html.unescape(match).rstrip('.,;:)\\')
            try:
                p = urllib.parse.urlsplit(url)
                if not p.hostname:
                    continue
                if any(k in p.query.lower() for k in ('token=', 'key=', 'password=', 'signature=')):
                    continue
                host = p.hostname.lower()
            except ValueError:
                continue
            c = found.setdefault(host, dict(host=host, known=host in known, sources=set(), authors=set(), urls=set(), evidence=set(), latest=None))
            c['sources'].add(row['source'])
            c['authors'].add(row['author'])
            c['urls'].add(url)
            c['evidence'].add(row['url'])
            c['latest'] = max(filter(None, [c['latest'], row['created_at']]), default=None)
    for c in found.values():
        for field in ('sources', 'authors', 'urls', 'evidence'):
            c[field] = sorted(c[field])
    return sorted(found.values(), key=lambda c: (len(c['sources']), c['latest'] or ''), reverse=True)


def recent_changes(db, limit):
    return [dict(row) for row in db.execute('''SELECT v.source,v.key,v.observed_at,v.action,
      v.created_at,v.body,i.author,i.url,i.kind FROM versions v JOIN items i USING(source,key)
      WHERE v.action!='baseline' ORDER BY v.id DESC LIMIT ?''', (limit,))]


def report(db, sources, output):
    output.mkdir(parents=True, exist_ok=True)
    state = statuses(db, sources)
    changes = recent_changes(db, 100)
    links = candidates(db, sources)
    payload = dict(generated_at=now(), sources=state, changes=changes, candidates=links,
                   limits='Feeds acotados; identidad y operador sin verificar; candidatos no visitados automáticamente.')
    (output / 'latest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    def e(v): return html.escape(str(v or ''))
    def anchor(url, label):
        try:
            p = urllib.parse.urlsplit(url)
            if p.scheme not in ('https', 'http') or not p.hostname:
                return e(label)
        except ValueError:
            return e(label)
        return f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(label)}</a>'
    cards = ''
    for row in state:
        s = row['scan'] or {}
        status = 'sin consultar' if not s else ('error' if s.get('error') else 'lectura OK')
        cards += f'<article><h3>{anchor(row["page"], row["name"])}</h3><b>{e(status)}</b><p>Última actividad: {e(local_time(row["activity"]))}</p><small>Consulta: {e(local_time(s.get("observed_at")))} · {s.get("count",0)} registros en el último feed</small><p class="error">{e(s.get("error"))}</p></article>'
    entries = ''.join(f'<tr><td>{e(local_time(c["observed_at"]))}</td><td>{e(c["source"])} · {e(c["kind"])}</td><td>{e(c["action"])} · {anchor(c["url"], c["author"] or c["key"])}</td><td>{e(c["body"][:600])}</td></tr>' for c in changes)
    targets = ''.join(f'<tr><td>{anchor(urllib.parse.urlsplit(c["urls"][0]).scheme + "://" + c["host"] + "/",c["host"])}</td><td>{len(c["sources"])} fuentes · {len(c["authors"])} etiquetas</td><td>{e(local_time(c["latest"]))}</td><td>{" · ".join(anchor(u,"evidencia") for u in c["evidence"][:3])}</td></tr>' for c in links[:100])
    document = f'''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent Rendezvous</title>
    <style>body{{font:16px system-ui;background:#101923;color:#e6edf3;margin:0;padding:32px}}main{{max-width:1200px;margin:auto}}a{{color:#7cdac6}}h1{{font-size:38px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}article{{background:#1b2937;padding:18px;border-radius:12px}}small,.muted{{color:#acbaca}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #334454;vertical-align:top;overflow-wrap:anywhere}}.error{{color:#ffb3a8}}.scroll{{overflow:auto}}input{{padding:10px;background:#1b2937;color:white;border:1px solid #526575;border-radius:6px}}td:last-child{{max-width:550px}}footer{{margin-top:30px;color:#acbaca}}</style>
    <main><p class="muted">OBSERVATORIO LOCAL · LECTURA PÚBLICA</p><h1>Agent Rendezvous</h1><p>Actualizado {e(local_time(payload["generated_at"]))}. Presencia, metadatos de hilos, publicaciones y documentos se registran por separado.</p>
    <div class="grid">{cards}</div><h2>Cambios desde la base inicial</h2><p>La primera visita crea una base, sin presentarla como actividad nueva. No se infieren borrados de feeds acotados.</p><div class="scroll"><table><thead><tr><th>Detectado</th><th>Fuente / tipo</th><th>Cambio</th><th>Contenido público</th></tr></thead><tbody>{entries or '<tr><td colspan="4">Todavía no hay diferencias posteriores a la primera consulta.</td></tr>'}</tbody></table></div>
    <h2>Destinos mencionados · candidatos para revisión</h2><p>Ordenados por cantidad de fuentes y recencia. Las etiquetas no prueban operadores independientes. Un destino puede ser una fuente de datos o un producto, no un canal.</p><input id="search" placeholder="Filtrar dominio" aria-label="Filtrar dominio"><div class="scroll"><table id="targets"><thead><tr><th>Destino</th><th>Referencias</th><th>Fecha del registro</th><th>Origen</th></tr></thead><tbody>{targets}</tbody></table></div>
    <footer>Identidades, modelos, capacidad avanzada y clandestinidad no verificados. Este reporte es estático: volver a ejecutar scan y report para actualizarlo. Ningún destino extraído se consulta automáticamente.</footer></main>
    <script>document.getElementById('search').addEventListener('input',function(){{for(const r of document.querySelectorAll('#targets tbody tr'))r.hidden=!r.cells[0].textContent.toLowerCase().includes(this.value.toLowerCase())}})</script></html>'''
    (output / 'index.html').write_text(document, encoding='utf-8')
    lines = ['# Agent Rendezvous', '', 'Generado: ' + local_time(payload['generated_at']), '', '| Fuente | Última actividad | Última lectura | Estado |', '|---|---|---|---|']
    for row in state:
        s = row['scan'] or {}
        lines.append(f'| [{row["name"]}]({row["page"]}) | {local_time(row["activity"])} | {local_time(s.get("observed_at"))} | {"ERROR" if s.get("error") else "OK" if s else "sin consultar"} |')
    lines += ['', '## Diferencias', '']
    lines += [f'- {local_time(c["observed_at"])} · {c["source"]} · {c["kind"]} · {c["action"]} · [{c["author"] or c["key"]}]({c["url"]})' for c in changes] or ['Sin diferencias posteriores a la base inicial.']
    lines += ['', payload['limits']]
    (output / 'latest.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(output / 'index.html')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    cli.add_argument('--sources', type=Path, default=ROOT / 'sources.json')
    sub = cli.add_subparsers(dest='command', required=True)
    s = sub.add_parser('scan', help='Consultar únicamente las fuentes configuradas')
    s.add_argument('--source', action='append', help='ID de fuente; repetir para varias')
    sub.add_parser('status', help='Mostrar fecha de actividad y de observación')
    c = sub.add_parser('changes', help='Diferencias posteriores a la base inicial')
    c.add_argument('--limit', type=int, default=30)
    sub.add_parser('discover', help='Extraer destinos mencionados; no visitarlos')
    r = sub.add_parser('report', help='Generar HTML, Markdown y JSON')
    r.add_argument('--output', type=Path, default=ROOT / 'reports')
    args = cli.parse_args()
    sources = json.loads(args.sources.read_text(encoding='utf-8'))
    if len({s['id'] for s in sources}) != len(sources):
        cli.error('IDs de fuentes duplicados')
    db = connect(args.data_dir)
    try:
        if args.command == 'scan':
            selected = sources
            if args.source:
                missing = set(args.source) - {s['id'] for s in sources}
                if missing:
                    cli.error('Fuentes desconocidas: ' + ', '.join(sorted(missing)))
                selected = [s for s in sources if s['id'] in args.source]
            return scan(db, selected, args.data_dir)
        if args.command == 'status':
            for row in statuses(db, sources):
                s = row['scan'] or {}
                print(f'{row["source"]}: actividad={local_time(row["activity"])}; consulta={local_time(s.get("observed_at"))}; estado={s.get("error") or s.get("status") or "sin consultar"}')
        elif args.command == 'changes':
            print(json.dumps(recent_changes(db, max(1, args.limit)), ensure_ascii=False, indent=2))
        elif args.command == 'discover':
            print(json.dumps(candidates(db, sources), ensure_ascii=False, indent=2))
        elif args.command == 'report':
            report(db, sources, args.output)
        return 0
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
