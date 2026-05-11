#!/usr/bin/env python3
"""
Forensic Android Backup + WhatsApp Extractor
Autor: Deltadri
Compatible: Android 8-15+, cualquier fabricante

Dependencias obligatorias: adb en PATH
Dependencias opcionales (extraccion WhatsApp):
  - abe/abe.jar          (Android Backup Extractor)
  - legacy_apk/LegacyWhatsApp.apk
  - java en PATH
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

FECHA     = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
BASE      = Path.home() / "backup_movil" / FECHA
ALMACEN   = BASE / "almacenamiento_interno"
DATOS     = BASE / "datos_forenses"
INFORME   = BASE / "informe.html"
HASHES    = BASE / "hashes.sha256"

WA_DIR      = BASE / "whatsapp"
WA_BACKUP   = WA_DIR / "whatsapp.ab"
WA_EXTRACT  = WA_DIR / "extracted"
WA_APKS_DIR = WA_DIR / "apks_originales"

_HERE      = Path(__file__).parent
LEGACY_APK = _HERE / "legacy_apk" / "LegacyWhatsApp.apk"
ABE_JAR    = _HERE / "abe" / "abe.jar"

LOG_LINES: list[str] = []

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg)
    LOG_LINES.append(msg)

# ---------------------------------------------------------------------------
# ADB HELPERS
# ---------------------------------------------------------------------------

def adb_shell(cmd, timeout: int = 15) -> str:
    """Run `adb shell <cmd>`, return stdout cleaned. Empty string on failure."""
    args = ["adb", "shell"] + (cmd if isinstance(cmd, list) else cmd.split())
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        return r.stdout.replace("\r", "").strip()
    except Exception:
        return ""


def adb_run(args: list, timeout: int = 60) -> tuple[bool, str, str]:
    """Run `adb <args>`. Returns (success, stdout, stderr)."""
    try:
        r = subprocess.run(["adb"] + args, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except Exception as e:
        return False, "", str(e)


def get_prop(prop: str) -> str:
    return adb_shell(["getprop", prop])

# ---------------------------------------------------------------------------
# 1. PREREQUISITES
# ---------------------------------------------------------------------------

def check_prerequisites() -> None:
    log("[*] Comprobando herramientas necesarias...")
    if shutil.which("adb") is None:
        log("[ERROR] 'adb' no encontrado en PATH.")
        log("        Instala android-tools / android-platform-tools.")
        sys.exit(1)
    ver = subprocess.run(["adb", "--version"], capture_output=True, text=True)
    log(f"    OK - {ver.stdout.splitlines()[0] if ver.stdout else 'adb'}")

# ---------------------------------------------------------------------------
# 2. DEVICE
# ---------------------------------------------------------------------------

def check_device() -> str:
    subprocess.run(["adb", "start-server"], capture_output=True)
    ok, out, _ = adb_run(["devices"])
    if not ok:
        log("[ERROR] No se pudo ejecutar adb devices.")
        sys.exit(1)

    lines = [l for l in out.splitlines()[1:] if l.strip()]
    devices  = [l.split()[0] for l in lines if l.split()[-1] == "device"]
    unauth   = [l.split()[0] for l in lines if "unauthorized" in l]

    if unauth:
        log("[ERROR] Dispositivo NO autorizado. Acepta la huella RSA en el movil.")
        sys.exit(1)
    if not devices:
        log("[ERROR] No hay ningun dispositivo conectado.")
        log("        Comprueba cable de datos, Depuracion USB activada y modo MTP.")
        sys.exit(1)
    if len(devices) > 1:
        log(f"[AVISO] Varios dispositivos. Usando: {devices[0]}")

    log(f"[*] Dispositivo: {devices[0]}")
    return devices[0]

# ---------------------------------------------------------------------------
# 3. IDENTIFICACION
# ---------------------------------------------------------------------------

def identify_device() -> dict:
    log("[1/8] Identificando dispositivo...")

    props = {
        "modelo":      get_prop("ro.product.model"),
        "marca":       get_prop("ro.product.manufacturer"),
        "brand":       get_prop("ro.product.brand"),
        "device":      get_prop("ro.product.device"),
        "android_ver": get_prop("ro.build.version.release"),
        "sdk":         get_prop("ro.build.version.sdk"),
        "serie":       get_prop("ro.serialno"),
        "build":       get_prop("ro.build.display.id"),
        "kernel":      adb_shell("uname -a"),
        "huella":      get_prop("ro.build.fingerprint"),
        "seguridad":   get_prop("ro.build.version.security_patch"),
        "arq":         get_prop("ro.product.cpu.abi"),
        "idioma":      get_prop("persist.sys.locale"),
        "timezone":    get_prop("persist.sys.timezone"),
        "bootloader":  get_prop("ro.bootloader"),
        "radio":       get_prop("gsm.version.baseband"),
    }

    capa = "Android stock"
    for prop_name, label in [
        ("ro.mi.os.version.name",     "HyperOS"),
        ("ro.miui.ui.version.name",   "MIUI"),
        ("ro.build.version.opporom",  "ColorOS"),
        ("ro.build.version.oneui",    "One UI"),
        ("ro.build.version.emui",     "EMUI"),
        ("ro.build.version.realmeui", "Realme UI"),
        ("ro.oxygen.version",         "OxygenOS"),
        ("ro.nothing.version",        "Nothing OS"),
    ]:
        val = get_prop(prop_name)
        if val:
            capa = f"{label} {val}"
            break
    props["capa"] = capa

    for k in ("modelo", "marca", "android_ver", "sdk", "serie"):
        if not props[k]:
            props[k] = "Desconocido"

    ok, raw, _ = adb_run(["shell", "getprop"])
    if raw:
        (DATOS / "propiedades.txt").write_text(raw, encoding="utf-8", errors="replace")

    return props

# ---------------------------------------------------------------------------
# 4. ALMACENAMIENTO
# ---------------------------------------------------------------------------

def pull_storage() -> None:
    log("[2/8] Copiando almacenamiento interno (puede tardar)...")
    tam = adb_shell("du -sh /storage/emulated/0/")
    log(f"      Tamano estimado: {tam.split()[0] if tam else '?'}")

    ALMACEN.mkdir(parents=True, exist_ok=True)
    pull_log = DATOS / "pull_log.txt"

    # /storage/emulated/0/ es mas fiable que /sdcard/ en Android moderno
    ok, out, err = adb_run(["pull", "/storage/emulated/0/", str(ALMACEN)], timeout=3600)
    with open(pull_log, "w", encoding="utf-8") as f:
        f.write(out + "\n" + err)

    if not ok or not any(ALMACEN.iterdir()):
        log("      Fallback a /sdcard/...")
        ok2, out2, err2 = adb_run(["pull", "/sdcard/", str(ALMACEN)], timeout=3600)
        with open(pull_log, "a", encoding="utf-8") as f:
            f.write(out2 + "\n" + err2)

# ---------------------------------------------------------------------------
# 5. APLICACIONES
# ---------------------------------------------------------------------------

def list_apps() -> dict:
    log("[3/8] Listando aplicaciones...")
    cmds = {
        "apps_todas.txt":          "pm list packages -f",
        "apps_usuario.txt":        "pm list packages -f -3",
        "apps_sistema.txt":        "pm list packages -f -s",
        "apps_deshabilitadas.txt": "pm list packages -d",
    }
    counts = {}
    for fname, cmd in cmds.items():
        out = adb_shell(cmd, timeout=30)
        if out:
            (DATOS / fname).write_text(out, encoding="utf-8", errors="replace")
        counts[fname] = len([l for l in out.splitlines() if l.strip()])
    return counts

# ---------------------------------------------------------------------------
# 6. ESTADO DEL SISTEMA
# ---------------------------------------------------------------------------

def system_state() -> dict:
    log("[4/8] Estado del sistema (bateria, red, procesos)...")
    cmds = {
        "bateria.txt":          "dumpsys battery",
        "wifi.txt":             "dumpsys wifi",
        "cpuinfo.txt":          "dumpsys cpuinfo",
        "meminfo.txt":          "dumpsys meminfo",
        "conectividad.txt":     "dumpsys connectivity",
        "telefonia.txt":        "dumpsys telephony.registry",
        "diskstats.txt":        "dumpsys diskstats",
        "almacenamiento.txt":   "df -h",
        "red.txt":              "ip addr",
        "procesos.txt":         "ps -A",
        "ajustes_sistema.txt":  "settings list system",
        "ajustes_seguros.txt":  "settings list secure",
        "ajustes_globales.txt": "settings list global",
        "servicios.txt":        "service list",
        "cpu_detalle.txt":      "cat /proc/cpuinfo",
        "ram_detalle.txt":      "cat /proc/meminfo",
    }
    for fname, cmd in cmds.items():
        out = adb_shell(cmd.split(), timeout=20)
        if out:
            (DATOS / fname).write_text(out, encoding="utf-8", errors="replace")

    def read(name):
        p = DATOS / name
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""

    # Bateria
    battery: dict[str, str] = {}
    for line in read("bateria.txt").splitlines():
        if ":" in line:
            k, _, v = line.strip().partition(":")
            battery[k.strip().lower()] = v.strip()

    # CPU
    cpu_raw   = read("cpu_detalle.txt")
    cpu_model = ""
    num_cores = 0
    for line in cpu_raw.splitlines():
        if line.startswith("Hardware") and not cpu_model:
            cpu_model = line.split(":", 1)[-1].strip()
        if line.startswith("model name") and not cpu_model:
            cpu_model = line.split(":", 1)[-1].strip()
        if line.startswith("processor"):
            num_cores += 1

    # RAM
    ram_total = ""
    for line in read("ram_detalle.txt").splitlines():
        if "MemTotal" in line:
            try:
                ram_total = f"{int(line.split()[1]) / 1024 / 1024:.2f} GB"
            except Exception:
                pass
            break

    # Red
    ip_wifi = ""
    for line in read("red.txt").splitlines():
        if "wlan" in line.lower() and "inet " in line:
            parts = line.strip().split()
            if len(parts) >= 2:
                ip_wifi = parts[1].split("/")[0]
                break

    num_procs = len([l for l in read("procesos.txt").splitlines() if l.strip()])

    return {
        "battery":   battery,
        "cpu_model": cpu_model,
        "num_cores": num_cores,
        "ram_total": ram_total,
        "ip_wifi":   ip_wifi,
        "num_procs": num_procs,
    }

# ---------------------------------------------------------------------------
# 7. HASHES SHA-256
# ---------------------------------------------------------------------------

def _human(n: int) -> str:
    v = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if v < 1024:
            return f"{v:.1f}{unit}"
        v /= 1024
    return f"{v:.1f}P"


def _inventory() -> tuple[int, str]:
    """Cuenta ficheros y tamano total en BASE. Se llama despues de toda la extraccion."""
    files = [f for f in BASE.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    return len(files), _human(total)


def _write_hashes() -> None:
    """Escribe hashes.sha256 cubriendo TODOS los ficheros generados (incluye WA e informe)."""
    log("[7/8] Generando manifiesto SHA-256...")
    files = [f for f in BASE.rglob("*") if f.is_file() and f.name != "hashes.sha256"]
    lines = []
    for f in files:
        try:
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            lines.append(f"{h}  {f.relative_to(BASE)}")
        except Exception:
            pass
    HASHES.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# 8. WHATSAPP EXTRACTION
# ---------------------------------------------------------------------------

def _wa_prereqs() -> tuple[bool, str]:
    if not LEGACY_APK.exists():
        return False, f"No existe {LEGACY_APK}"
    if not ABE_JAR.exists():
        return False, f"No existe {ABE_JAR}"
    if shutil.which("java") is None:
        return False, "java no esta instalado"
    return True, ""


def _wa_installed() -> bool:
    return "package:" in adb_shell(["pm", "path", "com.whatsapp"], timeout=10)


def _wa_get_apk_paths() -> list[str]:
    out = adb_shell(["pm", "path", "com.whatsapp"], timeout=10)
    return [l.replace("package:", "").strip() for l in out.splitlines()
            if l.startswith("package:")]


def _wa_pull_apks(apk_paths: list[str]) -> list[str] | None:
    WA_APKS_DIR.mkdir(parents=True, exist_ok=True)
    local = []
    for p in apk_paths:
        dst = WA_APKS_DIR / Path(p).name
        ok, _, err = adb_run(["pull", p, str(dst)], timeout=60)
        if not ok:
            log(f"[ERROR] No se pudo extraer {Path(p).name}: {err}")
            return None
        local.append(str(dst))
    return local


def _wa_reboot_wait() -> bool:
    log("[WA]  Reiniciando dispositivo...")
    adb_run(["reboot"], timeout=10)
    time.sleep(25)
    for _ in range(36):          # hasta ~3 min
        time.sleep(5)
        ok, out, _ = adb_run(["devices"], timeout=10)
        if ok:
            for l in out.splitlines()[1:]:
                parts = l.split()
                if len(parts) >= 2 and parts[1] == "device":
                    log("[WA]  Dispositivo listo")
                    return True
    log("[ERROR] Timeout esperando reinicio")
    return False


def _wa_install_legacy(sdk: str) -> bool:
    ok, _, err = adb_run(
        ["install", "-r", "-d", "--bypass-low-target-sdk-block", str(LEGACY_APK)],
        timeout=60,
    )
    if not ok:
        log("[WA]  Reintentando sin --bypass-low-target-sdk-block...")
        ok, _, err = adb_run(["install", "-r", "-d", str(LEGACY_APK)], timeout=60)
    if not ok:
        log(f"[ERROR] Fallo instalando APK legacy: {err}")
    return ok


def _wa_backup() -> bool:
    WA_DIR.mkdir(parents=True, exist_ok=True)
    log("[WA]  Abriendo WhatsApp...")
    adb_run(["shell", "am", "start", "-n", "com.whatsapp/.Main"], timeout=10)
    log("[WA]  Esperando 30 segundos a que cargue y aceptes permisos en el movil...")
    for i in range(30, 0, -1):
        print(f"\r[WA]  Arrancando WhatsApp... {i:2d}s ", end="", flush=True)
        time.sleep(1)
    print()
    log("[WA]  Ejecutando adb backup...")
    log("[WA]  >>> Aparecera un dialogo en el movil: pulsa 'Hacer copia de seguridad' (tienes 3 min) <<<")
    try:
        # Popen sin capturar stdout para no bloquear; el usuario interactua en el movil
        proc = subprocess.Popen(
            ["adb", "backup", "-f", str(WA_BACKUP), "com.whatsapp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        log("[ERROR] Timeout: no se acepto el backup en el dispositivo")
        return False
    except Exception as e:
        log(f"[ERROR] adb backup: {e}")
        return False

    if not WA_BACKUP.exists() or WA_BACKUP.stat().st_size < 1024:
        log("[ERROR] El backup esta vacio o no existe")
        return False
    with open(WA_BACKUP, "rb") as f:
        if not f.read(14).startswith(b"ANDROID BACKUP"):
            log("[ERROR] Cabecera .ab invalida")
            return False

    log(f"[WA]  Backup valido ({WA_BACKUP.stat().st_size:,} bytes)")
    return True


def _wa_extract_ab() -> bool:
    WA_EXTRACT.mkdir(parents=True, exist_ok=True)
    tar_path = WA_EXTRACT / "whatsapp.tar"
    log("[WA]  Convirtiendo .ab -> .tar con abe.jar...")
    try:
        r = subprocess.run(
            ["java", "-jar", str(ABE_JAR), "unpack", str(WA_BACKUP), str(tar_path), ""],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=120,
        )
        if r.returncode != 0 or not tar_path.exists():
            log(f"[ERROR] abe.jar: {r.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        log("[ERROR] Timeout en abe.jar")
        return False

    log("[WA]  Extrayendo tar...")
    try:
        with tarfile.open(str(tar_path)) as tf:
            # filter='data' disponible desde Python 3.11.4 para evitar DeprecationWarning
            extract_kwargs = {"filter": "data"} if sys.version_info >= (3, 12) else {}
            tf.extractall(str(WA_EXTRACT), **extract_kwargs)
        return True
    except Exception as e:
        log(f"[ERROR] Extrayendo tar: {e}")
        return False


def _wa_reinstall(local_apks: list[str]) -> bool:
    log("[WA]  Restaurando WhatsApp original...")
    adb_run(["shell", "pm", "uninstall", "-k", "com.whatsapp"], timeout=20)
    ok, _, err = adb_run(["install-multiple", "-r", "-d"] + local_apks, timeout=120)
    if not ok:
        log("[WA]  Reintentando sin flag de downgrade...")
        ok, _, err = adb_run(["install-multiple", "-r"] + local_apks, timeout=120)
    if not ok:
        log(f"[ERROR] No se pudo reinstalar WhatsApp: {err}")
        return False
    log("[WA]  WhatsApp restaurado")
    return True


def extract_whatsapp(sdk: str) -> dict:
    log("\n[*] Extraccion WhatsApp...")

    ok, reason = _wa_prereqs()
    if not ok:
        log(f"[WA]  Omitido: {reason}")
        return {"status": "skipped", "reason": reason}

    if not _wa_installed():
        msg = "WhatsApp no esta instalado"
        log(f"[WA]  {msg}")
        return {"status": "skipped", "reason": msg}

    apk_paths = _wa_get_apk_paths()
    if not apk_paths:
        return {"status": "error", "reason": "No se pudieron obtener rutas APK"}
    log(f"[WA]  {len(apk_paths)} APK(s) encontrados")

    local_apks = _wa_pull_apks(apk_paths)
    if not local_apks:
        return {"status": "error", "reason": "Error extrayendo APKs originales"}

    adb_run(["shell", "am", "force-stop", "com.whatsapp"], timeout=10)

    ok2, _, err2 = adb_run(["shell", "pm", "uninstall", "-k", "com.whatsapp"], timeout=20)
    if not ok2:
        return {"status": "error", "reason": f"Error desinstalando WA: {err2}"}

    if not _wa_reboot_wait():
        _wa_reinstall(local_apks)
        return {"status": "error", "reason": "Error reiniciando dispositivo"}

    if not _wa_install_legacy(sdk):
        _wa_reinstall(local_apks)
        return {"status": "error", "reason": "Error instalando APK legacy"}

    if not _wa_backup():
        _wa_reinstall(local_apks)
        return {"status": "error", "reason": "Error en adb backup"}

    extracted = _wa_extract_ab()
    _wa_reinstall(local_apks)

    if not extracted:
        return {"status": "error", "reason": "Error extrayendo backup"}

    db_files = list(WA_EXTRACT.rglob("*.db"))
    log(f"[WA]  Bases de datos encontradas: {len(db_files)}")
    for f in db_files:
        log(f"      - {f.relative_to(BASE)}")

    return {"status": "ok", "db_files": [str(f.relative_to(BASE)) for f in db_files]}

# ---------------------------------------------------------------------------
# HTML REPORT
# ---------------------------------------------------------------------------

_ICONS = {
    "phone":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>',
    "building": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z"/></svg>',
    "layers":   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/></svg>',
    "battery":  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="6" width="18" height="12" rx="2"/><line x1="22" y1="11" x2="22" y2="13"/></svg>',
    "app":      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
    "cog":      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 10v6M4.22 4.22l4.24 4.24m7.08 7.08l4.24 4.24M1 12h6m10 0h6M4.22 19.78l4.24-4.24m7.08-7.08l4.24-4.24"/></svg>',
    "bolt":     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
    "download": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
    "database": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>',
    "file":     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    "folder":   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    "search":   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    "chip":     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="22"/><line x1="15" y1="20" x2="15" y2="22"/><line x1="20" y1="9" x2="22" y2="9"/><line x1="20" y1="15" x2="22" y2="15"/><line x1="2" y1="9" x2="4" y2="9"/><line x1="2" y1="15" x2="4" y2="15"/></svg>',
    "wifi":     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>',
    "wa":       '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
}

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0d1117; --bg2: #161b22; --bg3: #21262d; --border: #30363d;
  --text: #c9d1d9; --text-dim: #8b949e; --accent: #58a6ff;
  --accent2: #79c0ff; --purple: #8957e5; --green: #3fb950;
}
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.6; padding: 1.5rem; }
.container { max-width: 1200px; margin: 0 auto; }
header { background: linear-gradient(135deg, #1f6feb 0%, var(--purple) 100%);
  padding: 2rem; border-radius: 12px; margin-bottom: 2rem;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4); position: relative; overflow: hidden; }
header::before { content: ""; position: absolute; top: -50%; right: -10%;
  width: 300px; height: 300px;
  background: radial-gradient(circle, rgba(255,255,255,0.1), transparent);
  border-radius: 50%; }
header h1 { color: white; font-size: 1.8rem; margin-bottom: 0.5rem;
  display: flex; align-items: center; gap: 0.75rem; position: relative; }
header h1 svg { width: 32px; height: 32px; }
header p { color: rgba(255,255,255,0.9); font-size: 0.95rem; position: relative; }
header p code { background: rgba(0,0,0,0.25); color: white; padding: 2px 8px; border-radius: 4px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem; margin-bottom: 2rem; }
.card { background: var(--bg2); border: 1px solid var(--border);
  border-radius: 10px; padding: 1.2rem; transition: all 0.2s ease; }
.card:hover { transform: translateY(-3px); border-color: var(--accent);
  box-shadow: 0 4px 16px rgba(88,166,255,0.15); }
.card-icon { color: var(--accent); width: 28px; height: 28px; margin-bottom: 0.8rem; opacity: 0.8; }
.card-icon svg { width: 100%; height: 100%; }
.card-label { font-size: 0.75rem; color: var(--text-dim);
  text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 0.3rem; }
.card-value { font-size: 1.3rem; color: var(--text); font-weight: 600; word-break: break-word; }
section { background: var(--bg2); border: 1px solid var(--border);
  border-radius: 10px; padding: 1.5rem; margin-bottom: 1.5rem; }
section h2 { color: var(--accent); margin-bottom: 1.2rem; padding-bottom: 0.7rem;
  border-bottom: 1px solid var(--border); display: flex; align-items: center;
  gap: 0.6rem; font-size: 1.25rem; }
section h2 svg { width: 22px; height: 22px; }
table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; font-size: 0.9rem; }
th, td { padding: 0.65rem 0.8rem; text-align: left; border-bottom: 1px solid var(--bg3); }
th { background: var(--bg3); color: var(--accent); font-weight: 600;
  font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px; }
tr:hover td { background: rgba(88,166,255,0.05); }
code { background: var(--bg); padding: 2px 7px; border-radius: 4px;
  font-size: 0.85em; color: var(--accent2); font-family: 'JetBrains Mono', Consolas, monospace; }
.badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 12px;
  font-size: 0.7rem; background: var(--accent); color: white;
  margin-left: 0.5rem; font-weight: 500; }
footer { text-align: center; color: var(--text-dim); margin-top: 2rem;
  padding: 1.5rem; font-size: 0.85rem; border-top: 1px solid var(--border); }
.info-grid { display: grid; grid-template-columns: max-content 1fr; gap: 0.7rem 1.5rem; }
.info-grid dt { color: var(--text-dim); font-weight: 500; font-size: 0.9rem; }
.info-grid dd { color: var(--text); word-break: break-all; font-size: 0.9rem; }
ul.archivos { list-style: none; padding: 0; display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 0.5rem; }
ul.archivos li { background: var(--bg); padding: 0.6rem 0.9rem; border-radius: 6px;
  border-left: 3px solid var(--accent); font-size: 0.85rem;
  display: flex; align-items: center; gap: 0.5rem; }
ul.archivos li svg { width: 16px; height: 16px; color: var(--text-dim); flex-shrink: 0; }
.table-wrap { overflow-x: auto; }
@media (max-width: 600px) {
  body { padding: 1rem; }
  header h1 { font-size: 1.4rem; }
  .info-grid { grid-template-columns: 1fr; gap: 0.3rem; }
  .info-grid dt { margin-top: 0.5rem; }
}
"""


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _card(icon: str, label: str, value, badge: str = "") -> str:
    b = f'<span class="badge">{badge}</span>' if badge else ""
    return (f'<div class="card"><div class="card-icon">{_ICONS[icon]}</div>'
            f'<div class="card-label">{label}</div>'
            f'<div class="card-value">{_esc(value)}{b}</div></div>')


