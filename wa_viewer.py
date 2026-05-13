#!/usr/bin/env python3
"""
WhatsApp Chat Viewer
Genera un HTML interactivo con todos los chats, mensajes y miniaturas.
"""

import sqlite3
import base64
import sys
from datetime import datetime
from pathlib import Path

def _parse_args():
    import argparse
    epilog = (
        "Ejemplos:\n"
        "  python3 wa_viewer.py --msgstore msgstore.db --wadb wa.db\n"
        "  python3 wa_viewer.py --msgstore msgstore.db --wadb wa.db \\\n"
        "                       --contacts contacts.vcf --output chats.html\n"
    )
    p = argparse.ArgumentParser(
        prog="wa_viewer.py",
        description="Delta Forensics - Visor de chats WhatsApp en HTML",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--msgstore",     default="db/msgstore.db",  help="Ruta a msgstore.db")
    p.add_argument("--wadb",         default="db/wa.db",        help="Ruta a wa.db")
    p.add_argument("--output",       default="wa_viewer.html",  help="Archivo HTML de salida")
    p.add_argument("--contacts", default=None,
                   help="Ruta a contacts.vcf exportado del telefono (anade nombres "
                        "de la libreta a los chats privados). Prevalece sobre los "
                        "nombres que WhatsApp guarda internamente.")
    p.add_argument("--default-cc",   default="34",
                   help="Codigo de pais por defecto para numeros locales del VCF "
                        "sin prefijo internacional (default: 34 / Espana).")
    if len(sys.argv) == 1:
        p.print_help()
        sys.exit(0)
    return p.parse_args()

_args        = _parse_args()
MSGSTORE     = Path(_args.msgstore)
WADB         = Path(_args.wadb)
OUTPUT       = Path(_args.output)
CONTACTS_FILE = Path(_args.contacts) if _args.contacts else None
DEFAULT_CC   = "".join(c for c in str(_args.default_cc) if c.isdigit()) or "34"

MSG_ICONS = {
    1: "🖼️ Imagen", 2: "🎵 Audio", 3: "🎬 Video", 4: "👤 Contacto",
    5: "📍 Ubicación", 8: "📄 Documento", 9: "🎤 Nota de voz",
    10: "🔗 Enlace", 13: "🎞️ GIF", 14: "Sticker", 20: "🚫 Eliminado",
}

AVATAR_COLORS = [
    "#e17055","#6c5ce7","#00b894","#0984e3",
    "#fd79a8","#fdcb6e","#00cec9","#a29bfe",
]

# ---------------------------------------------------------------------------

def esc(s):
    if not s:
        return ""
    return (str(s)
        .replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
        .replace("\n", "<br>"))

def ts_fmt(ts):
    if not ts:
        return ""
    try:
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""

def day_str(ts):
    if not ts:
        return ""
    try:
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")
    except Exception:
        return ""

def avatar_color(name):
    return AVATAR_COLORS[hash(str(name)) % len(AVATAR_COLORS)]

def initial(name):
    return (str(name)[0] if name else "?").upper()

def b64img(blob):
    if blob:
        return base64.b64encode(blob).decode()
    return None

# ---------------------------------------------------------------------------

def _table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _phone_to_jid(raw: str, default_cc: str):
    """Normaliza un telefono crudo del VCF a JID WhatsApp <num>@s.whatsapp.net.

    - Conserva el prefijo internacional si viene con '+' o '00'.
    - Si no hay prefijo internacional, antepone default_cc.
    - Descarta numeros con menos de 7 digitos (codigos de servicio: 1470, 11822...
      no son JIDs WhatsApp validos).
    """
    if not raw:
        return None
    plus = raw.strip().startswith("+")
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) < 7:
        return None
    if plus:
        num = digits
    elif digits.startswith("00") and len(digits) > 9:
        num = digits[2:]
    elif digits.startswith(default_cc):
        num = digits
    else:
        num = default_cc + digits
    return f"{num}@s.whatsapp.net"


