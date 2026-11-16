# Third-party notices

This installation includes the components below. The Jjal Maker application source is provided under the MIT License in `LICENSE.txt`.

## FFmpeg and FFprobe

Jjal Maker uses the TAS-FFMPEG 8.1.2 Windows x64 build as separate processes. `ffmpeg.exe`, `ffprobe.exe`, and their `av*.dll` and `sw*.dll` libraries are distributed under the GNU General Public License version 2 or later. The application invokes the executables through the command line. FFmpeg and the linked third-party libraries retain their individual copyright and license notices.

The installed `licenses/FFmpeg` folder includes the original licenses, build manifest, dependency lock and configure command. The build uses upstream FFmpeg revision `38b88335f99e76ed89ff3c93f877fdefce736c13`. [Binary build and original corresponding source](https://github.com/NevermindNilas/TAS-FFMPEG/releases/tag/v8.1.2) · [FFmpeg source revision](https://github.com/FFmpeg/FFmpeg/commit/38b88335f99e76ed89ff3c93f877fdefce736c13)

Download `tas-ffmpeg-8.1.2-corresponding-source.tar.xz` from the same version of the [Jjal Maker release](https://github.com/yunhyok/jjal-maker/releases). The archive contains FFmpeg, the pinned dependency sources, and the configuration and scripts used to build these binaries.

## Qt, PySide6 Essentials and Shiboken6

The application uses Qt, PySide6 Essentials, and Shiboken6 version 6.8.3 under the GNU Lesser General Public License version 3. Their copyright holders include The Qt Company Ltd. and contributors. The library files are dynamically loaded and can be replaced with compatible builds under `_internal/PySide6` and `_internal/shiboken6` while the app is closed. You may modify these libraries and reverse engineer the combined work to debug such modifications under the LGPL terms. The Jjal Maker source and build instructions are available for building against replacement libraries.

The GPL and LGPL texts and Qt component notices are in the installed `licenses/Qt` folder. The matching Qt Base, Qt SVG, Qt Image Formats, and PySide/Shiboken source archives are distributed on the matching [Jjal Maker release](https://github.com/yunhyok/jjal-maker/releases). [Qt 6.8.3 sources](https://download.qt.io/archive/qt/6.8/6.8.3/submodules/) · [PySide/Shiboken 6.8.3 source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/) · [Qt LGPL terms](https://doc.qt.io/qt-6/lgpl.html)

## Python and Windows runtime

Python 3.12 is redistributed with its licenses in `licenses/Python.txt`. Python and Qt use the Microsoft Visual C++ runtime for Windows. [Python license](https://docs.python.org/3.12/license.html) · [Microsoft Visual C++ redistributables](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)

## Packaging tools

The app is packaged with PyInstaller 6.21.0 under its bootloader exception. The installer and uninstaller are made with Inno Setup 6.7.3 by Jordan Russell and Martijn Laan. The Inno Setup license is in `licenses/InnoSetup.txt`. [PyInstaller license and exception](https://pyinstaller.org/en/v6.21.0/license.html) · [Inno Setup release](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3)
