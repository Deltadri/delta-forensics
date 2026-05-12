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

---

## Requisitos del sistema

### Obligatorios

| Herramienta | Version minima | Para que se usa |
|-------------|---------------|-----------------|
| Python | 3.8+ | Ejecutar los scripts |
| ADB | cualquiera | Comunicacion con el dispositivo |

> Los scripts usan unicamente modulos de la libreria estandar de Python (sqlite3, hashlib, subprocess, pathlib, base64, tarfile). No hay dependencias pip.

### Opcionales (solo para extraccion de WhatsApp)

| Herramienta | Necesaria para | Como instalar |
|---|---|---|
| **Java 11+** | Metodo `legacy` (descifrar el `.ab` con `abe.jar`) | `sudo apt install openjdk-17-jdk` (ver seccion Instalacion) |
| **wa-crypt-tools** | Metodo `crypt15` con descifrado (`--wa-key`) | `pip install wa-crypt-tools` |

> `abe.jar` y los APKs candidatos (`LegacyWhatsApp.apk`, etc.) ya estan incluidos en el repo. `wa-crypt-tools` lo instalas si vas a usar `--wa-key` o `--wa-key-file`; sin ellos no hace falta (pero el output queda cifrado).

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

Extrae almacenamiento, apps, estado del sistema y opcionalmente WhatsApp. Genera un informe HTML.

> En Windows usa `python` en lugar de `python3`.

### Que metodo de extraccion WhatsApp usar — guia rapida

```
              ¿Que Android tiene el movil?
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
    Android <= 13   Android 14    Android 15+
        │               │               │
        ▼               ▼               ▼
   metodo legacy   metodo legacy   metodo crypt15
   (auto lo elige)  (auto lo elige   (auto lo elige
                    si funciona)     siempre)
                                     ¿Quieres datos
                                     descifrados?
                                          │
                                  ┌───────┴───────┐
                                  ▼               ▼
                                 SI               NO
                          Pide la clave    Te basta con
                          de 64 hex al     preservar los
                          titular y pasa   .crypt15 + Media
                          --wa-key X
```

**Tu primera ejecucion deberia ser siempre:**

```bash
python3 forense_android.py
```

El script diagnostica el dispositivo, te dice que metodo va a usar, y te explica en el log que flags necesitas si quieres mejor resultado. **No hay que adivinar nada**.

### Tabla completa de flags

| Flag | Por defecto | Descripcion |
|---|---|---|
| `--skip-wa` | desactivado | Omite la fase 5/8 entera (sin tocar WhatsApp). El resto del backup forense corre normal. |
| `--only-wa` | desactivado | Lo contrario: salta las fases pesadas (2/8 almacenamiento, 3/8 apps, 4/8 estado) y va directo a WhatsApp. Util para reintentos rapidos. Incompatible con `--skip-wa`. |
| `--wa-method {auto,legacy,crypt15}` | `auto` | Elige metodo de extraccion WhatsApp. Ver tabla abajo. |
| `--wa-key HEX64` | — | Clave de 64 hex para descifrar `.crypt15`. Acepta `:`, `-`, espacios como separadores. Ej: `--wa-key 1234...abcd`. |
| `--wa-key-file PATH` | — | Alternativa a `--wa-key`: ruta al fichero `encrypted_backup.key` (binario). |
| `--device SERIAL` | autodetectado | Serial del dispositivo si hay >1 conectado. Sacalo de `adb devices`. Sin este flag, el script aborta cuando detecta varios moviles. |
| `--restore-wa DIR` | — | **Modo recuperacion**: reinstala WhatsApp desde una carpeta `apks_originales` de un run anterior que fallo a mitad. No hace el backup forense general. |

### Los 3 modos de `--wa-method`

