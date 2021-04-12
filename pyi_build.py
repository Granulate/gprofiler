#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
# PyInstaller requires your application to conform to some minimal structure,
# namely that you have a CLI script to start your application.
# Often, this means creating a small script outside of your Python package
# that simply imports your package and runs main().
# Source: https://realpython.com/pyinstaller-python/#preparing-your-project

from gprofiler.main import main

main()
