# Log Digitizer

Log Digitizer is a scientific desktop application for converting scanned
historical geophysical and petrophysical logs into calibrated digital curves.
It is designed for logs that may be distributed across several scanned images,
which the application manages as segments of a single project.

The software was originally conceived and developed by **Carlos Marín
Lechado** at the **Centro Nacional Instituto Geológico y Minero de España
(CN IGME-CSIC)**.

## Main features

- Project-based management of scanned logs and image segments.
- Four-point image rectification and tonal enhancement.
- Multipoint depth calibration.
- Independent horizontal calibration for each curve.
- Support for additional curve scales and complete-scale offsets.
- Semi-automatic minimum-cost curve tracking with guided corrections.
- Manual digitization, node editing, undo and redo.
- Assembly and review of digitized sections across overlapping segments.
- Export of selected curves in LAS 2.0 format.
- Integrated access to the PDF User Guide.

## Project status

LogDigitizer has been implemented and packaged for Windows.
The repository is currently private while licensing and public release
arrangements are being finalised.

The source version is defined in `src/logdigitizer/__init__.py`.

The current source version is `1.0.1`.

## Requirements

- Python 3.11 or later. Python 3.12 is recommended for the current build.
- A graphical desktop environment supported by Qt.

Direct application dependencies are listed in
`requirements-runtime.txt`. Testing and packaging tools are listed in
`requirements-dev.txt`.

## Installation from source on Windows

From PowerShell, in the project root directory:

```powershell
py -3.12 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
& ".\.venv\Scripts\python.exe" -m pip install --editable . --no-deps
```

These commands call the virtual-environment interpreter directly, so activating
PowerShell scripts is not required.

## Running the application

```powershell
& ".\.venv\Scripts\python.exe" -m logdigitizer.app
```

After the editable installation, the console entry point can also be used:

```powershell
& ".\.venv\Scripts\logdigitizer.exe"
```

## Running the tests

```powershell
& ".\.venv\Scripts\python.exe" -m pytest
```

The pytest configuration is stored in `pyproject.toml`; a separate
`pytest.ini` file is not required.

## Documentation

The current User Guide is located at:

```text
docs/LogDigitizer_User_Guide.pdf
```

It can also be opened from **Help → User Guide** within the application.

## Repository structure

```text
.
├── docs/                         User Guide
├── src/logdigitizer/             Application source code and resources
├── tests/                        Automated test suite
├── pyproject.toml                Project and pytest configuration
├── requirements-runtime.txt     Application dependencies
└── requirements-dev.txt         Test and packaging dependencies
```

## Licence

The software licence is currently under review and has not yet been
finalised. This repository is being maintained privately pending that
decision.

Licensing terms for the software and documentation will be confirmed
before any public release. Third-party components remain subject to
their respective licences.

## Citation

A scientific article describing Log Digitizer is planned. Once it is
published, it will become the preferred reference for scientific citation.
Until then, cite the specific software version used. The definitive citation
metadata will be maintained in `CITATION.cff`.

Suggested provisional format:

> Marín-Lechado, C. (year). *Log Digitizer* (Version X.Y.Z) [Computer
> software]. Instituto Geológico y Minero de España (IGME-CSIC). Software
> version DOI.

## Author and credits

**Original author and sole developer**

Carlos Marín Lechado  
Centro Nacional Instituto Geológico y Minero de España (CN IGME-CSIC)  
Agencia Estatal Consejo Superior de Investigaciones Científicas, M.P. (CSIC)

Future contributors will be identified in `AUTHORS.md` and in the repository
contribution history.
