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


def _adb_authorized() -> bool:
    """Probe real: ejecuta 'adb shell echo ok' y comprueba autorizacion efectiva.

    No basta con 'adb devices' diciendo 'device' — en Android 14/15 (sobre todo
    Realme/OPPO/OnePlus/Vivo) el estado puede reportarse 'device' por cache de
    adbd 5-10 s despues de un reboot mientras la autorizacion USB ya esta
    invalidada. Solo un comando real lo confirma.
    """
    try:
        r = subprocess.run(
            ["adb", "shell", "echo", "ok"],
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


def _wa_install_legacy(sdk: str) -> bool:
    """
    Instala LegacyWhatsApp.apk (targetSdk=19) en 3 estrategias escalonadas.
    Loguea TODOS los intentos con su stdout/stderr.

    Estrategia 1: adb install --bypass-low-target-sdk-block (host-side).
                  Requiere platform-tools >= 34.
    Estrategia 2: adb push + adb shell pm install --bypass-low-target-sdk-block
                  (device-side parsing del flag). Funciona con cualquier adb del host
                  siempre que Android >= 14. Sortea el host adb antiguo.
    Estrategia 3: adb install sin bypass. Solo funciona en Android <= 13 donde
                  el targetSdk bajo no esta bloqueado por sistema.
    """
    def _success(out: str, err: str) -> bool:
        # pm install / adb install imprimen 'Success' en stdout cuando va bien.
        return "Success" in out or "Success" in err

    # --- Estrategia 1 ---
    log("[WA]  Install legacy intento 1/3: adb install --bypass-low-target-sdk-block (host-side)")
    ok1, out1, err1 = adb_run(
        ["install", "-r", "-d", "--bypass-low-target-sdk-block", str(LEGACY_APK)],
        timeout=60,
    )
    log(f"[WA]      rc_ok={ok1}  stdout='{out1.strip()}'  stderr='{err1.strip()}'")
    if ok1 and _success(out1, err1):
        log("[WA]  OK - APK legacy instalado (estrategia 1)")
        return True

    # --- Estrategia 2 ---
    log("[WA]  Install legacy intento 2/3: adb push + adb shell pm install (device-side)")
    remote = "/data/local/tmp/LegacyWhatsApp.apk"
    okp, outp, errp = adb_run(["push", str(LEGACY_APK), remote], timeout=60)
    log(f"[WA]      push rc_ok={okp}  stdout='{outp.strip()}'  stderr='{errp.strip()}'")
    if okp:
        ok2, out2, err2 = adb_run(
            ["shell", "pm", "install", "-r", "-d", "--bypass-low-target-sdk-block", remote],
            timeout=60,
        )
        log(f"[WA]      pm install rc_ok={ok2}  stdout='{out2.strip()}'  stderr='{err2.strip()}'")
        adb_run(["shell", "rm", "-f", remote], timeout=10)
        if ok2 and _success(out2, err2):
            log("[WA]  OK - APK legacy instalado (estrategia 2, device-side bypass)")
            return True
    else:
        log("[WA]      (no se intenta pm install porque el push fallo)")

    # --- Estrategia 3 ---
    log("[WA]  Install legacy intento 3/3: adb install SIN bypass (solo Android <= 13)")
    ok3, out3, err3 = adb_run(["install", "-r", "-d", str(LEGACY_APK)], timeout=60)
    log(f"[WA]      rc_ok={ok3}  stdout='{out3.strip()}'  stderr='{err3.strip()}'")
    if ok3 and _success(out3, err3):
        log("[WA]  OK - APK legacy instalado (estrategia 3, sin bypass)")
        return True

    log("[ERROR] No se pudo instalar el APK legacy con ninguna de las 3 estrategias.")
    log("        Causas tipicas y como diagnosticarlas:")
    log("        - Android 14+ BBK (Realme/OPPO/OnePlus/Vivo): activa 'Desactivar monitor de")
    log("          permisos' en Opciones de desarrollador y togglea USB Debugging off/on.")
    log("        - Android 14+ Xiaomi/MIUI: activa 'Install via USB' y 'USB debugging (Security")
    log("          settings)' (requiere SIM o cuenta Mi).")
    log("        - Android 14+ stock/Pixel: el flag --bypass-low-target-sdk-block deberia bastar.")
    log("        - INSTALL_FAILED_VERIFICATION_FAILURE: desactiva 'Verify apps over USB' o")
    log("          ejecuta 'adb shell settings put global verifier_verify_adb_installs 0'.")
    log("        - INSTALL_FAILED_UPDATE_INCOMPATIBLE: el dispositivo aun tiene rastros del WA")
    log("          anterior (firma distinta). Reinicia el dispositivo.")
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
            # filter='data' disponible desde Python 3.11.4 para evitar DeprecationWarning
            extract_kwargs = {"filter": "data"} if sys.version_info >= (3, 12) else {}
            tf.extractall(str(WA_EXTRACT), **extract_kwargs)
        return True
    except Exception as e:
        log(f"[ERROR] Extrayendo tar: {e}")
        return False


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

    def _is_auth_error(stderr: str) -> bool:
        e = stderr.lower()
        return ("unauthorized" in e
                or "device offline" in e
                or "no devices/emulators" in e
                or "device not found" in e)

    def _try_install(args: list, label: str) -> tuple[bool, str, str]:
        """Ejecuta install-multiple y, si falla por auth, intenta reautorizar
        una sola vez y reintenta el MISMO comando (cambiar flags no arregla auth)."""
        ok_i, out_i, err_i = adb_run(args, timeout=120)
        log(f"[WA]      install-multiple ({label}) rc_ok={ok_i}  stdout='{out_i.strip()}'  stderr='{err_i.strip()}'")
        if not ok_i and _is_auth_error(err_i):
            log(f"[WA]      Error de autorizacion durante '{label}', reautorizando...")
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
        log("        WhatsApp queda desinstalado en el dispositivo. APKs locales en:")
        for p in local_apks:
            log(f"        {p}")
        log("        Para restaurar manualmente: adb install-multiple <ruta>/base.apk <split1> <split2>")
        return False

    # Verificacion: pm path debe encontrar com.whatsapp con tantos APKs como antes
    paths = _wa_get_apk_paths()
    if not paths:
        log("[ERROR] install-multiple dijo Success pero pm path com.whatsapp esta vacio.")
        return False
    log(f"[WA]  WhatsApp restaurado y verificado ({len(paths)} APK(s) en el dispositivo)")
    return True


def extract_whatsapp(sdk: str, props: dict | None = None) -> dict:
    log("\n[*] Extraccion WhatsApp...")

    # Avisos OEM/Android antes de tocar nada
    if props:
        quirks = detect_oem_quirks(props)
        for w in quirks.get("warnings", []):
            log(f"[WA]  AVISO: {w}")
        for p in quirks.get("preflight", []):
            log(f"[WA]  ACCION REQUERIDA EN EL MOVIL: {p}")

    ok, reason = _wa_prereqs()
    if not ok:
        log(f"[WA]  Omitido (prereqs): {reason}")
        return {"status": "skipped", "reason": reason}

    if not _wa_installed():
        msg = "WhatsApp no esta instalado en el dispositivo"
        log(f"[WA]  Omitido: {msg}")
        return {"status": "skipped", "reason": msg}

    apk_paths = _wa_get_apk_paths()
    if not apk_paths:
        msg = "pm path com.whatsapp no devolvio rutas (posible bloqueo OEM)"
        log(f"[ERROR] {msg}")
        return {"status": "error", "reason": msg}
    log(f"[WA]  {len(apk_paths)} APK(s) encontrados:")
    for p in apk_paths:
        log(f"      - {p}")

    log("[WA]  Pulling APKs originales (para restauracion posterior)...")
    local_apks = _wa_pull_apks(apk_paths)
    if not local_apks:
        msg = "Error pulling APKs originales — abortando ANTES de tocar el dispositivo"
        log(f"[ERROR] {msg}")
        return {"status": "error", "reason": msg}
    log(f"[WA]  APKs originales guardados localmente en {WA_APKS_DIR}")

    log("[WA]  am force-stop com.whatsapp")
    ok_fs, out_fs, err_fs = adb_run(
        ["shell", "am", "force-stop", "com.whatsapp"], timeout=10
    )
    log(f"[WA]      rc_ok={ok_fs}  stdout='{out_fs.strip()}'  stderr='{err_fs.strip()}'")

    log("[WA]  pm uninstall -k com.whatsapp (preserva /data/data/com.whatsapp/)")
    ok2, out2, err2 = adb_run(["shell", "pm", "uninstall", "-k", "com.whatsapp"], timeout=20)
    log(f"[WA]      rc_ok={ok2}  stdout='{out2.strip()}'  stderr='{err2.strip()}'")
    if not ok2 or "Success" not in (out2 + err2):
        msg = f"pm uninstall fallo: stdout='{out2.strip()}' stderr='{err2.strip()}'"
        log(f"[ERROR] {msg}")
        log("        En Huawei/EMUI esto es comun. WhatsApp NO se ha desinstalado, no hay que restaurar.")
        return {"status": "error", "reason": msg}

    if not _wa_reboot_wait():
        log("[WA]  Intentando restaurar WhatsApp tras fallo de reinicio...")
        _wa_reinstall(local_apks)
        return {"status": "error", "reason": "Error reiniciando dispositivo (ver logs)"}

    if not _wa_install_legacy(sdk):
        log("[WA]  Install legacy fallo. Restaurando WhatsApp original...")
        if not _wa_reinstall(local_apks):
            log("[ERROR CRITICO] No se pudo restaurar WhatsApp original tampoco.")
            log(f"                APKs originales estan en {WA_APKS_DIR}")
        return {"status": "error", "reason": "No se pudo instalar APK legacy (ver intentos arriba)"}

    if not _wa_backup():
        log("[WA]  adb backup fallo. Restaurando WhatsApp original...")
        if not _wa_reinstall(local_apks):
            log("[ERROR CRITICO] No se pudo restaurar WhatsApp original tampoco.")
        return {"status": "error", "reason": "adb backup fallo (ver logs detallados arriba)"}

    log("[WA]  Convirtiendo .ab -> .tar -> extracting...")
    extracted = _wa_extract_ab()

    log("[WA]  Restaurando WhatsApp original (siempre, exito o fallo de extraccion)...")
    if not _wa_reinstall(local_apks):
        log("[ERROR CRITICO] La restauracion de WhatsApp fallo. Revisa logs.")

    if not extracted:
        return {"status": "error", "reason": "Error extrayendo .ab (ver logs)"}

    db_files = list(WA_EXTRACT.rglob("*.db"))
    log(f"[WA]  Bases de datos encontradas: {len(db_files)}")
    for f in db_files:
        log(f"      - {f.relative_to(BASE)}")

    if not db_files:
        msg = "El backup se extrajo correctamente pero no contiene .db (datos vacios)"
        log(f"[ERROR] {msg}")
        return {"status": "error", "reason": msg}

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
        wa_result = extract_whatsapp(props["sdk"], props)

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
