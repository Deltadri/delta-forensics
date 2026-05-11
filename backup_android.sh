#!/bin/bash
# =====================================================================
# Backup + Informe Forense Android (Universal)
# Autor: Adrian Fernandez
# Practica: Analisis de dispositivos Android mediante ADB
#
# Compatible con cualquier marca (Samsung, Xiaomi, OPPO, Realme, OnePlus,
# Huawei, Honor, Motorola, Sony, Google Pixel, Nothing, Vivo, etc.) y
# con Android 4.x hasta Android 15+. Cada comando se ejecuta con
# fallback y solo se incluye en el informe si devuelve datos validos.
# =====================================================================

set -u

# ---------------------------------------------------------------------
# 1. COMPROBACIONES PREVIAS
# ---------------------------------------------------------------------

# Detectar gestor de paquetes para dar instrucciones correctas
detectar_gestor() {
    if   command -v apt > /dev/null 2>&1;     then echo "apt"
    elif command -v dnf > /dev/null 2>&1;     then echo "dnf"
    elif command -v yum > /dev/null 2>&1;     then echo "yum"
    elif command -v pacman > /dev/null 2>&1;  then echo "pacman"
    elif command -v zypper > /dev/null 2>&1;  then echo "zypper"
    elif command -v apk > /dev/null 2>&1;     then echo "apk"
    elif command -v brew > /dev/null 2>&1;    then echo "brew"
    else echo "desconocido"
    fi
}

comando_instalar() {
    local gestor="$1"; local paquete="$2"
    case "$gestor" in
        apt)    echo "sudo apt install -y $paquete" ;;
        dnf)    echo "sudo dnf install -y $paquete" ;;
        yum)    echo "sudo yum install -y $paquete" ;;
        pacman) echo "sudo pacman -S --noconfirm $paquete" ;;
        zypper) echo "sudo zypper install -y $paquete" ;;
        apk)    echo "sudo apk add $paquete" ;;
        brew)   echo "brew install $paquete" ;;
        *)      echo "[instala manualmente '$paquete' en tu distro]" ;;
    esac
}

nombre_paquete() {
    local cmd="$1"; local gestor="$2"
    case "$cmd" in
        adb)        case "$gestor" in
                        apt) echo "android-tools-adb" ;;
                        dnf|yum) echo "android-tools" ;;
                        pacman) echo "android-tools" ;;
                        zypper) echo "android-tools" ;;
                        brew) echo "android-platform-tools" ;;
                        apk) echo "android-tools" ;;
                        *) echo "android-tools-adb" ;;
                    esac ;;
        sha256sum)  echo "coreutils" ;;
        timeout)    echo "coreutils" ;;
        awk)        echo "gawk" ;;
        *)          echo "$cmd" ;;
    esac
}

GESTOR=$(detectar_gestor)
echo "[*] Comprobando herramientas necesarias..."
echo "    Gestor detectado: $GESTOR"

REQUERIDAS="adb timeout awk sed grep find du wc sha256sum tr cut head sort uniq"
FALTAN=""

for cmd in $REQUERIDAS; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        FALTAN="$FALTAN $cmd"
    fi
done

if [ -n "$FALTAN" ]; then
    echo ""
    echo "[ERROR] Faltan las siguientes herramientas:"
    for cmd in $FALTAN; do
        echo "         - $cmd"
    done
    echo ""
    echo "[!] Instrucciones de instalacion para tu sistema:"
    echo ""

    PAQUETES=""
    for cmd in $FALTAN; do
        paq=$(nombre_paquete "$cmd" "$GESTOR")
        case " $PAQUETES " in
            *" $paq "*) ;;
            *) PAQUETES="$PAQUETES $paq" ;;
        esac
    done

    if [ "$GESTOR" = "desconocido" ]; then
        echo "    No se ha podido detectar tu gestor de paquetes."
        echo "    Instala manualmente:$PAQUETES"
    else
        echo "    $(comando_instalar "$GESTOR" "$(echo $PAQUETES | tr -s ' ')")"
    fi
    echo ""
    echo "    Despues vuelve a ejecutar este script."
    exit 1
