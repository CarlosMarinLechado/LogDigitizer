# LogDigitizer

LogDigitizer is an open-source scientific desktop application for recovering
quantitative geophysical and petrophysical well-log curves from scanned legacy
raster images and exporting the reconstructed data in LAS 2.0 format.

It is designed for historical logs that may be distributed across several
scanned images, which are managed as segments of a single project.

The software was originally conceived and developed by **Carlos Marín Lechado**
at the **Centro Nacional Instituto Geológico y Minero de España
(CN IGME-CSIC)**.

## Main features

- Project-based management of scanned well logs and multiple image segments.
- Four-point image rectification and multipoint geometric correction.
- Image enhancement and preprocessing tools.
- Multipoint depth calibration.
- Independent horizontal calibration for each curve.
- Support for linear and logarithmic curve scales.
- Support for additional curve scales and complete-scale offsets.
- Semi-automatic minimum-cost curve tracking with guided corrections.
- Manual digitization with editable nodes and PCHIP interpolation.
- Assembly of digitized sections across overlapping image segments.
- Pre-export quality control for detecting gaps and missing intervals.
- Automatic interpolation of short gaps where appropriate.
- Direct navigation to intervals requiring review.
- LAS Curve Preview for visual inspection of assembled curves.
- Export of selected curves in LAS 2.0 format.
- Integrated access to the PDF User Guide.

## Workflow

The typical LogDigitizer workflow consists of six stages:

1. Project and image management
2. Preprocessing
3. Calibration
4. Digitization
5. Quality Control
6. LAS export

The workflow is iterative, allowing users to return to previous stages whenever
additional corrections or adjustments are required.

## Project status

LogDigitizer has been implemented, validated and packaged for Windows.

The source code is publicly available in this repository and the software is
distributed as free and open-source software under the European Union Public
Licence (EUPL), version 1.2 only.

The source version is defined in:

`src/logdigitizer/__init__.py`

The current release is **v1.1.0**.

## Windows installation

A packaged Windows installer is available from the GitHub Releases section of
this repository.

The installer provides a standalone version of LogDigitizer and does not
require users to install Python or the application dependencies separately.

## Requirements for running from source

- Python 3.11 or later.
- Python 3.12 is recommended for the current build.
- A graphical desktop environment supported by Qt.

Direct application dependencies are listed in:

`requirements-runtime.txt`

Testing and packaging tools are listed in:

`requirements-dev.txt`

## Installation from source on Windows

From PowerShell, in the project root directory:

```powershell
py -3.12 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
& ".\.venv\Scripts\python.exe" -m pip install --editable . --no-deps

## Citation

If you use LogDigitizer in scientific work, please cite the software version used:

> Marín Lechado, C. (2026). *LogDigitizer* (Version 1.1.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.23263539

Citation metadata are also available in [`CITATION.cff`](CITATION.cff).

## Licence

LogDigitizer is free and open-source software distributed under the
**European Union Public Licence (EUPL), version 1.2 only**.

See the [`LICENSE`](LICENSE) file for the full licence terms.

The LogDigitizer User Guide and associated documentation are distributed
under the **Creative Commons Attribution 4.0 International (CC BY 4.0)**
licence, unless otherwise stated.

Third-party libraries, components and other external materials remain subject
to their respective licences.

