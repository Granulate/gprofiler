@echo off
WHERE cargo
IF %ERRORLEVEL% NEQ 0 (
	ECHO cargo wasn't found. Attempting to install...
	.\dep\rustup-init.exe -y
)
ECHO "Current Path: %PATH%"
Rem SET PATH=%PATH%;%USERPROFILE%\.cargo\bin;%CD%\deps\winlibs-x86_64-posix-seh-gcc-12.1.0-llvm-14.0.4-mingw-w64ucrt-10.0.0-r2\mingw64\x86_64-w64-mingw32\bin;%CD%\deps\winlibs-x86_64-posix-seh-gcc-12.1.0-llvm-14.0.4-mingw-w64ucrt-10.0.0-r2\mingw64\bin
SET PATH=%PATH%;%USERPROFILE%\.cargo\bin;%CD%\deps
ECHO "Modified Path: %PATH%"
CD src\py-spy
rustup default stable
rustup target add x86_64-pc-windows-gnu

REM rustup toolchain install stable-x86_64-pc-windows-gnu
REM rustup default stable-x86_64-pc-windows-gnu
WHERE cargo
IF %ERRORLEVEL% NEQ 0 (
       ECHO py-spy build failed.
       EXIT /B -1
)
cargo install cross
REM cargo build --release --target=x86_64-pc-windows-gnu
cargo build --release
DIR target\release\py-spy.exe
IF %ERRORLEVEL% NEQ 0 (
	ECHO py-spy build failed.
	CD ..\..
	EXIT /B -1
)
ECHO py-spy install complete
CD ..\..
COPY src\py-spy\target\release\py-spy.exe .\py-spy
ECHO "Current Working Directory: %CD%"
ECHO Y | RMDIR /S src\py-spy\target
EXIT /B 0