| `--wa-method` | Que hace | Cuando usarlo | Requisitos |
|---|---|---|---|
| **`auto`** (default) | Diagnostica el dispositivo y elige `legacy` si es viable, si no `crypt15`. Si `legacy` falla cae a `crypt15`. | **Siempre** salvo que sepas exactamente que quieres. | Ninguno |
| **`legacy`** | Fuerza: desinstala WA -> instala WA viejo -> `adb backup` -> reinstala WA original. Te da `msgstore.db` plaintext directamente. | Android <= 13 o casos donde sabes que va a colar. | `java` en PATH |
| **`crypt15`** | Solo `adb pull` de `/sdcard/Android/media/com.whatsapp/WhatsApp/`. Salida cifrada salvo que pases `--wa-key`. NO modifica el WhatsApp del movil. | Android 14+ o cuando quieres CERO riesgo de tocar el WhatsApp del titular. | `pip install wa-crypt-tools` (solo si `--wa-key`) |

### Ejemplos por escenario

**Mi movil es Android 13 o anterior (OPPO, Samsung, Pixel viejo, etc.):**

```bash
# El default ya hace lo correcto:
python3 forense_android.py
```
Te genera `msgstore.db` plaintext directamente via legacy.

**Mi movil es Android 14/15 y SI puedo pedir la clave al titular:**

```bash
# 1) El titular en SU movil:
#    WA -> Ajustes -> Chats -> Copia de seguridad -> "Copia E2E" -> activar
#    Elegir "Usar clave de 64 digitos" (NO password)
#    Apuntar la clave que sale
#    Pulsar "Hacer copia" (boton verde)

# 2) En tu portatil con el movil enchufado:
pip install wa-crypt-tools
python3 forense_android.py \
    --wa-method crypt15 \
    --wa-key 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
```
Te genera `~/backup_movil/.../whatsapp/decrypted/msgstore.db` plaintext que luego pasas a `wa_viewer.py`.

**Mi movil es Android 14/15 y NO tengo la clave (preservacion forense):**

```bash
python3 forense_android.py --wa-method crypt15
```
Te baja los `.crypt15` cifrados + Media en claro + hashes SHA-256 + instrucciones de descifrado en el `informe.html`. Para descifrar despues cuando tengas la clave: `wadecrypt <CLAVE> msgstore.db.crypt15 msgstore.db`.

**Solo me interesa el backup forense (no WhatsApp):**

```bash
python3 forense_android.py --skip-wa
```

**Solo me interesa WhatsApp, sáltate todo lo demás (extraccion rapida o reintento):**

```bash
python3 forense_android.py --only-wa --wa-method crypt15 --wa-key <CLAVE_64HEX>
```
El script salta las fases 2/8 (almacenamiento), 3/8 (apps) y 4/8 (estado del sistema). Sigue generando el informe HTML y los hashes SHA-256 para los ficheros de WhatsApp. Tarda 30 s en vez de 10-15 min.

**Tengo varios moviles conectados:**

```bash
adb devices
# Apunta el serial del que quieras

python3 forense_android.py --device 007519410a0c148c
```

**Un run anterior fallo a mitad y me dejo WhatsApp roto:**

```bash
# La carpeta apks_originales esta dentro del backup anterior
python3 forense_android.py --restore-wa ~/backup_movil/2026-05-11_22-30-00/whatsapp/apks_originales
```
Esto solo reinstala WhatsApp con los APKs guardados, sin tocar nada mas.

### Como conseguir la clave de 64 hex

**Solo el titular del dispositivo puede sacarla**. No hay forma de extraerla sin root. Pasos en el movil:

1. Abre WhatsApp.
2. **Ajustes** → **Chats** → **Copia de seguridad**.
3. **"Copia de seguridad cifrada de extremo a extremo"** → Activar.
4. Elegir **"Usar clave de cifrado de 64 digitos"** (NO "Crear contrasena").
5. Confirmar con PIN/huella del movil.
6. WhatsApp muestra una clave de **64 caracteres hex**. Screenshot o anotala — si la pierdes no es recuperable.
7. Pulsar **"Hacer copia"** (boton verde) para forzar un backup fresco con esa clave.

Despues, pasale la clave al script con `--wa-key <CLAVE>` (con o sin separadores `:` / `-` / espacios, da igual). Si prefieres, exportala como fichero y usa `--wa-key-file`.

### Que ficheros genera el script segun el metodo

**Caso A — Metodo `legacy` (Android <= 13, OPPO Android 14, etc.):**

