# delta-forensics

Suite de herramientas forenses para dispositivos Android. Extrae datos del dispositivo via ADB, genera un informe HTML completo y permite visualizar conversaciones de WhatsApp en una interfaz web interactiva.

---

## ⚠️ Estado de compatibilidad real

`forense_android.py` ofrece **dos metodos** de extraccion de WhatsApp y elige automaticamente:

- **Metodo `legacy`** (clasico): desinstala WhatsApp moderno -> instala WhatsApp viejo via ADB -> `adb backup` -> reinstala el moderno. Solo funciona si el dispositivo es Android ≤ 13 o el WhatsApp actual tiene `targetSdk < 23`.
- **Metodo `crypt15`** (no invasivo, fallback): solo hace `adb pull` de `/sdcard/Android/media/com.whatsapp/WhatsApp/` (DBs cifradas + Media sin cifrar). No desinstala nada. Para obtener las DBs en plaintext el titular tiene que activar "Copia E2E" en WhatsApp y aportar la clave de 64 hex (`--wa-key`).

Estado real de los metodos por dispositivo probado:

| Fabricante | Android | OS skin | Metodo legacy | Metodo crypt15 | Notas |
|---|---|---|---|---|---|
| **OPPO** | **14** | ColorOS | ✅ Funciona | ✅ Funciona (no probado todavia, debe funcionar) | El unico escenario con `msgstore.db` plaintext extraido via legacy |
| Realme C71 | 15 | ColorOS 15 (BBK) | ❌ `INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE` | ✅ Pull funciona (no probado descifrado) | El backup forense general completa; WA requiere clave del titular |
| Huawei P Smart | 9 (EMUI 9) | EMUI | ❌ `adb backup` vuelve vacio | 🟡 Probable que funcione el pull crypt15 | EMUI bloquea backup pero no el pull de /sdcard |

**En la practica:**

- Si tu dispositivo es **Android 13 o anterior** (cualquier fabricante salvo Huawei/EMUI 9+) tiene MUY altas probabilidades de funcionar completo, igual que el OPPO probado.
- Si tu dispositivo es **Android 14** (cualquier fabricante salvo Huawei/EMUI o BBK con Permission Monitor activo) deberia funcionar — pero solo se ha confirmado en OPPO ColorOS, ten el movil a mano por si falla algun preflight.
- Si tu dispositivo es **Android 15+** la extraccion WA **probablemente fallara** por el bloqueo de Android contra el downgrade del modelo de permisos. El backup forense general seguira funcionando.
- Si tu dispositivo es **Huawei/Honor con EMUI 9+** la extraccion WA es **inviable** por bloqueo de OEM.

`wa_viewer.py` es **independiente del dispositivo** — solo necesita el `msgstore.db` y `wa.db` ya descifrados. Si el `forense_android.py` consiguio extraerlos en cualquier movil, el viewer los va a procesar correctamente.

### 🙋 Ayuda a ampliar esta lista

