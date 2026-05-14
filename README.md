# delta-forensics

Suite de herramientas forenses para dispositivos Android. Extrae datos del dispositivo via ADB, genera un informe HTML completo y permite visualizar conversaciones de WhatsApp en una interfaz web interactiva.

---

## 🚀 Quick Start

Caso tipico: tienes un Android moderno (15 o 16), el titular te da la clave de 64 hex, y quieres ver los chats en HTML.

```bash
# 1) Instala wa-crypt-tools (ver seccion Instalacion abajo para PEP 668 / Windows)
pipx install wa-crypt-tools

# 2) Extrae solo WhatsApp del movil (no toca el resto)
python3 forense_android.py --only-wa --wa-method crypt15 --wa-key TU_CLAVE_DE_64_HEX

# 3) Genera el HTML con nombres de contactos (exporta contacts.vcf desde Contactos de Google)
python3 wa_viewer.py \
    --msgstore ~/backup_movil/<FECHA>/whatsapp/decrypted/msgstore.db \
    --wadb     ~/backup_movil/<FECHA>/whatsapp/decrypted/wa.db \
    --contacts ~/Descargas/contacts.vcf \
    --output   chats.html

# 4) Abre chats.html en Chrome o Firefox.
```

¿Otro escenario? Ver [seccion Uso](#uso) abajo (Android ≤ 14, sin clave, solo backup forense, varios moviles, etc.). ¿Como saca el titular la clave de 64 hex? Ver [Como conseguir la clave](#como-conseguir-la-clave-de-64-hex).

---

## ⚠️ Estado de compatibilidad real

Este repo son **dos herramientas independientes**. No se invocan la una a la otra ni hay pipeline automatico: las ejecutas por separado cuando las necesites.

- **`forense_android.py`** (extractor): habla con el movil via ADB, hace backup forense completo y, entre otras cosas, deja `msgstore.db` + `wa.db` en plaintext.
- **`wa_viewer.py`** (visor): toma un `msgstore.db` + `wa.db` plaintext ya existentes (vengan del extractor de aqui, de otra herramienta forense, o de un backup que ya tenias) y genera un HTML interactivo de chats. No toca el movil.

Cada script tiene su propia matriz de compatibilidad **porque dependen de cosas distintas**: el extractor depende del dispositivo (OEM, version Android, WhatsApp instalado), el visor solo depende de la version de WhatsApp del backup. Las trato por separado abajo.

---

### Parte 1 — Compatibilidad de `forense_android.py` (extractor)

Depende del **dispositivo Android**. Ofrece **dos metodos** de extraccion de WhatsApp y elige automaticamente:

- **Metodo `legacy`** (clasico): desinstala WhatsApp moderno -> instala WhatsApp viejo via ADB -> `adb backup` -> reinstala el moderno. Funciona en Android ≤ 14 (la cadena `pm uninstall -k` + `pm install --bypass-low-target-sdk-block` sortea el bloqueo de Android 14). En Android 15+ ya no es viable: el `PERMISSION_MODEL_DOWNGRADE` es estricto y no hay flag adb que lo sortee.
- **Metodo `crypt15`** (no invasivo, fallback): solo hace `adb pull` de `/sdcard/Android/media/com.whatsapp/WhatsApp/` (DBs cifradas + Media sin cifrar). No desinstala nada. Para obtener las DBs en plaintext el titular tiene que activar "Copia E2E" en WhatsApp y aportar la clave de 64 hex (`--wa-key`).

Estado real de los metodos por dispositivo probado:

| Fabricante | Android | OS skin | Metodo legacy | Metodo crypt15 | Notas |
|---|---|---|---|---|---|
| **OPPO** | **14** | ColorOS | ✅ Funciona | ✅ Funciona (no probado todavia, debe funcionar) | El unico escenario con `msgstore.db` plaintext extraido via legacy |
| Realme C71 | 15 | ColorOS 15 (BBK) | ❌ `INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE` | ✅ Funciona (pull + descifrado con `--wa-key`) | El backup forense general completa; WA requiere clave del titular |
| Realme GT7 | 16 | Realme UI | ❌ `INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE` (Android 16 bloquea el downgrade) | ✅ Funciona | Confirmado en Android 16 — crypt15 sigue siendo viable en la version mas reciente |
| Huawei P Smart | 9 (EMUI 9) | EMUI | ❌ `adb backup` vuelve vacio | 🟡 Probable que funcione el pull crypt15 | EMUI bloquea backup pero no el pull de /sdcard |

**En la practica** — cada situacion tiene un metodo que funciona, raramente te quedas sin opciones:

- **Android 13 o anterior** (cualquier fabricante salvo Huawei/EMUI 9+): el metodo **legacy** funciona y es lo que el script elige por defecto. Te deja directamente `msgstore.db` y `wa.db` en plaintext sin necesidad de clave ni pasos extra. Lanza `python3 forense_android.py` sin flags y listo.

- **Android 14**: legacy *deberia* colar — confirmado en OPPO ColorOS, probable en la mayoria del resto. Si el preflight detecta que no es viable (Realme/OnePlus con Permission Monitor activo, WhatsApp con `targetSdk` demasiado alto, etc.), el script cae automaticamente a **crypt15**. Para obtener los datos en claro pasale entonces `--wa-key` con la clave de 64 hex que te dara el titular (ver mas abajo como sacarla).

- **Android 15 o superior** (incluyendo Android 16): legacy **no funciona** — Android bloquea explicitamente el downgrade del modelo de permisos. El script lo detecta de entrada y elige **crypt15** automaticamente, que **si funciona**. Confirmado en Realme C71 (Android 15) y Realme GT7 (Android 16). Pasa `--wa-key` si quieres los datos descifrados al vuelo; si no, se preservan los `.crypt15` cifrados + Media en claro + hashes SHA-256 + instrucciones de descifrado para hacerlo despues. **El backup forense general (almacenamiento, apps, estado del sistema) corre normal independientemente** del metodo WA elegido.

- **Huawei/Honor con EMUI 9+**: legacy bloqueado silenciosamente — `adb backup` vuelve vacio por restriccion del OEM. **Crypt15 probablemente funcione** (el pull de `/sdcard/Android/media/com.whatsapp/` sigue siendo viable en EMUI porque no depende de `adb backup`), pero todavia no esta confirmado en produccion. Si lo pruebas, [abre un issue](https://github.com/Deltadri/delta-forensics/issues/new) con el resultado.

---

### Parte 2 — Compatibilidad de `wa_viewer.py` (visor)

`wa_viewer.py` es **independiente del dispositivo** — no toca el movil, solo procesa SQLite plaintext local. Puede usarse con DBs extraidas por `forense_android.py` o con DBs de cualquier otro origen (otra herramienta forense, backup ya descifrado, etc.).

La unica variable que importa aqui es la **version de WhatsApp** que tuviera el movil cuando se hizo el backup, porque eso decide donde estan los nombres de los contactos:

| Version de WhatsApp en el movil | Inputs minimos | Resultado en el HTML |
|---|---|---|
| **WhatsApp <= 2.25** (clasico) | `msgstore.db` + `wa.db` | ✅ Todos los nombres correctos. `wa.db` aun tiene `wa_contacts` poblada. |
| **WhatsApp 2.26+ / Android 16** | `msgstore.db` + `wa.db` | 🟡 Grupos OK, **chats privados salen con numero**. WhatsApp ya no guarda los nombres de contactos individuales dentro de la BD — los lee en runtime de la libreta del SO, que esta fuera del backup. |
| **WhatsApp 2.26+ / Android 16** | `msgstore.db` + `wa.db` + `--contacts contacts.vcf` | ✅ Todos los nombres correctos. El `.vcf` se exporta desde la app Contactos del telefono — guia paso a paso en la seccion `wa_viewer.py` mas abajo. |

Es decir: si tu backup viene de un movil con WA moderno y los chats privados te salen con numero, **no es un bug del viewer** — es que falta pasarle la libreta del usuario con `--contacts`.

---

### 🙋 Ayuda a ampliar la matriz

Si has probado el extractor con un dispositivo que **NO** aparece en la tabla de arriba — funcione o no funcione — por favor [abre un issue en el repo](https://github.com/Deltadri/delta-forensics/issues/new) indicando:

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
| **wa-crypt-tools** | Metodo `crypt15` con descifrado (`--wa-key`) | `pipx install wa-crypt-tools` (ver seccion Instalacion — `pip` directo falla en Ubuntu 24.04+ por PEP 668) |

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

#### wa-crypt-tools (solo si vas a usar `--wa-key` o `--wa-key-file`)

En Ubuntu 22.04+ / Debian 12+ / Fedora 38+ no se puede hacer `pip install wa-crypt-tools` directamente — el sistema lo bloquea con `error: externally-managed-environment` (PEP 668). La forma limpia y recomendada es **pipx**, que aisla la herramienta en su propio entorno y deja el binario `wadecrypt` accesible desde cualquier sitio.

```bash
# Ubuntu / Debian
sudo apt install pipx -y
pipx ensurepath
source ~/.bashrc           # recarga el PATH en la terminal actual
pipx install wa-crypt-tools

# Fedora / RHEL
sudo dnf install pipx -y
pipx ensurepath
source ~/.bashrc
pipx install wa-crypt-tools

# Arch Linux
sudo pacman -S python-pipx
pipx ensurepath
source ~/.bashrc
pipx install wa-crypt-tools
```

Verifica que quedo bien:

```bash
which wadecrypt
# Debe imprimir: /home/<usuario>/.local/bin/wadecrypt
```

> **Alternativa con venv** si no quieres usar pipx: `python3 -m venv .venv && source .venv/bin/activate && pip install wa-crypt-tools`. Tendras que activar el venv cada vez que vayas a lanzar `forense_android.py`.

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

#### wa-crypt-tools (solo si vas a usar `--wa-key` o `--wa-key-file`)

En Windows PEP 668 no aplica igual que en Linux, asi que `pip install --user` funciona — pero pipx sigue siendo la opcion mas limpia porque deja `wadecrypt.exe` en PATH sin tocar el Python del sistema:

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
# Cierra y abre PowerShell para recargar el PATH, luego:
pipx install wa-crypt-tools
```

Verifica:

```powershell
Get-Command wadecrypt
# Debe imprimir la ruta a wadecrypt.exe en %USERPROFILE%\.local\bin\
```

> **Alternativa rapida** sin pipx: `pip install --user wa-crypt-tools`. Mas simple pero ensucia el Python del sistema.

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
        ┌───────────────┼─────────────────────────┐
        ▼               ▼                         ▼
    Android <= 13   Android 14          Android 15+ (incl. 16)
        │               │                         │
        ▼               ▼                         ▼
   metodo legacy   metodo legacy           metodo crypt15
   (auto lo elige)  (auto lo elige           (auto lo elige
                    si funciona,              siempre — legacy
                    si no cae a crypt15)      esta bloqueado)
                                              │
                                              ▼
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

**Tu primera ejecucion deberia ser siempre con auto-deteccion:**

```bash
python3 forense_android.py --wa-method auto
```

El script diagnostica el dispositivo, te dice que metodo va a usar, y te explica en el log que flags necesitas si quieres mejor resultado. **No hay que adivinar nada**.

> Si lanzas `python3 forense_android.py` SIN argumentos, te muestra el `--help` con todos los flags y ejemplos en vez de arrancar — proteccion para no desinstalar WhatsApp sin querer.

### Tabla completa de flags

| Flag | Por defecto | Descripcion |
|---|---|---|
| `--skip-wa` | desactivado | Omite la fase 5/8 entera (sin tocar WhatsApp). El resto del backup forense corre normal. |
| `--only-wa` | desactivado | Lo contrario: salta las fases pesadas (2/8 almacenamiento, 3/8 apps, 4/8 estado) y va directo a WhatsApp. Util para reintentos rapidos. Incompatible con `--skip-wa`. |
| `--wa-method {auto,legacy,crypt15}` | `auto` | Elige metodo de extraccion WhatsApp. Ver tabla abajo. |
| `--force-legacy` | desactivado | Permite arrancar `--wa-method legacy` aunque el diagnostico diga que NO es viable. Por defecto el script aborta para no desinstalar WhatsApp inutilmente. **Requiere** `--wa-method legacy` explicito — si lo pasas con `auto` / `crypt15` / `--skip-wa`, el script aborta con `[ERROR]` para evitar confusion. |
| `--wa-key HEX64` | — | Clave de 64 hex para descifrar `.crypt15`. Acepta `:`, `-`, espacios como separadores. Ej: `--wa-key TU_CLAVE_DE_64_HEX`. |
| `--wa-key-file PATH` | — | Alternativa a `--wa-key`: ruta al fichero `encrypted_backup.key` (binario). |
| `--device SERIAL` | autodetectado | Serial del dispositivo si hay >1 conectado. Sacalo de `adb devices`. Sin este flag, el script aborta cuando detecta varios moviles. |
| `--restore-wa DIR` | — | **Modo recuperacion**: reinstala WhatsApp desde una carpeta `apks_originales` de un run anterior que fallo a mitad. No hace el backup forense general. |

### Los 3 modos de `--wa-method`

| `--wa-method` | Que hace | Cuando usarlo | Requisitos |
|---|---|---|---|
| **`auto`** (default) | Diagnostica el dispositivo y elige `legacy` si es viable, si no `crypt15`. Si `legacy` falla cae a `crypt15`. | **Siempre** salvo que sepas exactamente que quieres. | Ninguno |
| **`legacy`** | Fuerza: desinstala WA -> instala WA viejo -> `adb backup` -> reinstala WA original. Te da `msgstore.db` plaintext directamente. | Android <= 14 o casos donde sabes que va a colar. | `java` en PATH |
| **`crypt15`** | Solo `adb pull` de `/sdcard/Android/media/com.whatsapp/WhatsApp/`. Salida cifrada salvo que pases `--wa-key`. NO modifica el WhatsApp del movil. | Android 15+ o cuando quieres CERO riesgo de tocar el WhatsApp del titular. | `pipx install wa-crypt-tools` (solo si `--wa-key`) |

### Ejemplos por escenario

**Mi movil es Android 14 o anterior (OPPO, Samsung, Pixel, etc.):**

```bash
# El default ya hace lo correcto:
python3 forense_android.py --wa-method auto
```
Te genera `msgstore.db` plaintext directamente via legacy. En Android 14 el script desinstala WhatsApp temporalmente para meter el APK viejo y lo restaura al final automaticamente — confirmado en OPPO Android 14.

**Mi movil es Android 15 o superior (incl. 16) y SI puedo pedir la clave al titular:**

```bash
# 1) El titular en SU movil:
#    WA -> Ajustes -> Chats -> Copia de seguridad -> Copia cifrada E2E
#    Si no la tiene activada: Activar -> "Mas opciones" (NO la "Llave de
#      acceso" biometrica) -> "Clave de cifrado de 64 digitos" -> Generar
#    Si ya la tiene activada: "Ver clave de 64 digitos" (PIN/huella)
#    Apuntar la clave; pulsar "Hacer copia ahora" para forzar un backup
#    fresco con esa clave.
#    Procedimiento detallado en la seccion "Como conseguir la clave" abajo.

# 2) En tu portatil con el movil enchufado:
pipx install wa-crypt-tools        # ver seccion Instalacion si te falla por PEP 668
python3 forense_android.py \
    --wa-method crypt15 \
    --wa-key TU_CLAVE_DE_64_HEX
```
Te genera `~/backup_movil/.../whatsapp/decrypted/msgstore.db` plaintext que luego pasas a `wa_viewer.py`.

**Mi movil es Android 15+ y NO tengo la clave (preservacion forense):**

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
python3 forense_android.py --only-wa --wa-method crypt15 --wa-key TU_CLAVE_DE_64_HEX
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

**Solo el titular del dispositivo puede sacarla**. No hay forma de extraerla sin root (la clave vive en `EndToEndEncryptionStoreFlatBuffer` dentro de `/data/data/com.whatsapp/`, inaccesible vía ADB normal). Hay **tres escenarios** segun como tenga el titular configurada la copia E2E:

#### Escenario A — El titular nunca ha activado la copia E2E

Es el caso por defecto en moviles nuevos o usuarios que no la han tocado.

1. Abre **WhatsApp**.
2. **Ajustes** (icono ⋮ o tres puntos, arriba a la derecha) → **Chats** → **Copia de seguridad**.
3. Pulsa **Copia de seguridad cifrada de extremo a extremo**.
4. Pulsa **Activar** (boton verde).
5. WhatsApp te ofrece primero **"Llave de acceso"** (passkey biometrica/huella) como metodo recomendado. **NO la elijas** — esa llave no sirve para descifrar `.crypt15` con `wa-crypt-tools`. En su lugar pulsa **"Mas opciones"** (es un enlace al final de la misma pantalla, en letra mas pequeña).
6. En la lista que aparece, elige **"Clave de cifrado de 64 digitos"** (la otra opcion suele ser "Contraseña" — esa tampoco la quieres).
7. Pulsa **Generar tu clave de 64 digitos**.
8. WhatsApp muestra la clave de 64 caracteres hex. **Screenshot + anotala en un sitio seguro** — si se pierde no es recuperable, ni Meta ni nadie puede restablecerla.
9. Pulsa **Continuar** → marca la casilla "He guardado mi clave en un lugar seguro" → **Crear**.
10. Vuelve a la pantalla **Copia de seguridad** y pulsa **Hacer copia ahora** (boton verde) para forzar un backup fresco cifrado con esa clave. *Importante*: el `.crypt15` que el script descargue debe ser uno generado **despues** de esta accion, los `.crypt15` antiguos usan la clave anterior (local, no la de 64 hex).

#### Escenario B — Ya tiene copia E2E activada, pero con contrasena (no clave)

WhatsApp protege la copia con una contrasena que el titular eligio. Para nuestros fines hay dos rutas:

- **Pedirle la contrasena tal cual** y pasarla al script con `wadecrypt --password "frase del titular" msgstore.db.crypt15 msgstore.db` (en lugar de `--wa-key`). Nota: `forense_android.py` con `--wa-key` no cubre el flow de password — para descifrar tras la extraccion usa `wadecrypt` a mano (`pipx install wa-crypt-tools`).
- **Convertirla a clave de 64 digitos**: Ajustes → Chats → Copia de seguridad → Copia E2E → **Cambiar contraseña** → elige **"Usar clave de cifrado de 64 digitos"** y sigue el Escenario A desde el paso 6.

#### Escenario C — Ya tiene copia E2E activada con clave de 64 digitos (caso comun en usuarios tecnicos)

El titular ya activo la copia con clave y solo necesita **verla** otra vez:

1. **WhatsApp** → **Ajustes** → **Chats** → **Copia de seguridad** → **Copia de seguridad cifrada de extremo a extremo**.
2. Pulsa **Ver clave de 64 digitos**.
3. Confirmar con **PIN o huella** del telefono.
4. WhatsApp muestra la clave actual. **No cambia ni se regenera**, es la misma de siempre.

#### Uso de la clave con el script

Una vez tienes la clave, pasala al script con `--wa-key <CLAVE>`. Acepta cualquier formato (con o sin separadores `:` / `-` / espacios, mayusculas o minusculas — el script normaliza):

```bash
# Todas estas formas son equivalentes:
--wa-key TU_CLAVE_DE_64_HEX
--wa-key 1234:5678:90ab:cdef:1234:5678:90ab:cdef:1234:5678:90ab:cdef:1234:5678:90ab:cdef
--wa-key "1234 5678 90AB CDEF 1234 5678 90AB CDEF 1234 5678 90AB CDEF 1234 5678 90AB CDEF"
```

Si prefieres pasarla por fichero (mas seguro que en el historial del shell), exporta `encrypted_backup.key` y usa `--wa-key-file ruta/al/encrypted_backup.key`.

Procedimiento oficial (puede cambiar con cada actualizacion mayor de WhatsApp): ver [Centro de ayuda — Activar copia cifrada E2E](https://faq.whatsapp.com/1246476872801203/?cms_platform=android&locale=es_LA).

### Que ficheros genera el script segun el metodo

**Caso A — Metodo `legacy` (Android <= 14):**

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
# Linux/macOS — especifica las rutas a las DBs ya extraidas
python3 wa_viewer.py \
    --msgstore ~/backup_movil/2026-05-11/whatsapp/extracted/apps/com.whatsapp/db/msgstore.db \
    --wadb     ~/backup_movil/2026-05-11/whatsapp/extracted/apps/com.whatsapp/db/wa.db \
    --output   chats_whatsapp.html

# Windows — mismo flujo, ajusta separadores de ruta
python wa_viewer.py ^
    --msgstore "%USERPROFILE%\backup_movil\2026-05-11\whatsapp\extracted\apps\com.whatsapp\db\msgstore.db" ^
    --wadb     "%USERPROFILE%\backup_movil\2026-05-11\whatsapp\extracted\apps\com.whatsapp\db\wa.db" ^
    --output   chats_whatsapp.html
```

> Si lanzas `python3 wa_viewer.py` SIN argumentos te muestra el `--help` con todos los flags y ejemplos.

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
| `--contacts` | — | Ruta a un `.vcf` con la libreta del telefono. Anade nombres de contactos privados a los chats. Prevalece sobre los nombres que WhatsApp guarda internamente. Ver explicacion abajo. |
| `--default-cc` | `34` | Codigo de pais por defecto para los telefonos del VCF que vienen en formato local sin prefijo `+` (ej. `654-117-918` -> `+34654117918`). Cambialo si tu agenda esta en otro pais. |

#### Por que `--contacts` (chats privados que salen con numero)

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
    --contacts ~/Descargas/contacts.vcf \
    --output   chats_whatsapp.html

# Windows
python wa_viewer.py ^
    --msgstore "%USERPROFILE%\backup_movil\2026-05-11\whatsapp\extracted\apps\com.whatsapp\db\msgstore.db" ^
    --wadb     "%USERPROFILE%\backup_movil\2026-05-11\whatsapp\extracted\apps\com.whatsapp\db\wa.db" ^
    --contacts "%USERPROFILE%\Downloads\contacts.vcf" ^
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

Tu movil es Android 15+ con WhatsApp moderno (`targetSdk >= 23`). **El metodo legacy no puede funcionar** en este escenario, es un bloqueo del propio Android que el flag `--bypass-low-target-sdk-block` no consigue sortear (confirmado en Realme C71 Android 15 y Realme GT7 Android 16). Usa `--wa-method crypt15` en su lugar. *Nota*: en Android 14 el script SI puede sortearlo gracias a `pm uninstall -k` previo, asi que este error en Android 14 indica un fallo distinto — revisa el log.

### `adb backup` produce un `.ab` vacio (solo cabecera, 0 datos)

Tu OEM (probablemente Huawei/EMUI) bloquea silenciosamente `adb backup`. **No hay forma programatica de sortearlo**. Cae al metodo `crypt15`.

### El script dice "WhatsApp esta en estado uninstalled-keep-data"

Un run anterior fallo entre el `pm uninstall -k` y el reinstall, dejando huerfanos los datos. **Tus chats siguen ahi**. Recupera con:

```bash
ls ~/backup_movil/*/whatsapp/apks_originales/  # localiza la carpeta del run anterior
python3 forense_android.py --restore-wa ~/backup_movil/YYYY-MM-DD_XX/whatsapp/apks_originales
```

### `wadecrypt: command not found`

Instala `wa-crypt-tools` con `pipx` (recomendado, funciona en Ubuntu 24.04+ y derivadas):

```bash
sudo apt install pipx -y          # o el equivalente del SO
pipx ensurepath
source ~/.bashrc                  # recarga PATH; o cierra y abre la terminal
pipx install wa-crypt-tools
which wadecrypt                   # debe imprimir /home/<usuario>/.local/bin/wadecrypt
```

Alternativas si `pipx` no esta disponible o no encaja:

```bash
# venv aislado
python3 -m venv .venv && source .venv/bin/activate && pip install wa-crypt-tools

# Windows (PEP 668 no aplica igual)
pip install --user wa-crypt-tools

# Distros antiguas sin PEP 668
pip install wa-crypt-tools
```

> Detalles completos por SO en la [seccion Instalacion](#instalacion).

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

Deltadri

[![YouTube](https://img.shields.io/badge/YouTube-@deltadri24-FF0000?logo=youtube&logoColor=white&style=for-the-badge)](https://www.youtube.com/@deltadri24)
[![X](https://img.shields.io/badge/X-@Deltadri-000000?logo=x&logoColor=white&style=for-the-badge)](https://x.com/Deltadri)
[![TikTok](https://img.shields.io/badge/TikTok-@deltaadri-000000?logo=tiktok&logoColor=white&style=for-the-badge)](https://www.tiktok.com/@deltaadri)