def _row(label: str, value) -> str:
    v = str(value).strip()
    if not v:
        return ""
    return f"<dt>{label}</dt><dd><code>{_esc(v)}</code></dd>"


def _bat_status(s: str) -> str:
    return {"1":"Desconocido","2":"Cargando","3":"Descargando","4":"No cargando","5":"Llena"}.get(s, s)


def _bat_health(s: str) -> str:
    return {"1":"Desconocida","2":"Buena","3":"Sobrecalentamiento","4":"Muerta","5":"Sobre voltaje","6":"Fallo","7":"Fria"}.get(s, s)


def generate_html(device_id: str, props: dict, app_counts: dict,
                  state: dict, num_files: int, total_size: str, wa_result: dict) -> None:
    log("[6/8] Generando informe HTML...")
    now_str = datetime.now().strftime("%d/%m/%Y a las %H:%M:%S")
    year    = datetime.now().year
    bat     = state["battery"]
    nivel   = bat.get("level", "")

    # -- Cards --
    cards = (
        _card("phone",    "Modelo",        props["modelo"]) +
        _card("building", "Fabricante",    props["marca"]) +
        _card("layers",   "Android",       props["android_ver"], f"API {props['sdk']}") +
        _card("cog",      "Capa",          props["capa"]) +
        (_card("battery", "Bateria",       f"{nivel}%") if nivel else "") +
        _card("app",      "Apps usuario",  app_counts.get("apps_usuario.txt", 0)) +
        _card("cog",      "Apps sistema",  app_counts.get("apps_sistema.txt", 0)) +
        _card("bolt",     "Procesos",      state["num_procs"]) +
        _card("download", "Ficheros",      num_files) +
        _card("database", "Datos copiados",total_size)
    )

    # -- Device info --
    dev_rows = "".join([
        _row("Modelo",           props["modelo"]),
        _row("Fabricante",       props["marca"]),
        _row("Marca",            props["brand"]),
        _row("Codename",         props["device"]),
        _row("Numero de serie",  props["serie"]),
        _row("Arquitectura",     props["arq"]),
        _row("Bootloader",       props["bootloader"]),
        _row("Banda base",       props["radio"]),
        _row("Build ID",         props["build"]),
        _row("Capa del sistema", props["capa"]),
        _row("Parche seguridad", props["seguridad"]),
        _row("Kernel",           props["kernel"]),
        _row("Idioma sistema",   props["idioma"]),
        _row("Zona horaria",     props["timezone"]),
        _row("Huella sistema",   props["huella"]),
    ])

    # -- Hardware section --
    hw_section = ""
    if state["cpu_model"] or state["num_cores"] or state["ram_total"]:
        hw_rows = "".join([
            _row("CPU",       state["cpu_model"]),
            _row("Nucleos",   state["num_cores"] if state["num_cores"] else ""),
            _row("RAM total", state["ram_total"]),
        ])
        hw_section = (f'<section><h2>{_ICONS["chip"]} Hardware</h2>'
                      f'<dl class="info-grid">{hw_rows}</dl></section>')

    # -- Battery section --
    bat_section = ""
    if nivel:
        tr = bat.get("temperature", "")
        vr = bat.get("voltage", "")
        bat_rows = "".join([
            _row("Nivel",       f"{nivel}%"),
            _row("Estado",      _bat_status(bat.get("status", ""))),
            _row("Salud",       _bat_health(bat.get("health", ""))),
            _row("Temperatura", f"{int(tr)/10:.1f} grados" if tr.isdigit() else ""),
            _row("Voltaje",     f"{int(vr)/1000:.2f} V" if vr.isdigit() else ""),
        ])
        bat_section = (f'<section><h2>{_ICONS["battery"]} Estado de la bateria</h2>'
                       f'<dl class="info-grid">{bat_rows}</dl></section>')

    # -- Network section --
    net_section = ""
    if state["ip_wifi"]:
        net_section = (f'<section><h2>{_ICONS["wifi"]} Conectividad</h2>'
                       f'<dl class="info-grid">{_row("IP WiFi", state["ip_wifi"])}</dl></section>')

    # -- Storage section --
    storage_section = ""
    if num_files > 0:
        folder_rows = ""
        if ALMACEN.exists():
            for d in sorted(ALMACEN.iterdir()):
                if d.is_dir():
                    try:
                        flist = list(d.rglob("*"))
                        sz  = sum(f.stat().st_size for f in flist if f.is_file())
                        cnt = sum(1 for f in flist if f.is_file())
                        folder_rows += f"<tr><td>{_esc(d.name)}</td><td>{_human(sz)}</td><td>{cnt}</td></tr>"
                    except Exception:
                        pass
        storage_section = (
            f'<section><h2>{_ICONS["database"]} Almacenamiento extraido</h2>'
            f'<p style="margin-bottom:1rem">Total: <strong>{total_size}</strong> en <strong>{num_files}</strong> archivos.</p>'
            f'<div class="table-wrap"><table>'
            f'<thead><tr><th>Carpeta</th><th>Tamano</th><th>Ficheros</th></tr></thead>'
            f'<tbody>{folder_rows}</tbody></table></div></section>'
        )

    # -- Apps section --
    apps_section = ""
    num_user = app_counts.get("apps_usuario.txt", 0)
    apps_path = DATOS / "apps_usuario.txt"
    if num_user > 0 and apps_path.exists():
        app_rows = ""
        count = 0
        for line in apps_path.read_text(encoding="utf-8").splitlines():
            if count >= 100:
                break
            if "=" in line:
                pkg  = _esc(line.split("=")[-1].strip())
                ruta = _esc(line.replace("package:", "").split("=")[0].strip())
                app_rows += f"<tr><td>{pkg}</td><td><code>{ruta}</code></td></tr>"
                count += 1
        apps_section = (
            f'<section><h2>{_ICONS["app"]} Aplicaciones de usuario '
            f'<span class="badge">{num_user} instaladas</span></h2>'
            f'<p style="margin-bottom:1rem;color:var(--text-dim);font-size:.9rem">'
            f'Listado completo en <code>datos_forenses/apps_usuario.txt</code>.</p>'
            f'<div class="table-wrap"><table>'
            f'<thead><tr><th>Paquete</th><th>Ruta del APK</th></tr></thead>'
            f'<tbody>{app_rows}</tbody></table></div></section>'
        )

    # -- WhatsApp section --
    wa_section = ""
    if wa_result["status"] == "ok":
        db_items = "".join(
            f'<li>{_ICONS["database"]}<code>{_esc(f)}</code></li>'
            for f in wa_result.get("db_files", [])
        )
        wa_section = (
            f'<section><h2>{_ICONS["wa"]} WhatsApp '
            f'<span class="badge" style="background:var(--green)">Extraido</span></h2>'
            f'<p style="margin-bottom:1rem;color:var(--text-dim);font-size:.9rem">'
            f'Base de datos extraida mediante tecnica de backup legacy.</p>'
            f'<ul class="archivos">{db_items}</ul></section>'
        )
    elif wa_result["status"] == "error":
        wa_section = (
            f'<section><h2>{_ICONS["wa"]} WhatsApp '
            f'<span class="badge" style="background:#da3633">Error</span></h2>'
            f'<p style="color:var(--text-dim)">{_esc(wa_result.get("reason",""))}</p></section>'
        )
    else:
        wa_section = (
            f'<section><h2>{_ICONS["wa"]} WhatsApp '
            f'<span class="badge" style="background:#6e7681">Omitido</span></h2>'
            f'<p style="color:var(--text-dim)">{_esc(wa_result.get("reason",""))}</p></section>'
        )

    # -- Generated files section --
    file_items = "".join(
        f'<li>{_ICONS["file"]}<code>datos_forenses/{_esc(f.name)}</code></li>'
        for f in sorted(DATOS.glob("*.txt"))
    )
    file_items += f'<li>{_ICONS["folder"]}<code>almacenamiento_interno/</code></li>'
    file_items += f'<li>{_ICONS["file"]}<code>hashes.sha256</code></li>'
    files_section = (
        f'<section><h2>{_ICONS["folder"]} Archivos generados</h2>'
        f'<ul class="archivos">{file_items}</ul></section>'
    )

    # -- Assemble --
    # NOTE: _CSS is a plain string (not f-string) so its CSS braces are safe
    html = (
        f'<!DOCTYPE html>\n<html lang="es">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<title>Informe Forense Android - {FECHA}</title>\n'
        f'<style>{_CSS}</style>\n</head>\n<body>\n<div class="container">\n\n'
        f'<header>\n'
        f'  <h1>{_ICONS["phone"]} Informe Forense Android</h1>\n'
        f'  <p>Generado el {now_str} &middot; ID dispositivo: <code>{_esc(device_id)}</code></p>\n'
        f'</header>\n\n'
        f'<div class="grid">{cards}</div>\n\n'
        f'<section><h2>{_ICONS["search"]} Identificacion del dispositivo</h2>'
        f'<dl class="info-grid">{dev_rows}</dl></section>\n\n'
        f'{hw_section}\n{bat_section}\n{net_section}\n'
        f'{storage_section}\n{apps_section}\n{wa_section}\n{files_section}\n\n'
        f'<footer>Informe generado automaticamente por forense_android.py<br>'
        f'Deltadri &middot; {year}</footer>\n\n'
        f'</div>\n</body>\n</html>'
    )

    INFORME.write_text(html, encoding="utf-8")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backup forense Android + extraccion WhatsApp"
    )
    parser.add_argument(
        "--skip-wa", action="store_true",
        help="Omitir extraccion de WhatsApp (no desinstala ni reinicia el dispositivo)"
    )
    args = parser.parse_args()

    check_prerequisites()
    print()

    device_id = check_device()

    BASE.mkdir(parents=True, exist_ok=True)
    ALMACEN.mkdir(parents=True, exist_ok=True)
    DATOS.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 65)
    print(" BACKUP + INFORME FORENSE ANDROID")
    print(f" Dispositivo: {device_id}")
    print(f" Destino:     {BASE}")
    print("=" * 65)
    print()

    props      = identify_device()        # [1/8]
    pull_storage()                        # [2/8]
    app_counts = list_apps()             # [3/8]
    state      = system_state()          # [4/8]

    log("[5/8] Extrayendo WhatsApp...")
    if args.skip_wa:
        wa_result = {"status": "skipped", "reason": "Omitido con --skip-wa"}
        log("[WA]  Omitido con --skip-wa")
    else:
        wa_result = extract_whatsapp(props["sdk"])

    # Inventario despues de toda la extraccion: el HTML muestra cifras reales
    num_files, total_size = _inventory()

    generate_html(device_id, props, app_counts, state, num_files, total_size, wa_result)  # [6/8]
    _write_hashes()                      # [7/8] — cubre WA, informe y todo lo extraido

    log("[8/8] Listo.")
    (BASE / "log.txt").write_text("\n".join(LOG_LINES), encoding="utf-8")

    print()
    print("=" * 65)
    print(" COMPLETADO")
    print("=" * 65)
    print(f" Carpeta:  {BASE}")
    print(f" Informe:  {INFORME}")
    print(f" Tamano:   {total_size} ({num_files} archivos)")
    print(f" Hashes:   {HASHES}")
    print()
    open_cmd = "start" if sys.platform == "win32" else ("open" if sys.platform == "darwin" else "xdg-open")
    print(" Abrir informe:")
    print(f'   {open_cmd} "{INFORME}"')
    print("=" * 65)


if __name__ == "__main__":
    main()