def parse_vcf(path: Path, default_cc: str):
    """Parsea un vCard 2.1 / 3.0 y devuelve {jid: nombre}.

    Soporta:
    - line folding estandar vCard (lineas que empiezan con espacio o tab)
    - quoted-printable line folding (lineas que acaban en '=')
    - decodificacion ENCODING=QUOTED-PRINTABLE / CHARSET=UTF-8 en FN
    - multiples TEL por contacto (cada uno mapea al mismo nombre)
    """
    import quopri
    result = {}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[WARN] No se pudo leer VCF '{path}': {e}")
        return result

    # Unfold: QP (linea acaba en '=') y vCard estandar (linea siguiente con espacio)
    lines = []
    for ln in raw.splitlines():
        if lines and lines[-1].endswith("="):
            lines[-1] = lines[-1][:-1] + ln
        elif ln.startswith(" ") or ln.startswith("\t"):
            lines[-1] = lines[-1] + ln[1:]
        else:
            lines.append(ln)

    def _decode_value(head: str, value: str) -> str:
        if "QUOTED-PRINTABLE" in head.upper():
            try:
                return quopri.decodestring(
                    value.encode("ascii", "ignore")
                ).decode("utf-8", "replace")
            except Exception:
                return value
        return value

    fn, tels = None, []
    for ln in lines:
        u = ln.upper()
        if u.startswith("BEGIN:VCARD"):
            fn, tels = None, []
        elif u.startswith("END:VCARD"):
            if fn:
                for tel in tels:
                    jid = _phone_to_jid(tel, default_cc)
                    if jid:
                        result.setdefault(jid, fn)
            fn, tels = None, []
        elif u.startswith("FN"):
            head, _, value = ln.partition(":")
            value = _decode_value(head, value).strip()
            if value:
                fn = value
        elif u.startswith("TEL"):
            _, _, value = ln.partition(":")
            value = value.strip()
            if value:
                tels.append(value)
    return result


