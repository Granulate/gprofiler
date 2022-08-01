# Styling related hooks.
# Copyright (C) 2010-2022 Free Software Foundation, Inc.

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Utilities for styling."""

import gdb

try:
    from pygments import formatters, lexers, highlight

    def colorize(filename, contents):
        # Don't want any errors.
        try:
            lexer = lexers.get_lexer_for_filename(filename, stripnl=False)
            formatter = formatters.TerminalFormatter()
            return highlight(contents, lexer, formatter).encode(
                gdb.host_charset(), "backslashreplace"
            )
        except:
            return None

    def colorize_disasm(content, gdbarch):
        # Don't want any errors.
        try:
            lexer = lexers.get_lexer_by_name("asm")
            formatter = formatters.TerminalFormatter()
            return highlight(content, lexer, formatter).rstrip().encode()
        except:
            return None

except:

    def colorize(filename, contents):
        return None

    def colorize_disasm(content, gdbarch):
        return None
