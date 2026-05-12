#!/usr/bin/env python3
"""
Forensic Android Backup + WhatsApp Extractor
Autor: Deltadri
Compatible: Android 8-15+, cualquier fabricante

Dependencias obligatorias: adb en PATH
Dependencias opcionales (extraccion WhatsApp):
  - abe/abe.jar              (Android Backup Extractor)
  - legacy_apk/*.apk         (uno o varios APKs candidatos para downgrade)
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

WA_DIR          = BASE / "whatsapp"
WA_BACKUP       = WA_DIR / "whatsapp.ab"          # metodo legacy: backup .ab
WA_EXTRACT      = WA_DIR / "extracted"            # metodo legacy: .ab descomprimido
WA_APKS_DIR     = WA_DIR / "apks_originales"      # APKs originales (para restaurar)
WA_EXTERNAL_DIR = WA_DIR / "external"             # metodo crypt15: pull de /sdcard/Android/media/com.whatsapp/
WA_DECRYPTED    = WA_DIR / "decrypted"            # metodo crypt15: descifrados con la clave del usuario

_HERE      = Path(__file__).parent
LEGACY_DIR = _HERE / "legacy_apk"   # carpeta con uno o varios APKs candidatos
ABE_JAR    = _HERE / "abe" / "abe.jar"

LOG_LINES: list[str] = []

# Serial del dispositivo escogido por check_device(). Una vez fijado, TODOS los
# comandos adb (excepto kill-server/start-server, que son del host) llevan
# "-s <serial>" para que multi-device nunca afecte al script.
_DEVICE_SERIAL: str | None = None


def _adb_base() -> list[str]:
    """Comando base 'adb' o 'adb -s <serial>' segun haya dispositivo elegido."""
    if _DEVICE_SERIAL:
        return ["adb", "-s", _DEVICE_SERIAL]
    return ["adb"]


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
    """Run `adb shell <cmd>` on the selected device. Empty string on failure."""
    args = _adb_base() + ["shell"] + (cmd if isinstance(cmd, list) else cmd.split())
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        return r.stdout.replace("\r", "").strip()
    except Exception:
        return ""


def adb_run(args: list, timeout: int = 60) -> tuple[bool, str, str]:
    """Run `adb <args>` on the selected device. Returns (success, stdout, stderr)."""
    try:
        r = subprocess.run(_adb_base() + args, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except Exception as e:
        return False, "", str(e)


def get_prop(prop: str) -> str:
    return adb_shell(["getprop", prop])


def _adb_authorized() -> bool:
    """Probe real: ejecuta 'adb shell echo ok' y comprueba autorizacion efectiva.

    No basta con 'adb devices' diciendo 'device' — en Android 14/15 (sobre todo
    Realme/OPPO/OnePlus/Vivo) el estado puede reportarse 'device' por cache de
    adbd 5-10 s despues de un reboot mientras la autorizacion USB ya esta
    invalidada. Solo un comando real lo confirma.
    """
    try:
        r = subprocess.run(
            _adb_base() + ["shell", "echo", "ok"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=10,
        )
    except Exception:
        return False
    combined = (r.stdout + r.stderr).lower()
    if "unauthorized" in combined or "device offline" in combined or "no devices" in combined:
        return False
    return r.returncode == 0 and "ok" in r.stdout


def _wait_for_auth(context: str = "", timeout_s: int = 90) -> bool:
    """Bloquea hasta que el dispositivo esta autorizado para comandos adb.

    Si ya lo esta, devuelve True inmediatamente (sin tocar nada — esencial para
    no penalizar Android 8-13 donde la autorizacion no se pierde).

    Si no lo esta, reinicia el adb server (forza el redialogo RSA), muestra
    una cuenta atras al usuario, y polleea cada 2 s hasta autorizar o timeout.

    Util tras 'adb reboot' (Android 14/15 + BBK invalidan la autorizacion) o
    cuando un comando intermedio reporta 'device unauthorized'.
    """
    if _adb_authorized():
        return True

    label = f" ({context})" if context else ""
    log(f"[WA]  Dispositivo NO autorizado{label}.")
    log("        Reinicio adb server para forzar reaparicion del dialogo RSA en el movil...")
    try:
        subprocess.run(["adb", "kill-server"], capture_output=True, timeout=10)
    except Exception:
        pass
    time.sleep(2)
    try:
        subprocess.run(["adb", "start-server"], capture_output=True, timeout=10)
    except Exception:
        pass
    time.sleep(2)

    log("[WA]  En el movil: acepta la huella RSA y marca 'Permitir siempre desde este ordenador'.")
    log(f"[WA]  Esperando hasta {timeout_s}s a que aceptes el dialogo...")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _adb_authorized():
            print()
            log("[WA]  Dispositivo autorizado. Continuando...")
            return True
        remaining = int(deadline - time.time())
        print(f"\r[WA]  Esperando autorizacion... {remaining:3d}s ", end="", flush=True)
        time.sleep(2)
    print()
    log(f"[ERROR] Timeout ({timeout_s}s) esperando autorizacion del dispositivo.")
    log("        Si el dialogo nunca aparecio: en el movil ve a Opciones de desarrollador")
    log("        -> 'Revocar autorizaciones de depuracion USB' y reconecta el cable.")
    return False


# ---------------------------------------------------------------------------
# 1. PREREQUISITES
# ---------------------------------------------------------------------------

def _adb_platform_tools_version() -> int:
    """Devuelve la version mayor de platform-tools, o 0 si no se puede determinar."""
    try:
        r = subprocess.run(["adb", "--version"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            s = line.strip()
            if s.lower().startswith("version "):
                return int(s.split()[1].split(".")[0])
    except Exception:
        pass
    return 0


def check_prerequisites() -> None:
    log("[*] Comprobando herramientas necesarias...")
    if shutil.which("adb") is None:
        log("[ERROR] 'adb' no encontrado en PATH.")
        log("        Instala android-tools / android-platform-tools.")
        sys.exit(1)
    ver = subprocess.run(["adb", "--version"], capture_output=True, text=True)
    first_line = ver.stdout.splitlines()[0] if ver.stdout else "adb"
    log(f"    OK - {first_line}")

    pt = _adb_platform_tools_version()
    if pt == 0:
        log("    [AVISO] No se pudo determinar la version de platform-tools")
    elif pt < 34:
        log(f"    [AVISO] platform-tools v{pt} es antiguo (Android 14+ requiere >= 34 para")
        log( "            'adb install --bypass-low-target-sdk-block'). El script usa fallback")
        log( "            via 'adb shell pm install' que NO necesita adb moderno.")
    else:
        log(f"    [INFO] platform-tools v{pt} (>= 34, soporta --bypass-low-target-sdk-block)")

# ---------------------------------------------------------------------------
# 2. DEVICE
# ---------------------------------------------------------------------------

def check_device(prefer_serial: str | None = None) -> str:
    """Selecciona el dispositivo objetivo y fija _DEVICE_SERIAL globalmente.

    Fail-hard si hay >1 dispositivo conectado y no se especifica --device <serial>.
    En forense **nunca** queremos que el script opere sobre el dispositivo
    equivocado por azar del orden de `adb devices`.
    """
    global _DEVICE_SERIAL

    # start-server es del host, no depende de serial — bypasea _adb_base().
    subprocess.run(["adb", "start-server"], capture_output=True)

    # Devices listing tambien va sin -s (es global del host).
    try:
        r = subprocess.run(["adb", "devices"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=15)
        out = r.stdout.strip()
        ok = r.returncode == 0
    except Exception as e:
        log(f"[ERROR] No se pudo ejecutar adb devices: {e}")
        sys.exit(1)
    if not ok:
        log("[ERROR] No se pudo ejecutar adb devices.")
        sys.exit(1)

    lines = [l for l in out.splitlines()[1:] if l.strip()]
    devices = [l.split()[0] for l in lines if l.split()[-1] == "device"]
    unauth  = [l.split()[0] for l in lines if "unauthorized" in l]

    if unauth and not devices:
        log("[ERROR] Dispositivo NO autorizado. Acepta la huella RSA en el movil")
        log("        y marca 'Permitir siempre desde este ordenador'.")
        sys.exit(1)
    if not devices:
        log("[ERROR] No hay ningun dispositivo conectado en estado 'device'.")
        log("        Comprueba cable de datos, Depuracion USB activada y modo MTP.")
        sys.exit(1)
    if len(devices) > 1 and not prefer_serial:
        log(f"[ERROR] {len(devices)} dispositivos conectados. Especifica cual con --device <serial>:")
        for d in devices:
            log(f"        - {d}")
        sys.exit(1)

    chosen = prefer_serial or devices[0]
    if chosen not in devices:
        log(f"[ERROR] Serial '{chosen}' no esta entre los dispositivos disponibles: {devices}")
        sys.exit(1)

    _DEVICE_SERIAL = chosen
    log(f"[*] Dispositivo: {chosen}")
    return chosen

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

def detect_oem_quirks(props: dict) -> dict:
    """
    Avisa sobre limitaciones conocidas del OEM/Android para la extraccion WhatsApp.
    No bloquea nada — solo informa. Devuelve {"warnings": [...], "preflight": [...]}.
    """
    quirks = {"warnings": [], "preflight": []}
    marca = (props.get("marca") or "").lower()
    brand = (props.get("brand") or "").lower()
    capa  = (props.get("capa")  or "").lower()
    try:
        sdk = int(props.get("sdk", "0") or 0)
    except (ValueError, TypeError):
        sdk = 0

    # Familia BBK: Realme/OPPO/OnePlus/Vivo — tienen Permission Monitoring activado
    bbk = ("realme", "oppo", "oneplus", "vivo")
    if any(b in marca or b in brand or b in capa for b in bbk):
        quirks["warnings"].append(
            "Dispositivo BBK detectado (Realme/OPPO/OnePlus/Vivo): tiene 'Permission Monitoring' "
            "que bloquea instalaciones con bypass via ADB."
        )
        quirks["preflight"].append(
            "En el movil: Ajustes -> Ajustes adicionales -> Opciones de desarrollador -> "
            "activar 'Desactivar monitor de permisos' (Disable Permission Monitoring) y luego "
            "togglear USB Debugging off/on para aplicar el cambio."
        )

    # Huawei/Honor — EMUI bloquea adb backup desde EMUI 9
    if "huawei" in marca or "honor" in marca or "emui" in capa or "harmony" in capa:
        quirks["warnings"].append(
            "Huawei/Honor detectado: EMUI bloquea o devuelve vacio el adb backup desde EMUI 9. "
            "La extraccion legacy probablemente devolvera un .ab vacio o no mostrara el dialogo "
            "de backup. Documentado por Oxygen Forensics y Belkasoft. Metodo no fiable aqui."
        )

    # Xiaomi/MIUI/HyperOS — restricciones de instalacion
    if "xiaomi" in marca or "miui" in capa or "hyperos" in capa:
        quirks["warnings"].append(
            "Xiaomi/MIUI/HyperOS detectado: requiere activar 'Instalar via USB' y "
            "'Depuracion USB (Ajustes de seguridad)' en Opciones de desarrollador "
            "(necesita SIM y conexion a internet del operador chino o cuenta Mi)."
        )
        quirks["preflight"].append(
            "Activar 'Install via USB' y 'USB debugging (Security settings)' en Opciones de desarrollador."
        )

    # Android 14+ (SDK 34+) — requiere bypass para targetSdk < 24
    if sdk >= 34:
        quirks["warnings"].append(
            f"Android {props.get('android_ver','?')} (SDK {sdk}): exige targetSdk >= 24. "
            "El APK legacy tiene targetSdk=19. El script intentara 3 estrategias de bypass "
            "(adb install + flag, adb shell pm install + flag, adb install sin flag)."
        )
        quirks["preflight"].append(
            "Android 14/15 invalida la autorizacion USB tras 'adb reboot' si no marcaste "
            "'Permitir siempre desde este ordenador' al aceptar la huella RSA. Asegurate "
            "de tenerla marcada antes de empezar (o ten el movil a mano para reaceptar)."
        )

    # Android 12+ (SDK 31+) — excluye datos para apps targetSdk >= 31, pero el legacy tiene 19
    if sdk >= 31:
        quirks["warnings"].append(
            "Android 12+: adb backup excluye datos de apps con targetSdk >= 31. "
            "El APK legacy tiene targetSdk=19, por lo que sus datos SI se incluyen."
        )

    return quirks


def _wa_detect_compatibility(props: dict) -> dict:
    """Diagnostico previo: indica si el metodo LEGACY puede funcionar.

    Lee del dispositivo:
      - Version de Android (de props['sdk'])
      - targetSdk del WhatsApp actualmente instalado (dumpsys package com.whatsapp)
      - allowBackup del WhatsApp actual (flag en dumpsys)
      - versionName del WhatsApp actual

    Decide si el metodo legacy (instalar WA viejo + adb backup) tiene alguna
    posibilidad de funcionar:
      - Falla en Android >= 14 (SDK 34+) si WA actual tiene targetSdk >= 23
        -> INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE bloquea el install legacy
      - Falla siempre en Huawei EMUI 9+ (adb backup devuelve .ab vacio)
      - Funciona en el resto (Android 6-13, OEMs no-EMUI, etc.)

    Devuelve dict con todas las metricas + boolean 'legacy_viable' + 'reason' si no lo es.
    """
    result: dict = {
        "legacy_viable": False,
        "reason_legacy_blocked": None,
        "wa_target_sdk": None,
        "wa_allow_backup": None,
        "wa_version": None,
        "android_sdk": 0,
        "android_version": props.get("android_ver", "?"),
        "is_huawei_emui9": False,
    }
    try:
        result["android_sdk"] = int(props.get("sdk", 0) or 0)
    except (ValueError, TypeError):
        pass

    # Huawei/EMUI 9+ bloquea adb backup en silencio
    marca = (props.get("marca") or "").lower()
    capa  = (props.get("capa")  or "").lower()
    if any(k in marca for k in ("huawei", "honor")) or "emui" in capa:
        result["is_huawei_emui9"] = True

    # Leer datos de WA desde el dispositivo
    out = adb_shell(["dumpsys", "package", "com.whatsapp"], timeout=20)
    if not out:
        result["reason_legacy_blocked"] = (
            "No se pudo ejecutar 'dumpsys package com.whatsapp' "
            "(WhatsApp puede no estar instalado o el OEM bloquea pm/dumpsys)."
        )
        return result

    import re
    m = re.search(r"targetSdk=(\d+)", out)
    if m:
        result["wa_target_sdk"] = int(m.group(1))
    m = re.search(r"versionName=(\S+)", out)
    if m:
        result["wa_version"] = m.group(1)

    # allowBackup aparece como flag en dumpsys: si el manifest lo declara true
    # (o no lo declara y el default es true), saldra como flag 'ALLOW_BACKUP'
    # en la linea 'flags=[...]'. Si esta false, no aparece.
    m = re.search(r"flags=\[([^\]]*)\]", out)
    if m:
        flags = m.group(1)
        result["wa_allow_backup"] = "ALLOW_BACKUP" in flags

    # ---- Reglas de viabilidad ----
    if result["is_huawei_emui9"]:
        result["reason_legacy_blocked"] = (
            "Huawei/EMUI 9+ bloquea 'adb backup' silenciosamente y devuelve un .ab "
            "vacio (solo cabecera, sin datos). Documentado por Oxygen Forensics y Belkasoft."
        )
        return result

    if (result["wa_target_sdk"] is not None
            and result["wa_target_sdk"] >= 23
            and result["android_sdk"] >= 34):
        result["reason_legacy_blocked"] = (
            f"Android {result['android_version']} (SDK {result['android_sdk']}) + "
            f"WhatsApp targetSdk={result['wa_target_sdk']}: el install del APK legacy "
            f"(targetSdk=19) sera rechazado por Android con "
            "INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE. No hay flag adb que lo sortee."
        )
        return result

    result["legacy_viable"] = True
    return result


def _wa_log_diagnostic(diag: dict) -> None:
    """Imprime el diagnostico de compatibilidad en bloque, formato tabla."""
    log("[WA]  Diagnostico de compatibilidad:")
    log(f"      Android version:   {diag['android_version']} (SDK {diag['android_sdk']})")
    log(f"      WhatsApp version:  {diag.get('wa_version') or '?'}")
    log(f"      WA targetSdk:      {diag.get('wa_target_sdk') if diag.get('wa_target_sdk') is not None else '?'}")
    ab = diag.get("wa_allow_backup")
    ab_str = "true" if ab is True else ("false" if ab is False else "?")
    log(f"      WA allowBackup:    {ab_str}")
    if diag["legacy_viable"]:
        log("      Metodo LEGACY:     ✓ viable en este dispositivo")
    else:
        log("      Metodo LEGACY:     ✗ NO viable")
        log(f"        Razon: {diag['reason_legacy_blocked']}")


def _wa_legacy_apks() -> list[Path]:
    """Devuelve la lista ordenada (alfabetica case-SENSITIVE) de APKs legacy.

    El usuario coloca uno o varios APKs en legacy_apk/ y controla el orden
    de intento con prefijos numericos en el nombre. Ejemplo:

        legacy_apk/
          01_WhatsApp_2.19.151.apk   <- se intenta primero (targetSdk 28)
          02_WhatsApp_2.16.396.apk   <- fallback (targetSdk 23)
          LegacyWhatsApp.apk         <- ultimo recurso

    Orden case-sensitive ASCII para que sea identico en Linux y Windows:
    'L' (codepoint 76) < 'c' (99), asi que 'LegacyWhatsApp.apk' SIEMPRE va
    antes que 'com.whatsapp_*.apk' independientemente del SO. Esto preserva
    el flujo del OPPO Android 14 (donde la 2.11.431 historica instalaba y
    backupeaba correctamente) — la 2.12.535 solo entra si la 2.11.431 falla.

    Sin esta ordenacion explicita, Path.__lt__ en Windows hace lowercase
    (case-insensitive) y cambia el orden, lo que en testing daba salida
    distinta a la de produccion en Linux.
    """
    folder = LEGACY_DIR
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.glob("*.apk") if p.is_file()), key=lambda p: p.name)


def _wa_prereqs() -> tuple[bool, str]:
    if not _wa_legacy_apks():
        return False, f"No hay ningun .apk en {LEGACY_DIR}/"
    if not ABE_JAR.exists():
        return False, f"No existe {ABE_JAR}"
    if shutil.which("java") is None:
        return False, "java no esta instalado"
    return True, ""


def _wa_installed() -> bool:
    return "package:" in adb_shell(["pm", "path", "com.whatsapp"], timeout=10)


def _wa_ghost_state() -> bool:
    """Detecta el estado 'uninstalled-keep-data' (residuo de 'pm uninstall -k')
    ESPECIFICAMENTE para com.whatsapp (no para WhatsApp Business com.whatsapp.w4b).

    En este estado:
      - 'pm path com.whatsapp'                          -> vacio
      - 'pm list packages -u' incluye 'package:com.whatsapp' (no w4b)
        (-u lista paquetes con DELETE_KEEP_DATA)

    Lo deja un run anterior que fallo entre el 'pm uninstall -k' y el
    'install-multiple' de restauracion. Los datos en /data/data/com.whatsapp/
    siguen intactos y se recuperan reinstalando los APKs originales
    (via --restore-wa <ruta a apks_originales/>).

    Si el usuario solo tuvo WhatsApp Business (com.whatsapp.w4b) y nunca WA
    normal, _wa_installed() devolvera False pero esto tambien devolvera False:
    no es un ghost state de WhatsApp; es simplemente otro paquete.
    """
    if _wa_installed():
        return False
    out = adb_shell(["pm", "list", "packages", "-u"], timeout=10)
    for line in out.splitlines():
        # Match exacto: NO confundir com.whatsapp con com.whatsapp.w4b
        if line.strip() == "package:com.whatsapp":
            return True
    return False


def _wa_get_apk_paths() -> list[str]:
    out = adb_shell(["pm", "path", "com.whatsapp"], timeout=10)
    return [l.replace("package:", "").strip() for l in out.splitlines()
            if l.startswith("package:")]


def _wa_pull_apks(apk_paths: list[str]) -> list[str] | None:
    WA_APKS_DIR.mkdir(parents=True, exist_ok=True)
    local = []
    for p in apk_paths:
        dst = WA_APKS_DIR / Path(p).name
        ok, out, err = adb_run(["pull", p, str(dst)], timeout=60)
        if not ok:
            log(f"[ERROR] adb pull {p} fallo: stdout='{out.strip()}' stderr='{err.strip()}'")
            return None
        if not dst.exists() or dst.stat().st_size == 0:
            log(f"[ERROR] adb pull devolvio OK pero el fichero local esta ausente o vacio: {dst}")
            return None
        log(f"[WA]      pulled {Path(p).name} ({dst.stat().st_size:,} bytes)")
        local.append(str(dst))
    return local


def _wa_reboot_wait() -> bool:
    log("[WA]  Reiniciando dispositivo (refresca PackageManager tras uninstall -k)...")
    adb_run(["reboot"], timeout=10)
    time.sleep(25)
    log("[WA]  Esperando hasta 3 min a que el dispositivo vuelva...")
    last_state = "?"
    stable_device = 0  # lecturas consecutivas en estado 'device'
    for attempt in range(36):
        time.sleep(5)
        ok, out, _ = adb_run(["devices"], timeout=10)
        if not ok:
            stable_device = 0
            continue
        current_state = ""
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            current_state = parts[1]
            last_state = current_state
            break

        if current_state == "device":
            stable_device += 1
            # No basta con la primera lectura 'device': Android 14/15 + BBK pueden
            # reportar 'device' por cache de adbd unos segundos antes de pasar a
            # 'unauthorized'. Exigimos 2 lecturas (~5s aparte) + probe real.
            if stable_device >= 2:
                if _adb_authorized():
                    log(f"[WA]  Dispositivo listo y autorizado (tras ~{25 + (attempt + 1) * 5}s)")
                    return True
                # adb reporta 'device' pero los comandos fallan: la autorizacion
                # USB se invalido durante el reboot. Pedimos reautorizar.
                log("[WA]  Estado 'device' pero adbd rechaza comandos (auth invalidada en reboot).")
                return _wait_for_auth("post-reboot")
        elif current_state == "unauthorized":
            log("[WA]  Estado 'unauthorized' tras reinicio.")
            return _wait_for_auth("post-reboot")
        else:
            # offline, recovery, sideload, etc -> seguir esperando
            stable_device = 0
    log(f"[ERROR] Timeout (3 min) esperando que el dispositivo vuelva (ultimo estado: {last_state})")
    return False


def _wa_try_install_one(apk: Path) -> bool:
    """Intenta instalar UN APK legacy concreto con 3 estrategias escalonadas.

    Estrategia 1: adb install --bypass-low-target-sdk-block (host-side).
                  Requiere platform-tools >= 34.
    Estrategia 2: adb push + adb shell pm install --bypass-low-target-sdk-block
                  (device-side parsing del flag). Funciona con cualquier adb del
                  host siempre que Android >= 14.
    Estrategia 3: adb install sin bypass. Solo funciona en Android <= 13.

    Devuelve True si ALGUNA de las 3 cuela. False si todas fallan.
    """
    def _success(out: str, err: str) -> bool:
        # pm install / adb install imprimen 'Success' en stdout cuando va bien.
        return "Success" in out or "Success" in err

    # --- Estrategia 1 ---
    log(f"[WA]    Estrategia 1/3: adb install --bypass-low-target-sdk-block (host-side)")
    ok1, out1, err1 = adb_run(
        ["install", "-r", "-d", "--bypass-low-target-sdk-block", str(apk)],
        timeout=60,
    )
    log(f"[WA]        rc_ok={ok1}  stdout='{out1.strip()}'  stderr='{err1.strip()}'")
    if ok1 and _success(out1, err1):
        log(f"[WA]    OK - {apk.name} instalado (estrategia 1)")
        return True

    # --- Estrategia 2 ---
    log(f"[WA]    Estrategia 2/3: adb push + adb shell pm install (device-side)")
    # Nombre remoto fijo y saneado: el nombre original del APK puede contener
    # caracteres que rompen el shell de Android (parentesis, comas, espacios,
    # `;`, etc.). Cualquier APK origen vale, en el device queda como nombre
    # canonico que no se evalua por sh.
    remote = "/data/local/tmp/wa_legacy_candidate.apk"
    okp, outp, errp = adb_run(["push", str(apk), remote], timeout=60)
    log(f"[WA]        push rc_ok={okp}  stdout='{outp.strip()}'  stderr='{errp.strip()}'")
    if okp:
        ok2, out2, err2 = adb_run(
            ["shell", "pm", "install", "-r", "-d", "--bypass-low-target-sdk-block", remote],
            timeout=60,
        )
        log(f"[WA]        pm install rc_ok={ok2}  stdout='{out2.strip()}'  stderr='{err2.strip()}'")
        adb_run(["shell", "rm", "-f", remote], timeout=10)
        if ok2 and _success(out2, err2):
            log(f"[WA]    OK - {apk.name} instalado (estrategia 2, device-side bypass)")
            return True
    else:
        log(f"[WA]        (no se intenta pm install porque el push fallo)")

    # --- Estrategia 3 ---
    log(f"[WA]    Estrategia 3/3: adb install SIN bypass (solo Android <= 13)")
    ok3, out3, err3 = adb_run(["install", "-r", "-d", str(apk)], timeout=60)
    log(f"[WA]        rc_ok={ok3}  stdout='{out3.strip()}'  stderr='{err3.strip()}'")
    if ok3 and _success(out3, err3):
        log(f"[WA]    OK - {apk.name} instalado (estrategia 3, sin bypass)")
        return True

    return False


def _wa_install_legacy(sdk: str) -> bool:
    """Itera sobre todos los APKs legacy candidatos en legacy_apk/ y prueba cada
    uno con _wa_try_install_one. Devuelve True en cuanto alguno cuela.

    El orden de intento es alfabetico — el usuario controla la prioridad con
    prefijos numericos en los nombres (ver _wa_legacy_apks). Estrategia tipica
    en Android 14/15: meter primero un APK con targetSdk >= 23 (para sortear
    INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE) y dejar el LegacyWhatsApp.apk
    historico (targetSdk=19) como ultimo recurso para Androids antiguos.
    """
    candidates = _wa_legacy_apks()
    if not candidates:
        log(f"[ERROR] No hay APKs en {LEGACY_DIR}/")
        return False

    log(f"[WA]  {len(candidates)} APK(s) legacy candidatos en orden de intento:")
    for c in candidates:
        log(f"      - {c.name} ({c.stat().st_size:,} bytes)")

    for idx, apk in enumerate(candidates, 1):
        log(f"\n[WA]  Candidato {idx}/{len(candidates)}: {apk.name}")
        if _wa_try_install_one(apk):
            return True
        if idx < len(candidates):
            log(f"[WA]  Candidato {idx} fallo, probando siguiente...")

    log("[ERROR] Todos los candidatos legacy fallaron. Causas tipicas:")
    log("        - INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE (-26): el dispositivo tuvo un WA")
    log("          con targetSdk >= 23 (Android 14/15 + WA moderno). Anade a legacy_apk/ un")
    log("          APK candidato con targetSdk >= 23 Y allowBackup=true. Renombra con prefijo")
    log("          '01_' para que se pruebe antes que el LegacyWhatsApp.apk historico.")
    log("        - INSTALL_FAILED_UPDATE_INCOMPATIBLE: firma distinta. Solo APKs oficiales")
    log("          de Meta funcionan. Cert SHA-256 esperado:")
    log("            3987d043d10aefaf5a8710b3671418fe57e0e19b653c9df82558feb5ffce5d44")
    log("          Los MODs (GBWhatsApp, FMWhatsApp, etc.) NO funcionan.")
    log("        - Android 14+ BBK (Realme/OPPO/OnePlus/Vivo): activa 'Desactivar monitor de")
    log("          permisos' en Opciones de desarrollador y togglea USB Debugging off/on.")
    log("        - Android 14+ Xiaomi/MIUI: activa 'Install via USB' y 'USB debugging (Security")
    log("          settings)' (requiere SIM o cuenta Mi).")
    log("        - INSTALL_FAILED_VERIFICATION_FAILURE: desactiva 'Verify apps over USB' o")
    log("          ejecuta 'adb shell settings put global verifier_verify_adb_installs 0'.")
    return False


def _wa_backup() -> bool:
    WA_DIR.mkdir(parents=True, exist_ok=True)
    log("[WA]  Abriendo WhatsApp legacy...")
    ok_open, out_open, err_open = adb_run(
        ["shell", "am", "start", "-n", "com.whatsapp/.Main"], timeout=10
    )
    log(f"[WA]      am start rc_ok={ok_open}  stdout='{out_open.strip()}'  stderr='{err_open.strip()}'")

    log("[WA]  Esperando 30 segundos a que cargue (si pide permisos, acepta en el movil)...")
    for i in range(30, 0, -1):
        print(f"\r[WA]  Arrancando WhatsApp... {i:2d}s ", end="", flush=True)
        time.sleep(1)
    print()

    log("[WA]  Ejecutando 'adb backup -f whatsapp.ab com.whatsapp'...")
    log("[WA]  >>> En el movil DEBE aparecer un dialogo: pulsa 'Hacer copia de seguridad' (3 min de margen) <<<")
    log("[WA]      Si NO aparece dialogo: el OEM probablemente lo bloquea (Huawei/EMUI suele hacerlo).")
    log("[WA]      Si pide contrasena de backup: introducela en el movil (debe estar configurada antes).")

    backup_stdout, backup_stderr = "", ""
    proc = None
    try:
        proc = subprocess.Popen(
            ["adb", "backup", "-f", str(WA_BACKUP), "com.whatsapp"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            out_b, err_b = proc.communicate(timeout=180)
            backup_stdout = out_b.decode("utf-8", errors="replace").strip()
            backup_stderr = err_b.decode("utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired:
            proc.kill()
            log("[ERROR] Timeout (180s) en adb backup.")
            log("        El dialogo no se acepto, no aparecio (OEM bloqueado), o el backup no progreso.")
            return False
    except FileNotFoundError:
        log("[ERROR] 'adb' no encontrado al lanzar adb backup")
        return False
    except Exception as e:
        log(f"[ERROR] Excepcion lanzando adb backup: {e}")
        return False

    rc = proc.returncode if proc else -1
    log(f"[WA]      adb backup rc={rc}")
    if backup_stdout:
        log(f"[WA]      stdout: {backup_stdout}")
    if backup_stderr:
        log(f"[WA]      stderr: {backup_stderr}")

    if not WA_BACKUP.exists():
        log(f"[ERROR] El archivo .ab no se ha creado: {WA_BACKUP}")
        return False

    size = WA_BACKUP.stat().st_size
    log(f"[WA]      tamano de whatsapp.ab: {size:,} bytes")

    if size == 0:
        log("[ERROR] El backup tiene 0 bytes — el OEM bloqueo el comando silenciosamente.")
        log("        Tipico de Huawei/EMUI 9+, Samsung Knox restrictivo y algunos MIUI.")
        return False
    if size < 1024:
        log(f"[ERROR] Backup demasiado pequeno ({size} bytes < 1024). Solo cabecera, sin datos.")
        log("        Causas tipicas:")
        log("        - Usuario cancelo el dialogo en el movil (pulso 'No copiar')")
        log("        - WhatsApp legacy se instalo pero no se le concedieron permisos al arrancar")
        log("        - El OEM crea el .ab pero no incluye datos (Huawei/Samsung en algunos modos)")
        return False

    with open(WA_BACKUP, "rb") as f:
        header = f.read(24)
    if not header.startswith(b"ANDROID BACKUP"):
        log(f"[ERROR] Cabecera .ab invalida. Bytes leidos: {header!r}")
        log("        El archivo no es un Android Backup valido.")
        return False

    # Detectar si el backup esta cifrado con contrasena (header tendra 'AES-256' tras la version)
    # Formato cabecera: 'ANDROID BACKUP\n<version>\n<compressed>\n<encryption>\n'
    try:
        with open(WA_BACKUP, "rb") as f:
            header_lines = f.read(200).split(b"\n")
        if len(header_lines) >= 4:
            enc = header_lines[3].decode("ascii", errors="replace").strip()
            log(f"[WA]      cifrado del .ab: {enc or 'none'}")
            if enc and enc.upper() not in ("NONE", ""):
                log("[AVISO] El backup esta cifrado con contrasena. abe.jar fallara con clave vacia.")
                log("        Reintenta sin contrasena de backup configurada en el movil.")
    except Exception:
        pass

    log(f"[WA]  Backup valido ({size:,} bytes)")
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
            _safe_extract_tar(tf, WA_EXTRACT)
        return True
    except Exception as e:
        log(f"[ERROR] Extrayendo tar: {e}")
        return False


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    """Extrae un tar fallando rapido si detecta entradas peligrosas.

    En 3.12+ usa filter='data' (que lanza OutsideDestinationError /
    AbsolutePathError / SpecialFileError ante path traversal, symlinks
    absolutos, devices, etc.).
    En 3.11 y anteriores, valida manualmente cada miembro y lanza
    tarfile.TarError al primer problema, replicando la semantica de 'data'.

    Esto importa porque el .ab proviene de un dispositivo potencialmente
    hostil (sospechoso); un .ab malicioso podria contener entradas como
    '../../etc/passwd' que escribirian fuera de WA_EXTRACT.

    Decision forense: ante una entrada sospechosa fallamos RUIDOSAMENTE
    en vez de saltar en silencio. Skip silencioso permitiria a un atacante
    ocultar contenido manipulado entre entradas legitimas. Un WhatsApp .ab
    legitimo nunca contiene este tipo de entradas — si las hay, el archivo
    NO es de confianza.
    """
    dest = dest.resolve()
    if sys.version_info >= (3, 12):
        tf.extractall(str(dest), filter="data")
        return

    for m in tf.getmembers():
        name = m.name.lstrip("/").lstrip("\\")
        target = (dest / name).resolve()
        try:
            target.relative_to(dest)
        except ValueError:
            raise tarfile.TarError(f"Path traversal en .ab: {m.name!r}")
        if m.issym() or m.islnk():
            ln = m.linkname
            if ln.startswith("/") or ln.startswith("\\"):
                raise tarfile.TarError(f"Link absoluto en .ab: {m.name!r} -> {ln!r}")
            link_target = (target.parent / ln).resolve()
            try:
                link_target.relative_to(dest)
            except ValueError:
                raise tarfile.TarError(f"Link fuera de dest en .ab: {m.name!r} -> {ln!r}")
        if m.isdev():
            raise tarfile.TarError(f"Device file en .ab: {m.name!r}")
    tf.extractall(str(dest))


def _wa_reinstall(local_apks: list[str]) -> bool:
    log("[WA]  Restaurando WhatsApp original (install-multiple con APKs guardados)...")

    # Pre-flight: aseguramos autorizacion antes de tocar adb. En Android 14/15
    # + BBK la autorizacion USB puede haberse invalidado durante el flujo
    # (reboot, cambio de modo USB, etc.) sin que adb devices lo refleje aun.
    if not _wait_for_auth("antes de install-multiple"):
        log("[ERROR] No se obtuvo autorizacion para reinstalar WhatsApp.")
        log("        APKs originales locales en:")
        for p in local_apks:
            log(f"        {p}")
        log("        Para restaurar manualmente: adb install-multiple <ruta>/base.apk <split1> <split2>")
        return False

    ok_u, out_u, err_u = adb_run(["shell", "pm", "uninstall", "-k", "com.whatsapp"], timeout=20)
    log(f"[WA]      uninstall del legacy rc_ok={ok_u}  stdout='{out_u.strip()}'  stderr='{err_u.strip()}'")

    def _is_auth_error(stdout: str, stderr: str) -> bool:
        """Detecta fallos de conectividad/autorizacion adb que cambiar de flags
        no arregla. Lista derivada de errores reales observados en adb client."""
        combined = (stdout + " " + stderr).lower()
        signals = (
            "unauthorized",
            "device offline",
            "no devices/emulators",
            "device not found",
            "more than one device",          # multi-device race
            "insufficient permissions for device",
            "device still connecting",
            "failed to get feature set",
            "protocol fault",
            "cannot connect to daemon",
        )
        return any(s in combined for s in signals)

    def _try_install(args: list, label: str) -> tuple[bool, str, str]:
        """Ejecuta install-multiple y, si falla por auth/conectividad, intenta
        reautorizar una sola vez y reintenta el MISMO comando (cambiar flags
        no arregla auth)."""
        ok_i, out_i, err_i = adb_run(args, timeout=120)
        log(f"[WA]      install-multiple ({label}) rc_ok={ok_i}  stdout='{out_i.strip()}'  stderr='{err_i.strip()}'")
        if not ok_i and _is_auth_error(out_i, err_i):
            log(f"[WA]      Error de autorizacion/conectividad durante '{label}', reautorizando...")
            if _wait_for_auth(f"mid-{label}"):
                ok_i, out_i, err_i = adb_run(args, timeout=120)
                log(f"[WA]      install-multiple ({label}, retry tras auth) rc_ok={ok_i}  stdout='{out_i.strip()}'  stderr='{err_i.strip()}'")
        return ok_i, out_i, err_i

    ok, out, err = _try_install(["install-multiple", "-r", "-d"] + local_apks, "con -d")
    if not ok or "Success" not in (out + err):
        log("[WA]  Reintentando install-multiple sin flag de downgrade (-d)...")
        ok, out, err = _try_install(["install-multiple", "-r"] + local_apks, "sin -d")
    if not ok or "Success" not in (out + err):
        log("[WA]  Ultimo intento: install-multiple con --bypass-low-target-sdk-block...")
        ok, out, err = _try_install(
            ["install-multiple", "-r", "-d", "--bypass-low-target-sdk-block"] + local_apks,
            "bypass",
        )
    if not ok or "Success" not in (out + err):
        log(f"[ERROR] install-multiple fallo despues de 3 intentos. stderr final: '{err.strip()}'")
        log("        WhatsApp queda en estado uninstalled-keep-data en el dispositivo")
        log("        (datos preservados en /data/data/com.whatsapp/). APKs locales en:")
        for p in local_apks:
            log(f"        {p}")
        apks_dir = str(Path(local_apks[0]).parent) if local_apks else "<ruta>"
        log("        Para reintentar la restauracion (cualquiera de las dos):")
        log(f"          A) python3 {Path(__file__).name} --restore-wa \"{apks_dir}\"")
        log(f"          B) adb install-multiple -r -d \"{apks_dir}\"/*.apk")
        return False

    # Verificacion: pm path debe encontrar com.whatsapp con tantos APKs como antes
    paths = _wa_get_apk_paths()
    if not paths:
        log("[ERROR] install-multiple dijo Success pero pm path com.whatsapp esta vacio.")
        return False
    log(f"[WA]  WhatsApp restaurado y verificado ({len(paths)} APK(s) en el dispositivo)")
    return True


def _wa_restore_from_dir(apks_dir: str) -> bool:
    """Reinstala WhatsApp desde una carpeta apks_originales de un run anterior.

    Caso de uso: un run previo fallo a mitad de la restauracion (p.ej. el bug
    de unauthorized de Android 14/15 + BBK) y dejo el paquete en estado
    'uninstalled-keep-data'. Los datos en /data/data/com.whatsapp/ siguen ahi;
    basta con reinstalar los APKs originales guardados para recuperar el acceso.
    """
    src = Path(apks_dir).expanduser().resolve()
    if not src.is_dir():
        log(f"[ERROR] No es un directorio: {src}")
        return False
    apks = sorted(str(p) for p in src.glob("*.apk") if p.is_file())
    if not apks:
        log(f"[ERROR] No hay ficheros .apk en {src}")
        return False

    # Sanity check: WhatsApp moderno se distribuye como app bundle (base.apk +
    # split_config.*.apk). Si no hay ningun fichero que empiece por 'base',
    # casi seguro que la carpeta no es un apks_originales valido sino otra cosa.
    # Mejor abortar ahora que tras desinstalar.
    has_base = any(Path(p).name.lower().startswith("base") for p in apks)
    if not has_base:
        log(f"[ERROR] No hay ningun 'base*.apk' en {src} — la carpeta no parece un")
        log(f"        apks_originales valido. install-multiple va a fallar si seguimos.")
        log(f"        APKs encontrados: {[Path(p).name for p in apks]}")
        log( "        Si la APK base esta con otro nombre, renombrala a 'base.apk' antes.")
        return False

    # Validacion adicional: avisar (no bloquear) si NO hay splits. Probablemente
    # entonces es una version vieja monolitica, lo cual puede ser intencionado.
    splits = [p for p in apks if Path(p).name.lower().startswith("split_")]
    if not splits and len(apks) == 1:
        log(f"[WA]  AVISO: solo 1 APK (sin splits). Si tu WA original era app bundle,")
        log(f"      install-multiple puede fallar por split mismatch. Continuamos...")

    log(f"[*] Restaurando WhatsApp desde {len(apks)} APK(s) en {src}:")
    for a in apks:
        log(f"    - {Path(a).name} ({Path(a).stat().st_size:,} bytes)")

    if shutil.which("adb") is None:
        log("[ERROR] 'adb' no encontrado en PATH.")
        return False
    subprocess.run(["adb", "start-server"], capture_output=True)

    # Delegamos en _wa_reinstall: ya tiene pre-flight de auth, retry tras
    # reautorizacion y los 3 intentos con flags escalonados.
    if _wa_reinstall(apks):
        log("[OK] WhatsApp restaurado. Abre la app en el movil para verificar tus chats.")
        return True
    log("[ERROR] No se pudo restaurar WhatsApp. Revisa los logs de arriba.")
    return False


# ---------------------------------------------------------------------------
# METODO B (crypt15): pull no-invasivo de los ficheros externos de WhatsApp
# y descifrado opcional con la clave de 64 hex que aporta el usuario.
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _adb_remote_dir_exists(remote: str) -> bool:
    """Devuelve True si <remote> existe en el dispositivo como directorio."""
    ok, _, _ = adb_run(["shell", "test", "-d", remote], timeout=10)
    return ok


def _wa_pull_external() -> dict:
    """Pull no-invasivo de /sdcard/Android/media/com.whatsapp/WhatsApp/.

    Esta ruta esta accesible desde adb sin root (es 'external private storage'
    desde Android 11). Contiene:
      - Databases/    -> backups msgstore-*.db.crypt15/14 cifrados + wa.db.crypt15
      - Media/        -> fotos, videos, audios, voice notes (SIN cifrar)
      - Backups/      -> formato viejo, si existe

    Tambien intenta /sdcard/WhatsApp/ por compatibilidad con OEMs/versiones viejas.

    Devuelve dict con status, crypt15_files (lista de {path, size, sha256}),
    media_count, total_size.
    """
    log("[WA]  Pull crypt15 externo (no invasivo, sin desinstalar nada)...")
    WA_EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)

    sources = [
        # (label, remote path, destino local)
        ("Databases (Android 11+)",
         "/sdcard/Android/media/com.whatsapp/WhatsApp/Databases",
         WA_EXTERNAL_DIR / "Android_media" / "Databases"),
        ("Media (Android 11+)",
         "/sdcard/Android/media/com.whatsapp/WhatsApp/Media",
         WA_EXTERNAL_DIR / "Android_media" / "Media"),
        ("Backups (Android 11+, si existe)",
         "/sdcard/Android/media/com.whatsapp/WhatsApp/Backups",
         WA_EXTERNAL_DIR / "Android_media" / "Backups"),
        ("Databases (legacy /sdcard/WhatsApp/)",
         "/sdcard/WhatsApp/Databases",
         WA_EXTERNAL_DIR / "sdcard_WhatsApp" / "Databases"),
        ("Media (legacy /sdcard/WhatsApp/)",
         "/sdcard/WhatsApp/Media",
         WA_EXTERNAL_DIR / "sdcard_WhatsApp" / "Media"),
    ]

    pulled_any = False
    for label, remote, dst in sources:
        if not _adb_remote_dir_exists(remote):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        log(f"[WA]      {label}: pull {remote} -> {dst.name}/...")
        ok, out, err = adb_run(["pull", remote, str(dst)], timeout=7200)
        log(f"[WA]          rc_ok={ok} stderr='{err.strip()[:200]}'")
        if ok and dst.exists():
            pulled_any = True

    if not pulled_any:
        return {
            "status": "error", "method": "crypt15",
            "reason": ("No se encontraron carpetas externas de WhatsApp accesibles "
                       "via adb. Posibles causas: WhatsApp nunca ha generado un backup "
                       "local (recien instalado), o un OEM esta filtrando la ruta."),
        }

    # Inventario: lista los .crypt15/14 con su SHA-256
    crypt_files: list[dict] = []
    for f in sorted(WA_EXTERNAL_DIR.rglob("*.crypt15")):
        if f.is_file():
            crypt_files.append({
                "path":   str(f.relative_to(BASE)),
                "name":   f.name,
                "size":   f.stat().st_size,
                "sha256": _sha256_file(f),
                "format": "crypt15",
            })
    for f in sorted(WA_EXTERNAL_DIR.rglob("*.crypt14")):
        if f.is_file():
            crypt_files.append({
                "path":   str(f.relative_to(BASE)),
                "name":   f.name,
                "size":   f.stat().st_size,
                "sha256": _sha256_file(f),
                "format": "crypt14",
            })

    media_count = sum(1 for f in WA_EXTERNAL_DIR.rglob("*") if f.is_file()
                      and not f.suffix.lower().startswith(".crypt"))
    total_size = sum(f.stat().st_size for f in WA_EXTERNAL_DIR.rglob("*") if f.is_file())

    log(f"[WA]      .crypt* encontrados: {len(crypt_files)}")
    log(f"[WA]      Media files:         {media_count}")
    log(f"[WA]      Tamano total externo: {_human(total_size)}")

    return {
        "status": "ok",
        "method": "crypt15",
        "crypt_files": crypt_files,
        "media_count": media_count,
        "total_size": total_size,
    }


def _wa_resolve_key(key_hex: str | None, key_file: str | None) -> tuple[str | None, str]:
    """Devuelve (token_para_wadecrypt, descripcion_humana).

    El token es lo que se pasa como primer argumento a `wadecrypt`. Puede ser:
      - El propio hex de 64 chars (wa-crypt-tools lo detecta y lo trata como key)
      - La ruta a un fichero 'encrypted_backup.key' o 'key'
    Devuelve (None, '') si no hay clave -> solo se hace pull sin descifrar.
    """
    if key_file:
        p = Path(key_file).expanduser()
        if not p.is_file():
            return None, f"--wa-key-file apunta a un fichero inexistente: {p}"
        return str(p), f"keyfile {p}"
    if key_hex:
        cleaned = key_hex.strip().replace(" ", "").replace(":", "").replace("-", "")
        if len(cleaned) == 64 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
            return cleaned, "clave de 64 hex"
        return None, f"--wa-key no parece una clave hex de 64 chars (got len={len(cleaned)})"
    return None, ""


def _wa_decrypt_one(crypt_path: Path, dst_path: Path, key_token: str) -> bool:
    """Invoca `wadecrypt` (wa-crypt-tools) para descifrar UN fichero.

    Si wa-crypt-tools no esta instalado en el PATH del sistema, devuelve False
    y loguea las instrucciones de instalacion.
    """
    if shutil.which("wadecrypt") is None:
        log("[WA]      wadecrypt no esta en PATH. Instalalo con:")
        log("[WA]          pip install wa-crypt-tools")
        log("[WA]      Despues podras descifrar manualmente con:")
        log(f"[WA]          wadecrypt <CLAVE_64HEX_O_KEYFILE> {crypt_path} {dst_path}")
        return False
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["wadecrypt", key_token, str(crypt_path), str(dst_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=300,
        )
        if r.returncode == 0 and dst_path.exists() and dst_path.stat().st_size > 0:
            log(f"[WA]      OK - {crypt_path.name} -> {dst_path.name} ({_human(dst_path.stat().st_size)})")
            return True
        log(f"[WA]      wadecrypt fallo: rc={r.returncode}")
        if r.stderr.strip():
            log(f"[WA]          stderr: {r.stderr.strip()[:300]}")
        return False
    except subprocess.TimeoutExpired:
        log(f"[WA]      Timeout descifrando {crypt_path.name}")
        return False
    except Exception as e:
        log(f"[WA]      Excepcion descifrando {crypt_path.name}: {e}")
        return False


def _wa_extract_crypt15(key_hex: str | None = None,
                       key_file: str | None = None,
                       props: dict | None = None) -> dict:
    """Metodo B: pull no-invasivo + descifrado opcional.

    El pull funciona SIEMPRE (cualquier Android 11+, cualquier OEM no-EMUI).
    El descifrado solo se intenta si el usuario aporta clave (hex o keyfile).
    """
    pull = _wa_pull_external()
    if pull.get("status") != "ok":
        return pull

    key_token, key_desc = _wa_resolve_key(key_hex, key_file)
    pull["decrypted_files"] = []

    if not key_token:
        # Sin clave: solo preservamos los ficheros cifrados + Media + instrucciones
        if key_desc:  # Hubo un --wa-key invalido
            log(f"[WA]  AVISO: {key_desc}. Continuando sin descifrar.")
        pull["decryption_instructions"] = (
            "Para descifrar los .crypt15/14 necesitas la clave de 64 digitos hex\n"
            "que el TITULAR del dispositivo puede ver en:\n"
            "  WhatsApp -> Ajustes -> Chats -> Copia de seguridad ->\n"
            "  Copia cifrada de extremo a extremo -> 'Ver clave de 64 digitos'\n\n"
            "Con la clave en mano, descifra con wa-crypt-tools:\n"
            "  pip install wa-crypt-tools\n"
            "  wadecrypt <CLAVE_64_HEX> msgstore.db.crypt15 msgstore.db\n"
            "  wadecrypt <CLAVE_64_HEX> wa.db.crypt15 wa.db\n\n"
            "El resultado son ficheros SQLite plaintext que abres con:\n"
            "  sqlite3, DB Browser for SQLite, o el wa_viewer.py de esta suite."
        )
        return pull

    # Hay clave: intentar descifrar los principales
    log(f"[WA]  Intentando descifrar con {key_desc}...")
    WA_DECRYPTED.mkdir(parents=True, exist_ok=True)

    targets = []  # (.crypt_path, .db_dst)
    for cf in pull["crypt_files"]:
        crypt_path = BASE / cf["path"]
        name = cf["name"]
        # msgstore.db.crypt15 -> msgstore.db ; wa.db.crypt15 -> wa.db ; msgstore-2026-..crypt15 -> msgstore-2026-...db
        if name.endswith(".crypt15"):
            dst_name = name[:-len(".crypt15")]
        elif name.endswith(".crypt14"):
            dst_name = name[:-len(".crypt14")]
        else:
            continue
        targets.append((crypt_path, WA_DECRYPTED / dst_name))

    for crypt_path, dst in targets:
        log(f"[WA]      Descifrando {crypt_path.name}...")
        if _wa_decrypt_one(crypt_path, dst, key_token):
            pull["decrypted_files"].append({
                "path":   str(dst.relative_to(BASE)),
                "name":   dst.name,
                "size":   dst.stat().st_size,
                "sha256": _sha256_file(dst),
            })

    if pull["decrypted_files"]:
        log(f"[WA]  Descifrados {len(pull['decrypted_files'])}/{len(targets)} ficheros.")
    else:
        log("[WA]  No se descifro ningun fichero. Revisa la clave y los logs.")
    return pull


def _wa_extract_legacy(sdk: str, props: dict | None = None) -> dict:
    """Metodo A (legacy): desinstala WA moderno -> instala WA viejo via adb -> adb backup
    -> reinstala WA moderno. Solo viable en Android <= 13 o con WA targetSdk < 23.

    Devuelve dict con status / method / db_files / reason. El tramo destructivo
    (entre pm uninstall y reinstall) esta envuelto en try/finally para
    garantizar restauracion ante Ctrl-C / excepcion / cierre forzado.
    """
    ok, reason = _wa_prereqs()
    if not ok:
        log(f"[WA]  Omitido (prereqs): {reason}")
        return {"status": "skipped", "method": "legacy", "reason": reason}

    if not _wa_installed():
        if _wa_ghost_state():
            msg = ("WhatsApp esta en estado 'uninstalled-keep-data' — residuo de un "
                   "run anterior que fallo entre 'pm uninstall -k' y el reinstall.")
            log(f"[WA]  {msg}")
            log("[WA]  Recuperacion: busca la carpeta 'apks_originales' del run anterior:")
            log(f"[WA]      ls -la ~/backup_movil/*/whatsapp/apks_originales/")
            log(f"[WA]      python3 {Path(__file__).name} --restore-wa <ruta_a_apks_originales/>")
            return {"status": "error", "method": "legacy", "reason": msg}
        msg = "WhatsApp no esta instalado en el dispositivo"
        log(f"[WA]  Omitido: {msg}")
        return {"status": "skipped", "method": "legacy", "reason": msg}

    apk_paths = _wa_get_apk_paths()
    if not apk_paths:
        msg = "pm path com.whatsapp no devolvio rutas (posible bloqueo OEM)"
        log(f"[ERROR] {msg}")
        return {"status": "error", "method": "legacy", "reason": msg}
    log(f"[WA]  {len(apk_paths)} APK(s) encontrados:")
    for p in apk_paths:
        log(f"      - {p}")

    log("[WA]  Pulling APKs originales (para restauracion posterior)...")
    local_apks = _wa_pull_apks(apk_paths)
    if not local_apks:
        msg = "Error pulling APKs originales — abortando ANTES de tocar el dispositivo"
        log(f"[ERROR] {msg}")
        return {"status": "error", "method": "legacy", "reason": msg}
    log(f"[WA]  APKs originales guardados localmente en {WA_APKS_DIR}")

    log("[WA]  am force-stop com.whatsapp")
    adb_run(["shell", "am", "force-stop", "com.whatsapp"], timeout=10)

    log("[WA]  pm uninstall -k com.whatsapp (preserva /data/data/com.whatsapp/)")
    ok2, out2, err2 = adb_run(["shell", "pm", "uninstall", "-k", "com.whatsapp"], timeout=20)
    log(f"[WA]      rc_ok={ok2}  stdout='{out2.strip()}'  stderr='{err2.strip()}'")
    if not ok2 or "Success" not in (out2 + err2):
        msg = f"pm uninstall fallo: stdout='{out2.strip()}' stderr='{err2.strip()}'"
        log(f"[ERROR] {msg}")
        return {"status": "error", "method": "legacy", "reason": msg}

    # ========================================================================
    # TRAMO DESTRUCTIVO: WhatsApp esta desinstalado (con datos preservados).
    # Cualquier salida — exito, fallo, KeyboardInterrupt, excepcion — debe
    # ejecutar _wa_reinstall(local_apks) en el finally.
    # ========================================================================
    restore_needed = True
    result: dict = {"status": "error", "method": "legacy",
                    "reason": "Interrumpido antes de definir resultado"}
    try:
        if not _wa_reboot_wait():
            result = {"status": "error", "method": "legacy",
                      "reason": "Error reiniciando dispositivo (ver logs)"}
            return result

        if not _wa_install_legacy(sdk):
            result = {"status": "error", "method": "legacy",
                      "reason": "No se pudo instalar APK legacy (ver intentos arriba)"}
            return result

        if not _wa_backup():
            result = {"status": "error", "method": "legacy",
                      "reason": "adb backup fallo o .ab vacio (ver logs)"}
            return result

        log("[WA]  Convirtiendo .ab -> .tar -> extracting...")
        extracted = _wa_extract_ab()
        if not extracted:
            result = {"status": "error", "method": "legacy",
                      "reason": "Error extrayendo .ab (ver logs)"}
            return result

        db_files = list(WA_EXTRACT.rglob("*.db"))
        log(f"[WA]  Bases de datos encontradas: {len(db_files)}")
        for f in db_files:
            log(f"      - {f.relative_to(BASE)}")
        if not db_files:
            result = {"status": "error", "method": "legacy",
                      "reason": "Backup extraido sin .db (datos vacios)"}
            return result

        result = {"status": "ok", "method": "legacy",
                  "db_files": [str(f.relative_to(BASE)) for f in db_files]}
        return result

    except KeyboardInterrupt:
        log("\n[WA]  [Ctrl-C detectado] Restauro WhatsApp ANTES de salir...")
        result = {"status": "error", "method": "legacy",
                  "reason": "Interrumpido por el usuario (Ctrl-C)"}
        raise
    except BaseException as e:
        log(f"[WA]  Excepcion inesperada en tramo destructivo: {type(e).__name__}: {e}")
        result = {"status": "error", "method": "legacy",
                  "reason": f"Excepcion: {type(e).__name__}: {e}"}
        raise
    finally:
        if restore_needed:
            log("[WA]  Restaurando WhatsApp original (siempre, sea exito o fallo)...")
            try:
                if not _wa_reinstall(local_apks):
                    log("[ERROR CRITICO] La restauracion de WhatsApp fallo. Datos preservados,")
                    log(f"                APK locales en {WA_APKS_DIR}.")
                    log(f"                Recupera con: python3 {Path(__file__).name} --restore-wa \"{WA_APKS_DIR}\"")
            except BaseException as e:
                log(f"[ERROR CRITICO] Excepcion durante restauracion: {type(e).__name__}: {e}")


def extract_whatsapp(sdk: str, props: dict | None = None,
                    method: str = "auto",
                    key_hex: str | None = None,
                    key_file: str | None = None) -> dict:
    """Dispatcher de extraccion WhatsApp. Elige entre metodos:

    - 'auto'    (default): diagnostica el dispositivo. Si legacy es viable lo
                intenta; si falla, cae a crypt15. Si legacy NO es viable salta
                directamente a crypt15.
    - 'legacy': fuerza el metodo A (instalar WA viejo + adb backup + restaurar).
                Avisa si el diagnostico dice que no es viable, pero lo intenta.
    - 'crypt15': fuerza el metodo B (pull no-invasivo de /sdcard/Android/media/
                com.whatsapp/WhatsApp/ + descifrado opcional con clave).

    key_hex y key_file solo se usan en metodo crypt15: si se aportan, intenta
    descifrar los .crypt15 con `wadecrypt` (de wa-crypt-tools).
    """
    log("\n[*] Extraccion WhatsApp...")

    # Avisos OEM/Android
    if props:
        quirks = detect_oem_quirks(props)
        for w in quirks.get("warnings", []):
            log(f"[WA]  AVISO: {w}")
        for p in quirks.get("preflight", []):
            log(f"[WA]  ACCION REQUERIDA EN EL MOVIL: {p}")

    # Diagnostico de compatibilidad del metodo legacy
    diag = _wa_detect_compatibility(props or {})
    _wa_log_diagnostic(diag)

    # ---- Dispatcher por --wa-method ----
    if method == "legacy":
        if not diag["legacy_viable"]:
            log("[WA]  ATENCION: --wa-method legacy forzado pero el diagnostico dice")
            log(f"        que NO es viable. Razon: {diag['reason_legacy_blocked']}")
            log("        Procediendo igual (tu lo pediste explicitamente)...")
        result = _wa_extract_legacy(sdk, props)
        result["diagnostic"] = diag
        return result

    if method == "crypt15":
        log("[WA]  Metodo CRYPT15 forzado por --wa-method. Saltando legacy.")
        result = _wa_extract_crypt15(key_hex=key_hex, key_file=key_file, props=props)
        result["diagnostic"] = diag
        return result

    # ---- method == 'auto' ----
    if diag["legacy_viable"]:
        log("[WA]  Procediendo con metodo LEGACY (viable en este dispositivo)...")
        legacy_result = _wa_extract_legacy(sdk, props)
        if legacy_result.get("status") == "ok":
            legacy_result["diagnostic"] = diag
            return legacy_result
        log(f"[WA]  Legacy fallo: {legacy_result.get('reason')}")
        log("[WA]  Cayendo al metodo CRYPT15 como fallback (no invasivo)...")
        crypt = _wa_extract_crypt15(key_hex=key_hex, key_file=key_file, props=props)
        crypt["legacy_attempt"] = legacy_result
        crypt["diagnostic"] = diag
        return crypt

    log("[WA]  Saltando metodo LEGACY (no viable). Usando CRYPT15...")
    log("[WA]  Para configuracion optima del metodo CRYPT15, en el movil:")
    log("[WA]    1) Activa 'Copia de seguridad cifrada E2E' en WA -> Ajustes -> Chats")
    log("[WA]    2) Elige 'Usar clave de 64 digitos' (NO password)")
    log("[WA]    3) Apunta la clave que sale en pantalla")
    log("[WA]    4) Pulsa 'Hacer copia' para forzar un backup fresco con esa clave")
    log("[WA]    5) Relanza con: --wa-method crypt15 --wa-key <CLAVE_64_HEX>")
    log("[WA]  Si lanzaste sin clave, los .crypt15 quedaran preservados con sus")
    log("[WA]  SHA-256 en el informe para descifrar mas tarde.")
    result = _wa_extract_crypt15(key_hex=key_hex, key_file=key_file, props=props)
    result["diagnostic"] = diag
    return result

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


def _build_wa_section(wa_result: dict) -> str:
    """Renderiza la seccion HTML de WhatsApp segun el metodo usado y el resultado."""
    status = wa_result.get("status")
    method = wa_result.get("method", "")
    diag   = wa_result.get("diagnostic", {})

    # Cabecera de diagnostico (siempre presente si tenemos diag)
    diag_html = ""
    if diag:
        ab = diag.get("wa_allow_backup")
        ab_str = "true" if ab is True else ("false" if ab is False else "?")
        legacy_str = "✓ viable" if diag.get("legacy_viable") else f"✗ no viable: {diag.get('reason_legacy_blocked','')}"
        diag_html = (
            '<div style="background:var(--bg2);border-left:3px solid var(--accent);'
            'padding:.75rem 1rem;margin:.5rem 0 1rem 0;font-size:.85rem;color:var(--text-dim)">'
            f'<strong>Diagnostico de compatibilidad</strong><br>'
            f'Android: <code>{_esc(diag.get("android_version","?"))}</code> '
            f'(SDK <code>{diag.get("android_sdk","?")}</code>) &middot; '
            f'WA version: <code>{_esc(diag.get("wa_version") or "?")}</code> &middot; '
            f'targetSdk: <code>{diag.get("wa_target_sdk") if diag.get("wa_target_sdk") is not None else "?"}</code> &middot; '
            f'allowBackup: <code>{ab_str}</code><br>'
            f'Metodo LEGACY: {_esc(legacy_str)}'
            '</div>'
        )

    # -- Caso 1: legacy OK --
    if status == "ok" and method == "legacy":
        db_items = "".join(
            f'<li>{_ICONS["database"]}<code>{_esc(f)}</code></li>'
            for f in wa_result.get("db_files", [])
        )
        return (
            f'<section><h2>{_ICONS["wa"]} WhatsApp '
            f'<span class="badge" style="background:var(--green)">Extraido (legacy)</span></h2>'
            f'{diag_html}'
            f'<p style="margin-bottom:1rem;color:var(--text-dim);font-size:.9rem">'
            f'Base de datos extraida mediante el metodo APK downgrade + adb backup. '
            f'Los <code>.db</code> resultantes ya estan en plaintext y se pueden abrir '
            f'con <code>sqlite3</code> o <code>wa_viewer.py</code>.</p>'
            f'<ul class="archivos">{db_items}</ul></section>'
        )

    # -- Caso 2: crypt15 OK --
    if status == "ok" and method == "crypt15":
        crypt_rows = "".join(
            f'<tr><td><code>{_esc(c["name"])}</code></td>'
            f'<td>{_esc(_human(c["size"]))}</td>'
            f'<td><code style="font-size:.75rem">{_esc(c["sha256"][:16])}…</code></td>'
            f'<td><code>{_esc(c["format"])}</code></td></tr>'
            for c in wa_result.get("crypt_files", [])
        )
        decrypted = wa_result.get("decrypted_files") or []
        decrypted_html = ""
        if decrypted:
            decr_items = "".join(
                f'<li>{_ICONS["database"]}<code>{_esc(f["path"])}</code> '
                f'<span style="color:var(--text-dim);font-size:.85rem">({_esc(_human(f["size"]))})</span></li>'
                for f in decrypted
            )
            decrypted_html = (
                f'<h3 style="margin-top:1.5rem;color:var(--accent2)">'
                f'Descifrados ({len(decrypted)})</h3>'
                f'<ul class="archivos">{decr_items}</ul>'
            )
        instructions_html = ""
        if wa_result.get("decryption_instructions"):
            instr = _esc(wa_result["decryption_instructions"]).replace("\n", "<br>")
            instructions_html = (
                f'<h3 style="margin-top:1.5rem;color:var(--accent2)">'
                f'Instrucciones de descifrado para el receptor</h3>'
                f'<div style="background:var(--bg2);padding:1rem;border-radius:6px;'
                f'font-size:.85rem;color:var(--text-dim);line-height:1.6">{instr}</div>'
            )
        return (
            f'<section><h2>{_ICONS["wa"]} WhatsApp '
            f'<span class="badge" style="background:var(--green)">Pulled (crypt15)</span></h2>'
            f'{diag_html}'
            f'<p style="margin-bottom:1rem;color:var(--text-dim);font-size:.9rem">'
            f'Extraccion no invasiva de <code>/sdcard/Android/media/com.whatsapp/</code>. '
            f'Media: <strong>{wa_result.get("media_count",0)}</strong> ficheros. '
            f'Tamano total externo: <strong>{_esc(_human(wa_result.get("total_size",0)))}</strong>.</p>'
            f'<div class="table-wrap"><table>'
            f'<thead><tr><th>Archivo cifrado</th><th>Tamano</th><th>SHA-256 (primeros 16)</th><th>Formato</th></tr></thead>'
            f'<tbody>{crypt_rows}</tbody></table></div>'
            f'{decrypted_html}'
            f'{instructions_html}'
            f'</section>'
        )

    # -- Caso 3: error --
    if status == "error":
        return (
            f'<section><h2>{_ICONS["wa"]} WhatsApp '
            f'<span class="badge" style="background:#da3633">Error</span></h2>'
            f'{diag_html}'
            f'<p style="color:var(--text-dim)">{_esc(wa_result.get("reason",""))}</p></section>'
        )

    # -- Caso 4: skipped --
    return (
        f'<section><h2>{_ICONS["wa"]} WhatsApp '
        f'<span class="badge" style="background:#6e7681">Omitido</span></h2>'
        f'{diag_html}'
        f'<p style="color:var(--text-dim)">{_esc(wa_result.get("reason",""))}</p></section>'
    )


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
    wa_section = _build_wa_section(wa_result)

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
    parser.add_argument(
        "--restore-wa", metavar="DIR",
        help="Modo recuperacion: reinstala WhatsApp desde una carpeta 'apks_originales' "
             "de un run anterior. Util cuando un run previo fallo entre uninstall y "
             "reinstall y dejo WhatsApp en estado uninstalled-keep-data. No realiza el "
             "backup forense — solo la restauracion. Ej: --restore-wa "
             "~/backup_movil/2026-05-12_02-18-31/whatsapp/apks_originales"
    )
    parser.add_argument(
        "--device", metavar="SERIAL",
        help="Serial del dispositivo (obligatorio si hay mas de uno conectado). "
             "Sacalo de 'adb devices'. Sin este flag y con multiples dispositivos, "
             "el script aborta — proteccion forense contra actuar sobre el movil equivocado."
    )
    parser.add_argument(
        "--wa-method", choices=("auto", "legacy", "crypt15"), default="auto",
        help="Metodo de extraccion WhatsApp: "
             "'auto' (default) diagnostica el dispositivo y elige; "
             "'legacy' fuerza el metodo clasico (instalar WA viejo + adb backup, "
             "solo viable en Android <= 13 o WA con targetSdk < 23); "
             "'crypt15' fuerza el metodo no invasivo (pull de /sdcard/Android/media/"
             "com.whatsapp/, requiere clave de 64 hex para descifrar si quieres "
             "los datos en claro)."
    )
    parser.add_argument(
        "--wa-key", metavar="HEX64",
        help="Clave de 64 caracteres hex para descifrar los .crypt15 con wa-crypt-tools. "
             "El titular la saca en WhatsApp -> Ajustes -> Chats -> Copia de seguridad -> "
             "Copia cifrada de extremo a extremo -> 'Ver clave de 64 digitos'. "
             "Solo aplica a --wa-method crypt15 (o cuando auto cae a crypt15). "
             "Ej: --wa-key 1234...abcd (acepta separadores ':', '-' o espacios; se normalizan)"
    )
    parser.add_argument(
        "--wa-key-file", metavar="PATH",
        help="Alternativa a --wa-key: ruta al fichero 'encrypted_backup.key' (crypt15) o "
             "'key' (crypt14). Pasado tal cual al primer argumento de wadecrypt."
    )
    args = parser.parse_args()

    check_prerequisites()
    print()

    if args.restore_wa:
        # Modo recuperacion: solo restaura WhatsApp, no toca el resto del backup.
        check_device(prefer_serial=args.device)
        ok = _wa_restore_from_dir(args.restore_wa)
        sys.exit(0 if ok else 1)

    device_id = check_device(prefer_serial=args.device)

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
        wa_result = extract_whatsapp(
            props["sdk"], props,
            method=args.wa_method,
            key_hex=args.wa_key,
            key_file=args.wa_key_file,
        )

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