```
~/backup_movil/YYYY-MM-DD_HH-MM-SS/
├── informe.html              <- Informe HTML navegable
├── hashes.sha256             <- Hashes SHA-256 de cada fichero (cadena de custodia)
├── log.txt                   <- Log completo de la ejecucion
├── almacenamiento_interno/   <- Copia de /storage/emulated/0/
├── datos_forenses/           <- dumpsys, ps, getprop, listas de apps...
└── whatsapp/
    ├── whatsapp.ab           <- Backup Android raw (.ab)
    ├── apks_originales/      <- APKs del WA original (por si hay que restaurar)
    └── extracted/
        └── apps/com.whatsapp/db/
            ├── msgstore.db   ★ <- Chats en SQLite plaintext
            └── wa.db         ★ <- Contactos en SQLite plaintext
```

**Caso B — Metodo `crypt15` SIN clave del usuario:**

```
~/backup_movil/YYYY-MM-DD_HH-MM-SS/
├── informe.html              <- Informe HTML (incluye seccion "WhatsApp crypt15"
│                                 con tabla de ficheros + SHA-256 + instrucciones)
├── hashes.sha256
├── log.txt
├── almacenamiento_interno/
├── datos_forenses/
└── whatsapp/
    └── external/
        └── Android_media/
            ├── Databases/
            │   ├── msgstore.db.crypt15            <- ULTIMO backup (anoche ~2am), CIFRADO
            │   ├── msgstore-YYYY-MM-DD.1.db.crypt15  <- backups historicos cifrados
            │   ├── wa.db.crypt15                  <- contactos cifrado
            │   └── ...
            ├── Media/                             <- todo SIN cifrar
            │   ├── WhatsApp Images/               <- fotos enviadas/recibidas (.jpg)
            │   ├── WhatsApp Video/                <- videos (.mp4)
            │   ├── WhatsApp Voice Notes/          <- audios (.opus)
            │   ├── WhatsApp Documents/            <- PDFs, docs
            │   ├── WhatsApp Stickers/
            │   └── .Statuses/                     <- estados vistos en cache
            └── Backups/                           <- formato viejo si existe
```

**Caso C — Metodo `crypt15` CON `--wa-key`:**

Igual que el caso B PERO ademas:

```
~/backup_movil/YYYY-MM-DD_HH-MM-SS/whatsapp/
└── decrypted/
    ├── msgstore.db           ★ <- Chats en SQLite plaintext (descifrado al vuelo)
    ├── wa.db                 ★ <- Contactos plaintext (descifrado al vuelo)
    └── msgstore-2026-05-11.1.db    <- backups historicos descifrados
```

★ son los ficheros que luego pasas a `wa_viewer.py` para ver los chats en HTML.

### Como se invoca todo en orden

1. Script arranca → diagnostica el movil + log.
2. Decide el metodo (auto / forzado por flag).
3. Pull del almacenamiento + apps + estado del sistema.
4. **Fase 5/8 WhatsApp** segun metodo elegido.
5. Genera `informe.html` con todos los datos + hashes SHA-256.
6. Termina mostrando el resumen y el comando para abrir el informe.

Si el flujo cae a mitad por Ctrl-C o crash, el script restaura WhatsApp automaticamente (solo aplica al metodo `legacy`; el `crypt15` no tiene nada que restaurar porque no modifica el movil).

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

#### Flags de `wa_viewer.py`

| Flag | Por defecto | Descripcion |
|---|---|---|
| `--msgstore` | `db/msgstore.db` | Ruta al `msgstore.db` plaintext extraido por `forense_android.py`. |
| `--wadb` | `db/wa.db` | Ruta al `wa.db` plaintext. |
| `--output` | `wa_viewer.html` | Fichero HTML de salida. |
| `--contacts-vcf` | — | Ruta a un `.vcf` con la libreta del telefono. Anade nombres de contactos privados a los chats. Prevalece sobre los nombres que WhatsApp guarda internamente. Ver explicacion abajo. |
| `--default-cc` | `34` | Codigo de pais por defecto para los telefonos del VCF que vienen en formato local sin prefijo `+` (ej. `654-117-918` -> `+34654117918`). Cambialo si tu agenda esta en otro pais. |