def load_contacts():
    """Construye el diccionario JID -> nombre buscando en varias fuentes:

    1. wa.db / wa_contacts        (formato legacy, WhatsApp <= 2.25)
    2. msgstore.db / lid_display_name (WhatsApp 2.26+ usa LIDs para privacidad)
    3. msgstore.db / chat.subject      (nombres de grupos)
    4. msgstore.db / message_mentions  (display_name visto en menciones)

    En WhatsApp 2.26 + Android 16, la BD wa.db tiene wa_contacts vacia porque
    los nombres se leen ahora directamente de la libreta del SO. Asi que la
    fuente principal es msgstore.db.
    """
    contacts = {}

    # --- Fuente 1: wa.db legacy ---
    try:
        con = sqlite3.connect(str(WADB))
        if _table_exists(con, "wa_contacts"):
            cnt = con.execute("SELECT COUNT(*) FROM wa_contacts").fetchone()[0]
            if cnt > 0:
                rows = con.execute(
                    "SELECT jid, COALESCE(NULLIF(display_name,''), NULLIF(wa_name,''), number, jid) "
                    "FROM wa_contacts"
                ).fetchall()
                for jid, name in rows:
                    if jid and name:
                        contacts[jid] = name
        con.close()
    except Exception as e:
        print(f"[WARN] wa.db: {e}")

    # --- Fuente 2/3/4: msgstore.db ---
    try:
        con = sqlite3.connect(str(MSGSTORE))

        # lid_display_name: para LIDs (formato @lid moderno)
        if _table_exists(con, "lid_display_name"):
            rows = con.execute("""
                SELECT j.raw_string, ldn.display_name, ldn.username
                FROM lid_display_name ldn
                JOIN jid j ON ldn.lid_row_id = j._id
                WHERE ldn.display_name IS NOT NULL AND ldn.display_name != ''
            """).fetchall()
            for jid, dname, username in rows:
                if jid and jid not in contacts:
                    contacts[jid] = dname or username or jid

        # jid_map + lid_display_name: cruza el LID con el numero de telefono
        # asociado para recuperar nombres de chats individuales (@s.whatsapp.net)
        # cuando WhatsApp 2.26+ solo guarda el nombre asociado al LID.
        if _table_exists(con, "lid_display_name") and _table_exists(con, "jid_map"):
            rows = con.execute("""
                SELECT j_phone.raw_string,
                       COALESCE(NULLIF(ldn.display_name, ''), ldn.username)
                FROM lid_display_name ldn
                JOIN jid j_lid ON ldn.lid_row_id = j_lid._id
                JOIN jid_map jm ON jm.lid_row_id = j_lid._id
                JOIN jid j_phone ON jm.jid_row_id = j_phone._id
                WHERE ldn.display_name IS NOT NULL AND ldn.display_name != ''
                  AND j_phone.raw_string IS NOT NULL
            """).fetchall()
            for phone_jid, name in rows:
                if phone_jid and name and phone_jid not in contacts:
                    contacts[phone_jid] = name

        # chat.subject: nombres de grupos
        if _table_exists(con, "chat"):
            chat_cols = [r[1] for r in con.execute("PRAGMA table_info(chat)")]
            if "subject" in chat_cols:
                rows = con.execute("""
                    SELECT j.raw_string, c.subject
                    FROM chat c
                    JOIN jid j ON c.jid_row_id = j._id
                    WHERE c.subject IS NOT NULL AND c.subject != ''
                """).fetchall()
                for jid, subject in rows:
                    if jid and (jid not in contacts or len(contacts[jid]) < 3):
                        contacts[jid] = subject

        # message_mentions: display_name visto en menciones (cubre individuales)
        if _table_exists(con, "message_mentions"):
            mention_cols = [r[1] for r in con.execute("PRAGMA table_info(message_mentions)")]
            if "display_name" in mention_cols and "jid_row_id" in mention_cols:
                rows = con.execute("""
                    SELECT j.raw_string, mm.display_name, COUNT(*) as votes
                    FROM message_mentions mm
                    JOIN jid j ON mm.jid_row_id = j._id
                    WHERE mm.display_name IS NOT NULL AND mm.display_name != ''
                    GROUP BY j.raw_string, mm.display_name
                    ORDER BY votes DESC
                """).fetchall()
                for jid, dname, _votes in rows:
                    if jid and jid not in contacts:
                        contacts[jid] = dname

        con.close()
    except Exception as e:
        print(f"[WARN] msgstore.db (contactos): {e}")

    # --- Fuente 5: VCF externo (libreta del telefono exportada) ---
    # Sobreescribe a las fuentes WA: el VCF refleja como llama el usuario a sus
    # contactos en su libreta, asi que es la "verdad" frente a los nombres que
    # WhatsApp deduce por LID/menciones.
    if CONTACTS_FILE:
        if CONTACTS_FILE.exists():
            vcf_names = parse_vcf(CONTACTS_FILE, DEFAULT_CC)
            for jid, name in vcf_names.items():
                contacts[jid] = name
            print(f"    + {len(vcf_names)} nombres desde {CONTACTS_FILE.name}")
        else:
            print(f"[WARN] --contacts no existe: {CONTACTS_FILE}")

    return contacts

def jid_to_name(jid, contacts):
    if not jid:
        return ""
    if jid in contacts:
        return contacts[jid]
    # Fallback: extrae numero/identificador del JID, mas legible que el JID entero
    head = jid.split("@")[0]
    # Si es un LID puro (@lid), normalizar a "(LID)"
    if "@lid" in jid:
        return f"LID {head[:8]}…"
    return head

# ---------------------------------------------------------------------------