fi

ADB_VER=$(adb --version 2>/dev/null | head -1)
echo "    OK - $ADB_VER"
echo ""

adb start-server > /dev/null 2>&1

# --- Comprobar dispositivos ---
DISPOSITIVOS=$(adb devices | grep -w "device" | awk '{print $1}')
NUM_DISP=$(echo "$DISPOSITIVOS" | grep -c .)

NO_AUTORIZADOS=$(adb devices | grep -w "unauthorized" | awk '{print $1}')
NUM_NO_AUTH=$(echo "$NO_AUTORIZADOS" | grep -c .)

if [ "$NUM_NO_AUTH" -gt 0 ]; then
    echo "[ERROR] Dispositivo conectado pero NO autorizado."
    echo ""
    echo "    Acepta la huella RSA que aparece en la pantalla del movil."
    echo "    Marca 'Permitir siempre desde este ordenador' y dale a OK."
    echo "    Despues vuelve a ejecutar este script."
    exit 1
fi

if [ "$NUM_DISP" -eq 0 ]; then
    echo "[ERROR] No hay ningun dispositivo conectado."
    echo ""
    echo "    Comprobaciones a realizar:"
    echo "    1. El cable USB es de DATOS (no solo de carga)."
    echo "    2. En el movil: Ajustes > Acerca del telefono > pulsar 7 veces"
    echo "       'Numero de compilacion' para activar Opciones de desarrollador."
    echo "    3. Ajustes > Sistema > Opciones de desarrollador > Depuracion USB: ON."
    echo "    4. Al conectar el USB, en la cortinilla de notificaciones cambia"
    echo "       el modo USB a 'Transferencia de archivos' (MTP)."
    echo "    5. Acepta la huella RSA si aparece en pantalla."
    echo ""
    echo "    Comprobar con: adb devices"
    exit 1
fi

if [ "$NUM_DISP" -gt 1 ]; then
    echo "[AVISO] Hay varios dispositivos conectados:"
    echo "$DISPOSITIVOS" | sed 's/^/    /'
    echo "        Usando el primero. Si quieres otro, desconecta los demas."
fi

DISPOSITIVO=$(echo "$DISPOSITIVOS" | head -n1)
echo "[*] Dispositivo encontrado: $DISPOSITIVO"
echo ""

# Helpers
adb_get() { timeout 15 adb shell "$@" 2>/dev/null | tr -d '\r'; }
escape_html() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'; }

# ---------------------------------------------------------------------
# 2. CARPETAS
# ---------------------------------------------------------------------
FECHA=$(date +%Y-%m-%d_%H-%M-%S)
BASE=$HOME/backup_movil/$FECHA
ALMACEN=$BASE/almacenamiento_interno
DATOS=$BASE/datos_forenses
INFORME=$BASE/informe.html

mkdir -p "$ALMACEN" "$DATOS"

echo "================================================================="
echo " BACKUP + INFORME FORENSE ANDROID"
echo " Dispositivo: $DISPOSITIVO"
echo " Destino:     $BASE"
echo "================================================================="

# ---------------------------------------------------------------------
# 3. IDENTIFICACION DEL DISPOSITIVO
# ---------------------------------------------------------------------
echo "[1/8] Identificando dispositivo..."

adb_get getprop > "$DATOS/propiedades.txt"

MODELO=$(adb_get getprop ro.product.model)
MARCA=$(adb_get getprop ro.product.manufacturer)
BRAND=$(adb_get getprop ro.product.brand)
DEVICE=$(adb_get getprop ro.product.device)
ANDROID_VER=$(adb_get getprop ro.build.version.release)
SDK=$(adb_get getprop ro.build.version.sdk)
SERIE=$(adb_get getprop ro.serialno)
BUILD=$(adb_get getprop ro.build.display.id)
KERNEL=$(adb_get uname -a)
HUELLA=$(adb_get getprop ro.build.fingerprint)
SEGURIDAD=$(adb_get getprop ro.build.version.security_patch)
ARQ=$(adb_get getprop ro.product.cpu.abi)
IDIOMA=$(adb_get getprop persist.sys.locale)
TIMEZONE=$(adb_get getprop persist.sys.timezone)
BOOTLOADER=$(adb_get getprop ro.bootloader)
RADIO=$(adb_get getprop gsm.version.baseband)

