# Log Digitizer 0.1.0 — creación del instalador

## 1. Copiar la actualización

Copie el contenido del ZIP en la raíz del proyecto `digitalizacion_logs` y
acepte la sustitución de `src/logdigitizer/__init__.py`.

La estructura añadida será:

```text
digitalizacion_logs/
├── assets/LogDigitizer.ico
├── installer/
│   ├── LogDigitizer.iss
│   └── README_INSTALLER.md
└── src/logdigitizer/__init__.py
```

## 2. Comprobar y reconstruir la aplicación

Ejecute desde PowerShell, situado en la raíz del proyecto:

```powershell
& ".\.venv-packaging\Scripts\python.exe" -m pytest
& ".\.venv-packaging\Scripts\python.exe" -m PyInstaller --clean --noconfirm ".\LogDigitizer.spec"
```

La segunda orden debe terminar mostrando `Build complete!` y debe crear:

```text
dist\LogDigitizer\LogDigitizer.exe
```

## 3. Instalar Inno Setup

Instale la edición de 64 bits de Inno Setup 7 desde la página oficial, o
utilice Windows Package Manager:

```powershell
winget install --id JRSoftware.InnoSetup.7 -e -s winget -i
```

## 4. Compilar el instalador

Abra `installer\LogDigitizer.iss` con **Inno Setup Compiler** y seleccione
**Build → Compile**.

El resultado se guardará en:

```text
installer_output\LogDigitizer_Setup_0.1.0.exe
```

El instalador requiere Windows 10 versión 1809 o posterior de 64 bits. Instala
el programa en `C:\Program Files\Log Digitizer`, crea accesos en Inicio y en el
escritorio, incluye el User Guide y registra el desinstalador en Windows.