def build_sidebar_item(chat, preview, contacts, active=False):
    name  = chat["name"]
    color = avatar_color(name)
    ini   = initial(name)
    p     = preview or {}
    pre   = esc((p.get("text") or "")[:45])
    ts    = ts_fmt(p.get("ts", 0))
    pfx   = "Tú: " if p.get("from_me") else ""
    cls   = "active" if active else ""
    cid   = chat["id"]
    return f"""
<div class="chat-item {cls}" data-chat="{cid}" onclick="showChat({cid})">
  <div class="avatar" style="background:{color}">{esc(ini)}</div>
  <div class="chat-info">
    <div class="chat-name-row">
      <span class="chat-name">{esc(name)}</span>
      <span class="chat-ts">{ts}</span>
    </div>
    <div class="chat-preview">{pfx}{pre}</div>
  </div>
</div>"""

def render_msg(msg, contacts):
    from_me  = msg["from_me"]
    mtype    = msg["type"]
    text     = msg["text"]
    thumb    = msg["thumb"]
    mime     = msg["mime"] or ""
    caption  = msg["caption"]
    sender   = msg["sender_name"]
    ts       = ts_fmt(msg["ts"])
    cls      = "outgoing" if from_me else "incoming"
    tick     = " ✓✓" if from_me else ""

    # System message
    if mtype == 7:
        return f'<div class="system-msg">{esc(text) or "Evento del sistema"}</div>'

    content = ""

    # Sender label (groups)
    if not from_me and sender:
        color = avatar_color(sender)
        content += f'<div class="sender-name" style="color:{color}">{esc(sender)}</div>'

    # Media content
    if thumb:
        if "video" in mime:
            content += (f'<div class="media-thumb">'
                        f'<img src="data:image/jpeg;base64,{thumb}" class="thumb"/>'
                        f'<div class="play-btn">▶</div></div>')
        elif "audio" in mime:
            content += f'<div class="audio-row">🎵 <span>Nota de voz</span></div>'
        else:
            content += f'<img src="data:image/jpeg;base64,{thumb}" class="thumb"/>'
        if caption:
            content += f'<div class="caption">{esc(caption)}</div>'
    elif mtype in MSG_ICONS:
        if mtype == 20:
            content += f'<div class="deleted-msg">{MSG_ICONS[mtype]}</div>'
        else:
            label = MSG_ICONS[mtype]
            if mtype == 8 and msg["file_path"]:
                fname = msg["file_path"].split("/")[-1].split("\\")[-1]
                label += f' {esc(fname)}'
            content += f'<div class="media-pill">{label}</div>'
            if caption:
                content += f'<div class="caption">{esc(caption)}</div>'

    # Text
    if text:
        content += f'<div class="text">{esc(text)}</div>'

    if not content:
        content = f'<div class="text text-dim">[Tipo {mtype}]</div>'

    return f"""
<div class="msg {cls}">
  <div class="bubble">
    {content}
    <div class="msg-time">{ts}{tick}</div>
  </div>
</div>"""

# ---------------------------------------------------------------------------

def build_chat_panel(chat, msgs, contacts, display):
    name  = chat["name"]
    jid   = chat["jid"]
    color = avatar_color(name)
    ini   = initial(name)
    cid   = chat["id"]
    disp  = "flex" if display else "none"

    msgs_html = ""
    current_day = None
    for msg in msgs:
        d = day_str(msg["ts"])
        if d != current_day:
            current_day = d
            msgs_html += f'<div class="day-sep"><span>{d}</span></div>'
        msgs_html += render_msg(msg, contacts)

    if not msgs:
        msgs_html = '<div class="empty">Sin mensajes</div>'

    return f"""
<div class="chat-panel" id="chat-{cid}" style="display:{disp}">
  <div class="chat-header">
    <div class="avatar" style="background:{color}">{esc(ini)}</div>
    <div class="header-info">
      <div class="header-name">{esc(name)}</div>
      <div class="header-sub">{esc(jid)}</div>
    </div>
  </div>
  <div class="msgs-list" id="msgs-{cid}">
    {msgs_html}
  </div>
</div>"""

# ---------------------------------------------------------------------------

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0b141a;--sidebar-bg:#111b21;--header:#202c33;
  --out:#005c4b;--in:#202c33;--text:#e9edef;--dim:#8696a0;
  --accent:#00a884;--border:#2a3942;--hover:#2a3942;
}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);
  color:var(--text);height:100vh;overflow:hidden;display:flex}