# Detectar capa de personalizacion
CAPA="Android stock"
MIUI=$(adb_get getprop ro.miui.ui.version.name)
COLOROS=$(adb_get getprop ro.build.version.opporom)
ONEUI=$(adb_get getprop ro.build.version.oneui)
EMUI=$(adb_get getprop ro.build.version.emui)
HYPEROS=$(adb_get getprop ro.mi.os.version.name)
REALMEUI=$(adb_get getprop ro.build.version.realmeui)
OXYGENOS=$(adb_get getprop ro.oxygen.version)
NOTHINGOS=$(adb_get getprop ro.nothing.version)

if   [ -n "$HYPEROS" ];  then CAPA="HyperOS $HYPEROS"
elif [ -n "$MIUI" ];     then CAPA="MIUI $MIUI"
elif [ -n "$COLOROS" ];  then CAPA="ColorOS $COLOROS"
elif [ -n "$ONEUI" ];    then CAPA="One UI $ONEUI"
elif [ -n "$EMUI" ];     then CAPA="EMUI $EMUI"
elif [ -n "$REALMEUI" ]; then CAPA="Realme UI $REALMEUI"
elif [ -n "$OXYGENOS" ]; then CAPA="OxygenOS $OXYGENOS"
elif [ -n "$NOTHINGOS" ];then CAPA="Nothing OS $NOTHINGOS"
fi

[ -z "$MODELO" ]      && MODELO="Desconocido"
[ -z "$MARCA" ]       && MARCA="Desconocida"
[ -z "$ANDROID_VER" ] && ANDROID_VER="Desconocida"
[ -z "$SDK" ]         && SDK="?"
[ -z "$SERIE" ]       && SERIE="No disponible"

# ---------------------------------------------------------------------
# 4. COPIA DEL ALMACENAMIENTO
# ---------------------------------------------------------------------
echo "[2/8] Copiando almacenamiento interno (puede tardar)..."

TAM_SDCARD=$(adb_get du -sh /sdcard/ | awk '{print $1}')
[ -z "$TAM_SDCARD" ] && TAM_SDCARD="?"
echo "      Tamano estimado en el dispositivo: $TAM_SDCARD"

adb pull /sdcard/ "$ALMACEN/" > "$DATOS/pull_log.txt" 2>&1 || true

if [ ! "$(ls -A "$ALMACEN" 2>/dev/null)" ]; then
    echo "      /sdcard/ vacio, intentando /storage/emulated/0/"
    adb pull /storage/emulated/0/ "$ALMACEN/" >> "$DATOS/pull_log.txt" 2>&1 || true
fi

# ---------------------------------------------------------------------
# 5. APLICACIONES
# ---------------------------------------------------------------------
echo "[3/8] Listando aplicaciones..."

adb_get pm list packages -f       > "$DATOS/apps_todas.txt"
adb_get pm list packages -f -3    > "$DATOS/apps_usuario.txt"
adb_get pm list packages -f -s    > "$DATOS/apps_sistema.txt"
adb_get pm list packages -d       > "$DATOS/apps_deshabilitadas.txt"

NUM_TOTAL=$(wc -l < "$DATOS/apps_todas.txt" 2>/dev/null | tr -d ' ')
NUM_USUARIO=$(wc -l < "$DATOS/apps_usuario.txt" 2>/dev/null | tr -d ' ')
NUM_SISTEMA=$(wc -l < "$DATOS/apps_sistema.txt" 2>/dev/null | tr -d ' ')
NUM_DESHAB=$(wc -l < "$DATOS/apps_deshabilitadas.txt" 2>/dev/null | tr -d ' ')
[ -z "$NUM_TOTAL" ]   && NUM_TOTAL=0
[ -z "$NUM_USUARIO" ] && NUM_USUARIO=0
[ -z "$NUM_SISTEMA" ] && NUM_SISTEMA=0
[ -z "$NUM_DESHAB" ]  && NUM_DESHAB=0