#### Por que `--contacts-vcf` (chats privados que salen con numero)

A partir de WhatsApp **2.26 / Android 16**, los nombres de los contactos individuales **ya no se guardan dentro de las BDs de WhatsApp**. WhatsApp los lee en runtime de la libreta del sistema Android, que esta fuera del backup. Por eso:

- **Grupos**: siempre salen con su nombre (esta en `chat.subject` de `msgstore.db`).
- **Chats privados**: salen con el numero de telefono, salvo que la persona te haya escrito via LID y exista mapeo LID->JID, o haya sido mencionada en un grupo.

Pasar el VCF de tu libreta resuelve esos chats privados.

#### Como exportar el `.vcf` desde Android

**Desde la app oficial de Google Contacts (Android):**

1. Abre la app **Contactos** de Google en el movil.
2. En la barra inferior pulsa **Organizar** (icono abajo a la derecha).
3. Pulsa **Exportar a archivo** -> elige la cuenta de Google que quieras exportar.
4. Se genera un `contacts.vcf` (normalmente en `Descargas/`).
5. Pasa el `.vcf` al ordenador (cable USB, Drive, lo que prefieras).

> Si tu version de Contactos no muestra "Organizar", el flujo equivalente esta en **menu ☰ -> Ajustes -> Exportar -> Exportar a archivo .vcf**. Algunos OEM (Samsung, Xiaomi) tienen su propia app de Contactos con un flujo similar bajo **Ajustes -> Importar/Exportar contactos -> Exportar al almacenamiento**.

**Ejemplo de uso con VCF:**

```bash
# Linux / macOS
python3 wa_viewer.py \
    --msgstore ~/backup_movil/2026-05-11/whatsapp/extracted/apps/com.whatsapp/db/msgstore.db \
    --wadb     ~/backup_movil/2026-05-11/whatsapp/extracted/apps/com.whatsapp/db/wa.db \
    --contacts-vcf ~/Descargas/contacts.vcf \
    --output   chats_whatsapp.html

# Windows
python wa_viewer.py ^
    --msgstore "%USERPROFILE%\backup_movil\2026-05-11\whatsapp\extracted\apps\com.whatsapp\db\msgstore.db" ^
    --wadb     "%USERPROFILE%\backup_movil\2026-05-11\whatsapp\extracted\apps\com.whatsapp\db\wa.db" ^
    --contacts-vcf "%USERPROFILE%\Downloads\contacts.vcf" ^
    --output   chats_whatsapp.html
```

El parser entiende vCard 2.1/3.0, soporta tildes/ñ (decodifica `QUOTED-PRINTABLE`), descarta numeros cortos de servicio (1004, 1470, etc.) que no son JIDs WhatsApp validos, y normaliza formatos locales (`654-117-918`, `0034...`, `+34 ...`) al JID `<num>@s.whatsapp.net`.

---

## Estructura del repositorio