Si has probado el script con un dispositivo que **NO** aparece en la tabla — funcione o no funcione — por favor [abre un issue en el repo](https://github.com/Deltadri/delta-forensics/issues/new) indicando:

- **Fabricante y modelo exacto** (p.ej. `Samsung Galaxy A54`, `Xiaomi Redmi Note 12`)
- **Version de Android** (Ajustes → Acerca del telefono → Version de Android)
- **OS skin** y version si aplica (OneUI 6.1, MIUI 14, ColorOS 13, etc.)
- **Resultado**: backup forense general (si/no), extraccion WhatsApp (si/no)
- Si falla, **el ultimo bloque de log relevante** (especialmente las lineas `[WA]` y cualquier `[ERROR]`)
- Cualquier preflight necesario que no esta documentado (toggles especificos del OEM, etc.)

Cuantas mas confirmaciones recibamos mas precisa sera la matriz y menos sorpresas tendran los proximos usuarios.

---

## Herramientas

| Script | Descripcion |
|--------|-------------|
| `forense_android.py` | Backup forense completo + extraccion WhatsApp + informe HTML |
| `wa_viewer.py` | Visor de chats WhatsApp en HTML desde `msgstore.db` |
| `backup_android.sh` | Version shell del backup (solo Linux, sin extraccion WA) |

---

## Requisitos del sistema

### Obligatorios

| Herramienta | Version minima | Para que se usa |
|-------------|---------------|-----------------|
| Python | 3.8+ | Ejecutar los scripts |
| ADB | cualquiera | Comunicacion con el dispositivo |

> Los scripts usan unicamente modulos de la libreria estandar de Python (sqlite3, hashlib, subprocess, pathlib, base64, tarfile). No hay dependencias pip.

### Opcionales (solo para extraccion de WhatsApp)

| Herramienta | Para que se usa |
|-------------|-----------------|
| Java 11+ | Ejecutar `abe.jar` (extrae el backup `.ab`) |

> `abe.jar` y `LegacyWhatsApp.apk` ya estan incluidos en el repo.

---

## Instalacion

### Linux

#### ADB

```bash
# Ubuntu / Debian
sudo apt install adb

# Fedora / RHEL
sudo dnf install android-tools

# Arch Linux
sudo pacman -S android-tools
```

#### Java (solo si vas a extraer WhatsApp)

```bash
# Ubuntu / Debian
sudo apt install openjdk-17-jdk

# Fedora / RHEL
sudo dnf install java-17-openjdk

# Arch Linux
sudo pacman -S jdk17-openjdk
```

#### Clonar el repositorio

```bash
git clone https://github.com/Deltadri/delta-forensics.git
cd delta-forensics
```

---

### Windows

#### ADB

```powershell
winget install -e --id Google.PlatformTools
```

#### Java (solo si vas a extraer WhatsApp)

```powershell
winget install EclipseAdoptium.Temurin.17.JDK
```

#### Python

```powershell
winget install -e --id Python.Python.3.13 --scope machine
```

#### Clonar el repositorio

```powershell
git clone https://github.com/Deltadri/delta-forensics.git
cd delta-forensics
```

---

### Preparar archivos opcionales para extraccion WhatsApp

`LegacyWhatsApp.apk` y `abe.jar` ya estan incluidos en el repo. No necesitas descargar nada extra.

---

## Preparar el dispositivo Android

1. Activa **Opciones de desarrollador**: Ajustes → Acerca del telefono → pulsa 7 veces "Numero de compilacion"
2. Activa **Depuracion USB**: Ajustes → Sistema → Opciones de desarrollador → Depuracion USB: ON
3. Conecta el movil por USB en modo **Transferencia de archivos (MTP)**
4. Acepta la huella RSA que aparece en la pantalla del movil

Verifica la conexion:

```bash
adb devices
# Debe aparecer tu dispositivo con estado "device"
```

---

## Uso

### `forense_android.py` — Backup forense completo

Extrae almacenamiento, apps, estado del sistema y opcionalmente la base de datos de WhatsApp. Genera un informe HTML.

> En Windows usa `python` en lugar de `python3`.

```bash
# Backup completo + extraccion WhatsApp (modo auto: elige metodo segun diagnostico)
python3 forense_android.py

# Solo backup, sin tocar WhatsApp
python3 forense_android.py --skip-wa

# Forzar metodo legacy (instalar WA viejo + adb backup; solo Android <= 13)
python3 forense_android.py --wa-method legacy

# Forzar metodo crypt15 (no invasivo, pull de /sdcard/Android/media/com.whatsapp/)
python3 forense_android.py --wa-method crypt15

# crypt15 + descifrado automatico con la clave de 64 hex del titular
python3 forense_android.py --wa-method crypt15 --wa-key 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef

# Misma cosa pero leyendo la clave de un fichero (encrypted_backup.key)
python3 forense_android.py --wa-method crypt15 --wa-key-file ~/wa_key.bin

# Si tienes 2 moviles conectados, especifica cual:
python3 forense_android.py --device 007519410a0c148c

# Modo recuperacion: si un run anterior fallo a medias, restaura WhatsApp
python3 forense_android.py --restore-wa ~/backup_movil/2026-05-12_XX/whatsapp/apks_originales
```

### Como conseguir la clave de 64 hex (para `--wa-key`)

Es algo que el **titular del dispositivo** tiene que hacer en su WhatsApp. **No se puede sacar de otra forma sin root**:

1. Abre WhatsApp en el movil.
2. Ajustes -> Chats -> Copia de seguridad.
3. "Copia de seguridad cifrada de extremo a extremo" -> Activar.
4. Elegir **"Usar clave de cifrado de 64 digitos"** (NO "Crear contrasena").
5. Confirmar con PIN/huella del movil.
6. WhatsApp muestra una clave de 64 caracteres hex. **Screenshot o anotar**.
7. Pulsar "Hacer copia" (boton verde) para forzar un backup fresco con esa clave.

Despues, pasale la clave al script con `--wa-key <CLAVE>` o `--wa-key-file <FICHERO>`. wa-crypt-tools (`pip install wa-crypt-tools`) se invoca automaticamente si esta en PATH.

**Que genera:**

```
~/backup_movil/YYYY-MM-DD_HH-MM-SS/
├── informe.html                  <- Informe forense navegable
├── hashes.sha256                 <- Manifiesto de integridad SHA-256
├── log.txt                       <- Log completo de la ejecucion
├── almacenamiento_interno/       <- Copia de /storage/emulated/0/
├── datos_forenses/               <- dumpsys, ps, propiedades, apps...
└── whatsapp/                     <- (si la extraccion WA tuvo exito)
    ├── whatsapp.ab               <- Backup Android raw
    ├── apks_originales/          <- APKs del WA original (para restaurar)
    └── extracted/                <- Base de datos descifrada
        └── apps/com.whatsapp/db/
            ├── msgstore.db       <- Mensajes
            └── wa.db             <- Contactos
```

> **Nota sobre la extraccion WhatsApp:**
> El script tiene **2 metodos** y elige automaticamente segun el diagnostico:
>
> - **Metodo `legacy`**: desinstala WhatsApp moderno temporalmente (manteniendo los datos con `-k`), instala una version antigua con `allowBackup=true`, ejecuta `adb backup` y restaura la version original. Requiere que aceptes el dialogo de backup en el movil. Solo funciona en Android <= 13 o con WhatsApp `targetSdk < 23`.
> - **Metodo `crypt15`**: NO desinstala nada. Solo `adb pull` de `/sdcard/Android/media/com.whatsapp/WhatsApp/`. Las DBs salen cifradas (.crypt15) y se descifran con la clave de 64 hex del titular usando `wa-crypt-tools` (`pip install wa-crypt-tools`). Es el unico metodo que funciona en Android 14+.
>
> En ambos metodos tus mensajes NO se borran. El metodo `legacy` desinstala y reinstala WhatsApp brevemente; el metodo `crypt15` no toca la app en absoluto.

---

### `wa_viewer.py` — Visor de chats WhatsApp

Genera un HTML interactivo con todos los chats, mensajes, miniaturas de imagenes y nombres de contactos.

> En Windows usa `python` en lugar de `python3`.

```bash
# Usando rutas por defecto (db/msgstore.db y db/wa.db)
python3 wa_viewer.py

# Especificando rutas manualmente — Linux/macOS
python3 wa_viewer.py \
    --msgstore ~/backup_movil/2026-05-11/whatsapp/extracted/apps/com.whatsapp/db/msgstore.db \
    --wadb     ~/backup_movil/2026-05-11/whatsapp/extracted/apps/com.whatsapp/db/wa.db \
    --output   chats_whatsapp.html

# Especificando rutas manualmente — Windows
python wa_viewer.py ^
    --msgstore "%USERPROFILE%\backup_movil\2026-05-11\whatsapp\extracted\apps\com.whatsapp\db\msgstore.db" ^
    --wadb     "%USERPROFILE%\backup_movil\2026-05-11\whatsapp\extracted\apps\com.whatsapp\db\wa.db" ^
    --output   chats_whatsapp.html
```

Abre el HTML generado en Chrome o Firefox.

**Caracteristicas del visor:**
- Sidebar con lista de chats y preview del ultimo mensaje
- Nombres de contacto resueltos (incluye soporte para LIDs, el formato nuevo de WhatsApp)
- Miniaturas de imagenes embebidas en base64
- Separadores por dia
- Diseño oscuro estilo WhatsApp
- Funciona sin servidor, es un HTML estatico

---

### `backup_android.sh` — Backup shell (solo Linux)

Version alternativa del backup en bash puro, sin extraccion de WhatsApp.

```bash
chmod +x backup_android.sh
./backup_android.sh
```

---

## Estructura del repositorio

```
delta-forensics/
├── forense_android.py        # Suite principal (backup + WA + informe)
├── wa_viewer.py              # Visor HTML de chats WhatsApp
├── backup_android.sh         # Backup alternativo en shell (Linux)
├── abe/
│   └── abe.jar               # Android Backup Extractor (incluido)
├── legacy_apk/
│   └── LegacyWhatsApp.apk    # APK legacy (2.11.431) incluido — anade aqui mas
│                              # APKs candidatos si lo necesitas (orden alfabetico)
├── .gitignore
└── README.md
```

---

## Compatibilidad por SO del host

| Sistema | `forense_android.py` | `wa_viewer.py` | `backup_android.sh` |
|---------|---------------------|----------------|---------------------|
| Linux (Ubuntu, Debian, Fedora, Arch) | ✅ Probado | ✅ Probado | ✅ Probado |
| Windows | 🟡 No verificado — debe funcionar (stdlib + adb.exe + java.exe en PATH) pero **no esta confirmado**. Si lo pruebas, abre un issue. | ✅ Probado | ❌ Script bash, no aplica |
| macOS   | 🟡 No probado pero **deberia** funcionar igual que Linux | 🟡 No probado | 🟡 Probablemente parcial |

> **El entorno de produccion soportado es Linux** (Ubuntu 22.04+ y derivadas). Es donde se ha desarrollado y donde se ejecutan los runs reales.

Para la matriz por **dispositivo Android** ver el apartado [Estado de compatibilidad real](#%EF%B8%8F-estado-de-compatibilidad-real-probado-no-marketing) al principio del README.

---

## Aviso legal

Esta herramienta esta desarrollada con fines educativos y de analisis forense autorizado. Unicamente debe utilizarse en dispositivos propios o con autorizacion expresa del propietario. El uso no autorizado puede ser constitutivo de delito.

---

## Autor

Deltadri — Practica de analisis forense de dispositivos Android