# ---------------------------------------------------------------------
# 6. ESTADO DEL SISTEMA
# ---------------------------------------------------------------------
echo "[4/8] Estado del sistema (bateria, red, procesos)..."

adb_get dumpsys battery            > "$DATOS/bateria.txt"
adb_get dumpsys wifi               > "$DATOS/wifi.txt"
adb_get dumpsys cpuinfo            > "$DATOS/cpuinfo.txt"
adb_get dumpsys meminfo            > "$DATOS/meminfo.txt"
adb_get dumpsys connectivity       > "$DATOS/conectividad.txt"
adb_get dumpsys telephony.registry > "$DATOS/telefonia.txt"
adb_get dumpsys diskstats          > "$DATOS/diskstats.txt"
adb_get df -h                      > "$DATOS/almacenamiento.txt"
adb_get ip addr                    > "$DATOS/red.txt"
adb_get ps -A                      > "$DATOS/procesos.txt"
adb_get settings list system       > "$DATOS/ajustes_sistema.txt"
adb_get settings list secure       > "$DATOS/ajustes_seguros.txt"
adb_get settings list global       > "$DATOS/ajustes_globales.txt"
adb_get service list               > "$DATOS/servicios.txt"
adb_get cat /proc/cpuinfo          > "$DATOS/cpu_detalle.txt"
adb_get cat /proc/meminfo          > "$DATOS/ram_detalle.txt"

for f in "$DATOS"/*.txt; do
    [ -f "$f" ] && [ ! -s "$f" ] && rm -f "$f"
done

NIVEL_BAT=$(grep -i "^[[:space:]]*level:" "$DATOS/bateria.txt" 2>/dev/null | awk '{print $2}' | head -1)
ESTADO_BAT=$(grep -i "^[[:space:]]*status:" "$DATOS/bateria.txt" 2>/dev/null | awk '{print $2}' | head -1)
SALUD_BAT=$(grep -i "^[[:space:]]*health:" "$DATOS/bateria.txt" 2>/dev/null | awk '{print $2}' | head -1)
TEMP_BAT=$(grep -i "^[[:space:]]*temperature:" "$DATOS/bateria.txt" 2>/dev/null | awk '{print $2}' | head -1)
VOLT_BAT=$(grep -i "^[[:space:]]*voltage:" "$DATOS/bateria.txt" 2>/dev/null | awk '{print $2}' | head -1)

case "$ESTADO_BAT" in
    1) ESTADO_BAT_TXT="Desconocido" ;;
    2) ESTADO_BAT_TXT="Cargando" ;;
    3) ESTADO_BAT_TXT="Descargando" ;;
    4) ESTADO_BAT_TXT="No cargando" ;;
    5) ESTADO_BAT_TXT="Llena" ;;
    *) ESTADO_BAT_TXT="$ESTADO_BAT" ;;
esac

case "$SALUD_BAT" in
    1) SALUD_BAT_TXT="Desconocida" ;;
    2) SALUD_BAT_TXT="Buena" ;;
    3) SALUD_BAT_TXT="Sobrecalentamiento" ;;
    4) SALUD_BAT_TXT="Muerta" ;;
    5) SALUD_BAT_TXT="Sobre voltaje" ;;
    6) SALUD_BAT_TXT="Fallo no especificado" ;;
    7) SALUD_BAT_TXT="Fria" ;;
    *) SALUD_BAT_TXT="$SALUD_BAT" ;;
esac

TEMP_BAT_TXT=""
[ -n "$TEMP_BAT" ] && TEMP_BAT_TXT="$(awk -v t="$TEMP_BAT" 'BEGIN {printf "%.1f", t/10}') grados"

VOLT_BAT_TXT=""
[ -n "$VOLT_BAT" ] && VOLT_BAT_TXT=$(awk -v v="$VOLT_BAT" 'BEGIN {printf "%.2f V", v/1000}')

NUM_PROCESOS=$(wc -l < "$DATOS/procesos.txt" 2>/dev/null | tr -d ' ')
[ -z "$NUM_PROCESOS" ] && NUM_PROCESOS=0

IP_WIFI=$(grep -E "wlan[0-9]" "$DATOS/red.txt" 2>/dev/null | grep "inet " | awk '{print $2}' | head -1)
[ -z "$IP_WIFI" ] && IP_WIFI=""

RAM_TOTAL=$(grep "MemTotal" "$DATOS/ram_detalle.txt" 2>/dev/null | awk '{printf "%.2f GB", $2/1024/1024}')
[ -z "$RAM_TOTAL" ] && RAM_TOTAL=""

CPU_MODELO=$(grep -m1 "Hardware" "$DATOS/cpu_detalle.txt" 2>/dev/null | cut -d':' -f2- | sed 's/^[[:space:]]*//')
[ -z "$CPU_MODELO" ] && CPU_MODELO=$(grep -m1 "model name" "$DATOS/cpu_detalle.txt" 2>/dev/null | cut -d':' -f2- | sed 's/^[[:space:]]*//')