```
delta-forensics/
├── forense_android.py        # Suite principal (backup + WA + informe)
├── wa_viewer.py              # Visor HTML de chats WhatsApp
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

| Sistema | `forense_android.py` | `wa_viewer.py` |
|---------|---------------------|----------------|
| Linux (Ubuntu, Debian, Fedora, Arch) | ✅ Probado | ✅ Probado |
| Windows | 🟡 No verificado — debe funcionar (stdlib + adb.exe + java.exe en PATH) pero **no esta confirmado**. Si lo pruebas, abre un issue. | ✅ Probado |
| macOS   | 🟡 No probado pero **deberia** funcionar igual que Linux | 🟡 No probado |

> **El entorno de produccion soportado es Linux** (Ubuntu 22.04+ y derivadas). Es donde se ha desarrollado y donde se ejecutan los runs reales.

Para la matriz por **dispositivo Android** ver el apartado [Estado de compatibilidad real](#%EF%B8%8F-estado-de-compatibilidad-real) al principio del README.

---

## Troubleshooting (problemas comunes)

### `adb devices` muestra `unauthorized`

El movil no acepto la huella RSA o la revoco. En el movil → **Opciones de desarrollador** → "Revocar autorizaciones de depuracion USB" → desconectar cable → reconectar → aceptar la huella **marcando "Permitir siempre desde este ordenador"**.

### El script aborta con "Multiples dispositivos conectados"

Es proteccion forense para no actuar sobre el movil equivocado. Soluciones:
- Desconecta los demas moviles, deja solo el de interes.
- O pasa `--device <SERIAL>` (lo sacas de `adb devices`).

### Tras `adb reboot` el movil queda en `unauthorized` (Android 14/15 / BBK)

El script lo detecta y espera 90 s a que reaceptes la huella RSA en el movil. **Tenlo a mano cuando reinicie**. Si no aparece el dialogo: Opciones de desarrollador → "Revocar autorizaciones de depuracion USB" → reconectar.

### `INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE` en el metodo legacy

Tu movil es Android 14+ con WhatsApp moderno (`targetSdk >= 23`). **El metodo legacy no puede funcionar** en este escenario, es un bloqueo del propio Android. Usa `--wa-method crypt15` en su lugar.

### `adb backup` produce un `.ab` vacio (solo cabecera, 0 datos)

Tu OEM (probablemente Huawei/EMUI) bloquea silenciosamente `adb backup`. **No hay forma programatica de sortearlo**. Cae al metodo `crypt15`.

### El script dice "WhatsApp esta en estado uninstalled-keep-data"

Un run anterior fallo entre el `pm uninstall -k` y el reinstall, dejando huerfanos los datos. **Tus chats siguen ahi**. Recupera con:

```bash
ls ~/backup_movil/*/whatsapp/apks_originales/  # localiza la carpeta del run anterior
python3 forense_android.py --restore-wa ~/backup_movil/YYYY-MM-DD_XX/whatsapp/apks_originales
```

### `wadecrypt: command not found`

Instala `wa-crypt-tools`:
```bash
pip install wa-crypt-tools
```
Si trabajas en un entorno aislado (PEP 668 en Ubuntu reciente):
```bash
pipx install wa-crypt-tools
# o:
python3 -m venv venv && source venv/bin/activate && pip install wa-crypt-tools
```

### El `.crypt15` no se descifra con la clave que te dio el titular

Causas tipicas:
- La clave es de **otra cuenta** de WhatsApp (el titular tiene 2 numeros y se confundio).
- El backup fue creado **antes** de activar E2E en el movil — esos `.crypt15` viejos usan la clave local (root-only) no la de 64 hex. Pide al titular que pulse "Hacer copia" para generar uno fresco con la nueva clave, y vuelve a hacer pull.
- El titular eligio **contrasena** en lugar de "clave de 64 digitos" en la pantalla E2E de WhatsApp. wa-crypt-tools soporta password-based decryption pero no se invoca con `--wa-key`. En ese caso usa `wadecrypt` directamente con `--password` (consulta `wadecrypt --help`).

### El backup forense general funciona pero la fase 5/8 (WhatsApp) falla en todo

Mientras `[1/8]` a `[4/8]` y `[6/8]` a `[8/8]` terminen bien, ya tienes el backup completo de almacenamiento + apps + estado del sistema + informe HTML. Solo te falta WhatsApp. Es un fallo parcial recuperable: relanza con `--wa-method crypt15` o salta WhatsApp con `--skip-wa` si no lo necesitas.

### Ayuda

Si tu problema no esta cubierto aqui, [abre un issue](https://github.com/Deltadri/delta-forensics/issues/new) con:
- Modelo del movil + version Android + OS skin
- El comando exacto que lanzaste
- Las ultimas 20-30 lineas del log (`log.txt` dentro del backup)

---

## Aviso legal

Esta herramienta esta desarrollada con fines educativos y de analisis forense autorizado. Unicamente debe utilizarse en dispositivos propios o con autorizacion expresa del propietario. El uso no autorizado puede ser constitutivo de delito.

---

## Autor

Deltadri — Practica de analisis forense de dispositivos Android