.app{display:flex;width:100%;height:100vh}

/* SIDEBAR */
.sidebar{width:360px;min-width:300px;display:flex;flex-direction:column;
  background:var(--sidebar-bg);border-right:1px solid var(--border)}
.sidebar-header{padding:.9rem 1.2rem;background:var(--header);
  font-size:1.1rem;font-weight:700;display:flex;align-items:center;gap:.5rem}
.sidebar-header .wa-icon{color:var(--accent);font-size:1.4rem}
.chats-list{overflow-y:auto;flex:1}
.chat-item{display:flex;align-items:center;gap:.85rem;padding:.75rem 1.1rem;
  cursor:pointer;border-bottom:1px solid #1f2c33;transition:background .12s}
.chat-item:hover,.chat-item.active{background:var(--hover)}
.avatar{width:46px;height:46px;border-radius:50%;display:flex;
  align-items:center;justify-content:center;font-weight:700;
  font-size:1.05rem;color:#fff;flex-shrink:0}
.chat-info{flex:1;min-width:0}
.chat-name-row{display:flex;justify-content:space-between;align-items:baseline}
.chat-name{font-size:.93rem;font-weight:500;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.chat-ts{font-size:.7rem;color:var(--dim);margin-left:.4rem;flex-shrink:0}
.chat-preview{font-size:.8rem;color:var(--dim);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;margin-top:.12rem}

/* MAIN */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.chat-panel{flex:1;display:flex;flex-direction:column;overflow:hidden}
.chat-header{padding:.7rem 1.2rem;background:var(--header);
  display:flex;align-items:center;gap:.85rem;border-bottom:1px solid var(--border)}
.header-name{font-size:.98rem;font-weight:600}
.header-sub{font-size:.75rem;color:var(--dim)}
.msgs-list{flex:1;overflow-y:auto;padding:.8rem 6%;
  display:flex;flex-direction:column;gap:.15rem;
  background:var(--bg)}

/* MESSAGES */
.msg{display:flex;margin:.08rem 0}
.msg.outgoing{justify-content:flex-end}
.msg.incoming{justify-content:flex-start}
.bubble{max-width:62%;padding:.45rem .7rem .35rem;border-radius:8px;
  position:relative;word-break:break-word}
.msg.outgoing .bubble{background:var(--out);border-bottom-right-radius:2px}
.msg.incoming .bubble{background:var(--in);border-bottom-left-radius:2px}
.sender-name{font-size:.76rem;font-weight:700;margin-bottom:.18rem}
.text{font-size:.88rem;line-height:1.45}
.text-dim{color:var(--dim);font-style:italic}
.msg-time{font-size:.66rem;color:var(--dim);text-align:right;margin-top:.18rem}
.thumb{max-width:240px;max-height:200px;border-radius:6px;
  display:block;cursor:pointer}
.media-thumb{position:relative;display:inline-block}
.play-btn{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  background:rgba(0,0,0,.55);border-radius:50%;width:42px;height:42px;
  display:flex;align-items:center;justify-content:center;font-size:1.1rem}
.media-pill{display:inline-flex;align-items:center;gap:.4rem;
  background:rgba(255,255,255,.07);padding:.35rem .65rem;border-radius:18px;
  font-size:.82rem;color:var(--dim)}
.audio-row{display:flex;align-items:center;gap:.5rem;
  font-size:.85rem;color:var(--dim);padding:.2rem 0}
.caption{font-size:.84rem;margin-top:.3rem}
.deleted-msg{color:var(--dim);font-style:italic;font-size:.85rem}
.system-msg{text-align:center;margin:.5rem auto;padding:.28rem .8rem;
  background:#182229;border-radius:8px;font-size:.76rem;
  color:var(--dim);max-width:75%}
.day-sep{text-align:center;margin:.7rem 0}
.day-sep span{background:#182229;padding:.28rem .9rem;
  border-radius:8px;font-size:.76rem;color:var(--dim)}
.empty{text-align:center;color:var(--dim);padding:2rem;font-size:.9rem}
.no-chat{flex:1;display:flex;align-items:center;justify-content:center;
  flex-direction:column;gap:.5rem;color:var(--dim)}
.no-chat .wa-big{font-size:4rem}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-thumb{background:#374045;border-radius:3px}
"""

_JS = """
function showChat(id){
  document.querySelectorAll('.chat-panel').forEach(el=>el.style.display='none');
  var p=document.getElementById('chat-'+id);
  if(p){p.style.display='flex';var l=document.getElementById('msgs-'+id);if(l)l.scrollTop=l.scrollHeight;}
  document.querySelectorAll('.chat-item').forEach(el=>el.classList.remove('active'));
  var it=document.querySelector('[data-chat="'+id+'"]');
  if(it)it.classList.add('active');
}
document.addEventListener('DOMContentLoaded',function(){
  var first=document.querySelector('.chat-messages');
  if(first){var l=first.querySelector('.msgs-list');if(l)l.scrollTop=l.scrollHeight;}
  // scroll first visible
  document.querySelectorAll('.msgs-list').forEach(function(l){l.scrollTop=l.scrollHeight;});
});
"""

# ---------------------------------------------------------------------------

def main():
    for p, label in [(MSGSTORE, "msgstore.db"), (WADB, "wa.db")]:
        if not p.exists():
            print(f"[ERROR] No se encuentra: {p}")
            sys.exit(1)

    print("[*] Cargando contactos...")
    contacts = load_contacts()
    print(f"    {len(contacts)} contactos")

    print("[*] Leyendo mensajes...")
    con = sqlite3.connect(str(MSGSTORE))
    cur = con.cursor()

    # Detectar tablas disponibles (varia segun version de WhatsApp)
    has_jid_map   = _table_exists(con, "jid_map")
    has_thumbnail = _table_exists(con, "message_thumbnail")
    has_media     = _table_exists(con, "message_media")

    # Chats — resuelve LIDs (@lid) a JIDs reales si jid_map existe (WA moderno)
    if has_jid_map:
        chats_raw = cur.execute("""
            SELECT c._id,
                   COALESCE(j2.raw_string, j.raw_string) as resolved_jid,
                   c.sort_timestamp
            FROM chat c
            JOIN jid j ON c.jid_row_id = j._id
            LEFT JOIN jid_map jm ON j._id = jm.lid_row_id
            LEFT JOIN jid j2 ON jm.jid_row_id = j2._id
            ORDER BY c.sort_timestamp DESC
        """).fetchall()
    else:
        chats_raw = cur.execute("""
            SELECT c._id, j.raw_string, c.sort_timestamp
            FROM chat c
            JOIN jid j ON c.jid_row_id = j._id
            ORDER BY c.sort_timestamp DESC
        """).fetchall()

    chats = []
    for cid, jid, sts in chats_raw:
        name = jid_to_name(jid, contacts)
        chats.append({"id": cid, "jid": jid or "", "name": name})

    # Construir queries de mensajes segun tablas disponibles
    sender_join = ""
    sender_col  = "NULL"
    if has_jid_map:
        sender_join = """LEFT JOIN jid j ON m.sender_jid_row_id = j._id
            LEFT JOIN jid_map jm ON j._id = jm.lid_row_id
            LEFT JOIN jid j2 ON jm.jid_row_id = j2._id"""
        sender_col = "COALESCE(j2.raw_string, j.raw_string)"
    else:
        sender_join = "LEFT JOIN jid j ON m.sender_jid_row_id = j._id"
        sender_col  = "j.raw_string"

    thumb_join = ("LEFT JOIN message_thumbnail mt ON m._id = mt.message_row_id"
                  if has_thumbnail else "")
    thumb_col  = "mt.thumbnail" if has_thumbnail else "NULL"

    media_join = ("LEFT JOIN message_media mm ON m._id = mm.message_row_id"
                  if has_media else "")
    media_cols = "mm.file_path, mm.mime_type, mm.media_caption" if has_media else "NULL, NULL, NULL"

    preview_media = "mm.media_caption" if has_media else "NULL"
    preview_join  = media_join

    MSG_SQL = f"""
        SELECT m._id, m.from_me, m.timestamp, m.message_type,
               m.text_data,
               {sender_col} as resolved_jid,
               {thumb_col},
               {media_cols}
        FROM message m
        {sender_join}
        {thumb_join}
        {media_join}
        WHERE m.chat_row_id = ? ORDER BY m.sort_id ASC
    """

    PREVIEW_SQL = f"""
        SELECT m.text_data, m.timestamp, m.from_me, m.message_type,
               {preview_media}
        FROM message m
        {preview_join}
        WHERE m.chat_row_id = ? ORDER BY m.sort_id DESC LIMIT 1
    """

    # Messages per chat
    sidebar_html = ""
    panels_html  = ""

    total_chats = len(chats)
    total_msgs  = 0
    bar_width   = 30

    def _safe(s, n=32):
        """Trunca a n chars y normaliza para que el terminal no se rompa con unicode."""
        s = (str(s) or "")[:n]
        return s.encode("ascii", "replace").decode().ljust(n)

    for i, chat in enumerate(chats):
        cid = chat["id"]

        # Last message preview
        last = cur.execute(PREVIEW_SQL, (cid,)).fetchone()

        preview = {}
        if last:
            text, ts, from_me, mtype, cap = last
            preview = {
                "text": text or cap or MSG_ICONS.get(mtype, ""),
                "ts": ts, "from_me": from_me,
            }

        sidebar_html += build_sidebar_item(chat, preview, contacts, active=(i == 0))

        # All messages
        rows = cur.execute(MSG_SQL, (cid,)).fetchall()

        msgs = []
        for row in rows:
            mid, from_me, ts, mtype, text, sender_jid, thumb, fpath, mime, cap = row
            msgs.append({
                "id": mid, "from_me": bool(from_me), "ts": ts or 0,
                "type": mtype or 0, "text": text or "",
                "sender_name": jid_to_name(sender_jid, contacts),
                "thumb": b64img(thumb),
                "file_path": fpath or "", "mime": mime or "",
                "caption": cap or "",
            })

        total_msgs += len(msgs)
        panels_html += build_chat_panel(chat, msgs, contacts, display=(i == 0))

        # Barra de progreso in-place: se sobreescribe en la misma linea
        done    = i + 1
        pct     = done / total_chats
        filled  = int(bar_width * pct)
        bar     = "#" * filled + "-" * (bar_width - filled)
        line = (f"\r  [{bar}] {done:>4}/{total_chats} ({pct*100:5.1f}%) "
                f"- {total_msgs:>8,} msgs - {_safe(chat['name'])}")
        print(line, end="", flush=True)

    print()  # Salto de linea final tras la barra
    print(f"  Procesados {total_chats} chats con {total_msgs:,} mensajes totales.")
    con.close()

    html = (
        f'<!DOCTYPE html><html lang="es"><head>'
        f'<meta charset="UTF-8">'
        f'<title>WhatsApp Viewer</title>'
        f'<style>{_CSS}</style></head><body>'
        f'<div class="app">'
        f'<div class="sidebar">'
        f'<div class="sidebar-header"><span class="wa-icon">💬</span> Delta Forensics &middot; WhatsApp Viewer</div>'
        f'<div class="chats-list">{sidebar_html}</div>'
        f'</div>'
        f'<div class="main">{panels_html}</div>'
        f'</div>'
        f'<script>{_JS}</script>'
        f'</body></html>'
    )

    OUTPUT.write_text(html, encoding="utf-8")
    print(f"\n[OK] {OUTPUT}")
    print(f"     Abre ese archivo en Chrome o Firefox.")

if __name__ == "__main__":
    main()