NUM_CORES=$(grep -c "^processor" "$DATOS/cpu_detalle.txt" 2>/dev/null)
[ "$NUM_CORES" = "0" ] && NUM_CORES=""

# ---------------------------------------------------------------------
# 7. CALCULOS Y HASHES
# ---------------------------------------------------------------------
echo "[5/8] Calculando tamanos..."

TAMANO_TOTAL=$(du -sh "$ALMACEN" 2>/dev/null | awk '{print $1}')
[ -z "$TAMANO_TOTAL" ] && TAMANO_TOTAL="0"
NUM_FICHEROS=$(find "$ALMACEN" -type f 2>/dev/null | wc -l | tr -d ' ')

echo "[6/8] Generando manifiesto SHA-256..."
(cd "$BASE" && find . -type f -not -name "hashes.sha256" -exec sha256sum {} \;) > "$BASE/hashes.sha256" 2>/dev/null

CARPETAS_HTML=""
for dir in "$ALMACEN"/*/; do
    if [ -d "$dir" ]; then
        nombre=$(basename "$dir")
        tam=$(du -sh "$dir" 2>/dev/null | awk '{print $1}')
        num=$(find "$dir" -type f 2>/dev/null | wc -l | tr -d ' ')
        CARPETAS_HTML+="<tr><td>$nombre</td><td>$tam</td><td>$num</td></tr>"
    fi
done

APPS_HTML=""
APPS_COUNT=0
while IFS= read -r linea; do
    [ $APPS_COUNT -ge 100 ] && break
    paquete=$(echo "$linea" | sed 's/.*=//' | escape_html)
    ruta=$(echo "$linea" | sed 's/package://' | sed 's/=.*//' | escape_html)
    [ -z "$paquete" ] && continue
    APPS_HTML+="<tr><td>$paquete</td><td><code>$ruta</code></td></tr>"
    APPS_COUNT=$((APPS_COUNT + 1))
done < "$DATOS/apps_usuario.txt"

# ---------------------------------------------------------------------
# 8. INFORME HTML
# ---------------------------------------------------------------------
echo "[7/8] Generando informe HTML..."

fila_si_existe() {
    local etiqueta="$1"; local valor="$2"
    if [ -n "$valor" ]; then
        echo "<dt>$etiqueta</dt><dd><code>$(echo "$valor" | escape_html)</code></dd>"
    fi
}

ICON_PHONE='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>'
ICON_BUILDING='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z"/></svg>'
ICON_LAYERS='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/></svg>'
ICON_BATTERY='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="6" width="18" height="12" rx="2"/><line x1="22" y1="11" x2="22" y2="13"/></svg>'
ICON_APP='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>'
ICON_COG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 10v6M4.22 4.22l4.24 4.24m7.08 7.08l4.24 4.24M1 12h6m10 0h6M4.22 19.78l4.24-4.24m7.08-7.08l4.24-4.24"/></svg>'
ICON_BOLT='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
ICON_DOWNLOAD='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>'
ICON_DATABASE='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>'
ICON_FILE='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
ICON_FOLDER='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
ICON_SEARCH='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
ICON_CHIP='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="22"/><line x1="15" y1="20" x2="15" y2="22"/><line x1="20" y1="9" x2="22" y2="9"/><line x1="20" y1="15" x2="22" y2="15"/><line x1="2" y1="9" x2="4" y2="9"/><line x1="2" y1="15" x2="4" y2="15"/></svg>'
ICON_WIFI='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>'

TARJETAS=""
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_PHONE</div><div class=\"card-label\">Modelo</div><div class=\"card-value\">$(echo "$MODELO" | escape_html)</div></div>"
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_BUILDING</div><div class=\"card-label\">Fabricante</div><div class=\"card-value\">$(echo "$MARCA" | escape_html)</div></div>"
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_LAYERS</div><div class=\"card-label\">Android</div><div class=\"card-value\">$ANDROID_VER<span class=\"badge\">API $SDK</span></div></div>"
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_COG</div><div class=\"card-label\">Capa</div><div class=\"card-value\">$(echo "$CAPA" | escape_html)</div></div>"
[ -n "$NIVEL_BAT" ] && TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_BATTERY</div><div class=\"card-label\">Bateria</div><div class=\"card-value\">${NIVEL_BAT}%</div></div>"
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_APP</div><div class=\"card-label\">Apps usuario</div><div class=\"card-value\">$NUM_USUARIO</div></div>"
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_COG</div><div class=\"card-label\">Apps sistema</div><div class=\"card-value\">$NUM_SISTEMA</div></div>"
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_BOLT</div><div class=\"card-label\">Procesos</div><div class=\"card-value\">$NUM_PROCESOS</div></div>"
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_DOWNLOAD</div><div class=\"card-label\">Ficheros</div><div class=\"card-value\">$NUM_FICHEROS</div></div>"
TARJETAS+="<div class=\"card\"><div class=\"card-icon\">$ICON_DATABASE</div><div class=\"card-label\">Datos copiados</div><div class=\"card-value\">$TAMANO_TOTAL</div></div>"

cat > "$INFORME" << HTMLEOF
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Informe Forense Android - $FECHA</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0d1117; --bg2: #161b22; --bg3: #21262d; --border: #30363d;
  --text: #c9d1d9; --text-dim: #8b949e; --accent: #58a6ff;
  --accent2: #79c0ff; --purple: #8957e5;
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
.card-icon { color: var(--accent); width: 28px; height: 28px;
  margin-bottom: 0.8rem; opacity: 0.8; }
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
</style>
</head>
<body>
<div class="container">

<header>
  <h1>$ICON_PHONE Informe Forense Android</h1>
  <p>Generado el $(date '+%d/%m/%Y a las %H:%M:%S') &middot; ID dispositivo: <code>$DISPOSITIVO</code></p>
</header>

<div class="grid">
$TARJETAS
</div>

<section>
  <h2>$ICON_SEARCH Identificacion del dispositivo</h2>
  <dl class="info-grid">
$(fila_si_existe "Modelo"           "$MODELO")
$(fila_si_existe "Fabricante"       "$MARCA")
$(fila_si_existe "Marca"            "$BRAND")
$(fila_si_existe "Codename"         "$DEVICE")
$(fila_si_existe "Numero de serie"  "$SERIE")
$(fila_si_existe "Arquitectura"     "$ARQ")
$(fila_si_existe "Bootloader"       "$BOOTLOADER")
$(fila_si_existe "Banda base"       "$RADIO")
$(fila_si_existe "Build ID"         "$BUILD")
$(fila_si_existe "Capa del sistema" "$CAPA")
$(fila_si_existe "Parche seguridad" "$SEGURIDAD")
$(fila_si_existe "Kernel"           "$KERNEL")
$(fila_si_existe "Idioma sistema"   "$IDIOMA")
$(fila_si_existe "Zona horaria"     "$TIMEZONE")
$(fila_si_existe "Huella sistema"   "$HUELLA")
  </dl>
</section>

HTMLEOF

if [ -n "$CPU_MODELO" ] || [ -n "$NUM_CORES" ] || [ -n "$RAM_TOTAL" ]; then
cat >> "$INFORME" << HTMLEOF
<section>
  <h2>$ICON_CHIP Hardware</h2>
  <dl class="info-grid">
$(fila_si_existe "CPU"       "$CPU_MODELO")
$(fila_si_existe "Nucleos"   "$NUM_CORES")
$(fila_si_existe "RAM total" "$RAM_TOTAL")
  </dl>
</section>
HTMLEOF
fi

if [ -n "$NIVEL_BAT" ]; then
cat >> "$INFORME" << HTMLEOF
<section>
  <h2>$ICON_BATTERY Estado de la bateria</h2>
  <dl class="info-grid">
$(fila_si_existe "Nivel"       "${NIVEL_BAT}%")
$(fila_si_existe "Estado"      "$ESTADO_BAT_TXT")
$(fila_si_existe "Salud"       "$SALUD_BAT_TXT")
$(fila_si_existe "Temperatura" "$TEMP_BAT_TXT")
$(fila_si_existe "Voltaje"     "$VOLT_BAT_TXT")
  </dl>
</section>
HTMLEOF
fi

if [ -n "$IP_WIFI" ]; then
cat >> "$INFORME" << HTMLEOF
<section>
  <h2>$ICON_WIFI Conectividad</h2>
  <dl class="info-grid">
$(fila_si_existe "IP WiFi" "$IP_WIFI")
  </dl>
</section>
HTMLEOF
fi

if [ "$NUM_FICHEROS" -gt 0 ]; then
cat >> "$INFORME" << HTMLEOF
<section>
  <h2>$ICON_DATABASE Almacenamiento extraido</h2>
  <p style="margin-bottom: 1rem;">Total: <strong>$TAMANO_TOTAL</strong> en <strong>$NUM_FICHEROS</strong> archivos.</p>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Carpeta</th><th>Tamano</th><th>Ficheros</th></tr></thead>
    <tbody>$CARPETAS_HTML</tbody>
  </table>
  </div>
</section>
HTMLEOF
fi

if [ "$NUM_USUARIO" -gt 0 ]; then
cat >> "$INFORME" << HTMLEOF
<section>
  <h2>$ICON_APP Aplicaciones de usuario<span class="badge">$NUM_USUARIO instaladas</span></h2>
  <p style="margin-bottom: 1rem; color: var(--text-dim); font-size: 0.9rem;">Listado completo en <code>datos_forenses/apps_usuario.txt</code>.</p>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Paquete</th><th>Ruta del APK</th></tr></thead>
    <tbody>$APPS_HTML</tbody>
  </table>
  </div>
</section>
HTMLEOF
fi

cat >> "$INFORME" << HTMLEOF
<section>
  <h2>$ICON_FOLDER Archivos generados</h2>
  <ul class="archivos">
HTMLEOF

for f in "$DATOS"/*.txt; do
    if [ -f "$f" ]; then
        nombre=$(basename "$f")
        echo "    <li>$ICON_FILE<code>datos_forenses/$nombre</code></li>" >> "$INFORME"
    fi
done

echo "    <li>$ICON_FOLDER<code>almacenamiento_interno/</code></li>" >> "$INFORME"
echo "    <li>$ICON_FILE<code>hashes.sha256</code></li>" >> "$INFORME"

cat >> "$INFORME" << HTMLEOF
  </ul>
</section>

<footer>
  Informe generado automaticamente por el script de backup forense<br>
  Adrian Fernandez &middot; $(date '+%Y')
</footer>

</div>
</body>
</html>
HTMLEOF

echo "[8/8] Listo."
echo ""
echo "================================================================="
echo " COMPLETADO"
echo "================================================================="
echo " Carpeta:  $BASE"
echo " Informe:  $INFORME"
echo " Tamano:   $TAMANO_TOTAL ($NUM_FICHEROS archivos)"
echo " Hashes:   $BASE/hashes.sha256"
echo ""
echo " Abrir informe:"
echo "   xdg-open \"$INFORME\""
echo "================================================================="