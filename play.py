#!/usr/bin/env python3
# -*- mode:python; coding:utf-8 -*-

__version__ = "play 1.02"

"""


mcplay - A curses front-end for various audio players based on cplay

Copyright (C) 1998-2005 Ulf Betlehem <flu@iki.fi>
Copyright (C) 2008-2011 Adrian C. <anrxc@sysphere.org>
Copyright (C) 2022-???? Michael T. <dev@null>

CHANGELOG:
Edited for Python 3.7

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or (at
your option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
"""

# ------------------------------------------
from types import *

import re
import os
import sys
import time
import getopt
import signal
import string
import select
import subprocess

try: from ncurses import curses
except ImportError: import curses

try: import tty
except ImportError: tty = None

try: import locale; locale.setlocale(locale.LC_ALL, "")
except: pass

# ------------------------------------------
XTERM = re.search("rxvt|xterm", os.environ["TERM"])
CONTROL_FIFO = "%s/play-control-%s" % (
    os.environ.get("TMPDIR", "/tmp"), os.environ["USER"])

# ------------------------------------------
def which(program):
    for path in str.split(os.environ["PATH"], ":"):
        if os.path.exists(os.path.join(path, program)):
            return os.path.join(path, program)

# ------------------------------------------
def cut(s, n, left=0):
    if left: return len(s) > n and "<%s" % s[-n+1:] or s
    else: return len(s) > n and "%s>" % s[:n-1] or s

# ------------------------------------------
class Stack:
    def __init__(self):
        self.items = ()

    def push(self, item):
        self.items = (item,) + self.items

    def pop(self):
        self.items, item = self.items[1:], self.items[0]
        return item

# ------------------------------------------
class KeymapStack(Stack):
    def process(self, code):
        for keymap in self.items:
            if keymap and keymap.process(code):
                break

# ------------------------------------------
class Keymap:
    def __init__(self):
        self.methods = [None] * curses.KEY_MAX

    def bind(self, key, method, args=None):
        if type(key) is str:
            ki = ord(key)
            self.methods[ki] = (method, args)
        if type(key) in (tuple, list):
            for ki in key:
                if type(ki) is str:
                    ki = ord(ki)
                self.methods[ki] = (method, args)
        if type(key) is range:
            for k in key:
                self.methods[k] = (method, args)

    def process(self, key):
        try:
            if self.methods[key] is None: return 0
        except IndexError:
            return 0
        method, args = self.methods[key]
        if args is None: args = (key,)
        method(*args)
        return 1

# ------------------------------------------
class Window:
    chars = string.ascii_letters+string.digits+string.punctuation+string.whitespace

    def __init__(self, parent):
        self.parent = parent
        self.children = []
        self.name = None
        self.keymap = None
        self.visible = 1
        self.resize()
        if parent: parent.children.append(self)

    def insstr(self, s):
        if not s: return
        try:
            self.w.addstr(s[:-1])
            self.w.hline(ord(s[-1]), 1)  # insch() work-around
        except:
            pass

    def __getattr__(self, name):
        return getattr(self.w, name)

    def getmaxyx(self):
        y, x = self.w.getmaxyx()
        try: curses.version  # tested with 1.2 and 1.6
        except AttributeError:
            # pyncurses - emulate traditional (silly) behavior
            y, x = y+1, x+1
        return y, x

    def touchwin(self):
        try: self.w.touchwin()
        except AttributeError: self.touchln(0, self.getmaxyx()[0])

    def attron(self, attr):
        try: self.w.attron(attr)
        except AttributeError: self.w.attr_on(attr)

    def attroff(self, attr):
        try: self.w.attroff(attr)
        except AttributeError: self.w.attr_off(attr)

    def newwin(self):
        return curses.newwin(curses.tigetnum('lines'), curses.tigetnum('cols'), 0, 0)

    def resize(self):
        self.w = self.newwin()
        self.ypos, self.xpos = self.getbegyx()
        self.rows, self.cols = self.getmaxyx()
        self.keypad(1)
        self.leaveok(0)
        self.scrollok(0)
        for child in self.children:
            child.resize()

    def update(self):
        self.clear()
        self.refresh()
        for child in self.children:
            child.update()

# ------------------------------------------
class ProgressWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.value = 0

    def newwin(self):
        return curses.newwin(1, self.parent.cols, self.parent.rows-2, 0)

    def update(self):
        self.move(0, 0)
        self.hline(ord('-'), self.cols)
        if self.value > 0:
            self.move(0, 0)
            x = int(self.value * self.cols)  # 0 to cols-1
            x and self.hline(ord('='), x)
            self.move(0, x)
            # Color of the progress indicator
            self.attron(curses.color_pair(1))
            self.insstr('O')
            self.attroff(curses.color_pair(1))
        self.touchwin()
        self.refresh()

    def progress(self, value):
        self.value = min(value, 0.99)
        self.update()

# ------------------------------------------
class StatusWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.default_message = ''
        self.current_message = ''
        self.tid = None

    def newwin(self):
        return curses.newwin(1, self.parent.cols-12, self.parent.rows-1, 0)

    def update(self):
        msg = self.current_message
        self.move(0, 0)
        self.clrtoeol()
        # Color of statusbar messages
        self.attron(curses.color_pair(3))
        self.insstr(cut(msg, self.cols))
        self.attroff(curses.color_pair(3))
        self.touchwin()
        self.refresh()

    def status(self, message, duration = 0):
        self.current_message = str(message)
        if self.tid: app.timeout.remove(self.tid)
        if duration: self.tid = app.timeout.add(duration, self.timeout)
        else: self.tid = None
        self.update()

    def timeout(self):
        self.tid = None
        self.restore_default_status()

    def set_default_status(self, message):
        if self.current_message == self.default_message: self.status(message)
        self.default_message = message
        XTERM and sys.stderr.write("\033]0;%s\a" % (message or "play"))

    def restore_default_status(self):
        self.status(self.default_message)

# ------------------------------------------
class CounterWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.values = [0, 0]
        self.mode = 1

    def newwin(self):
        return curses.newwin(1, 11, self.parent.rows-1, self.parent.cols-11)

    def update(self):
        h, s = divmod(self.values[self.mode], 3600)
        m, s = divmod(s, 60)
        self.move(0, 0)
        # Color of the statusbar counter
        self.attron(curses.color_pair(1))
        self.insstr("[%02d:%02d:%02d]" % (h, m, s))
        self.attroff(curses.color_pair(1))
        self.touchwin()
        self.refresh()

    def counter(self, values):
        self.values = values
        self.update()

    def toggle_mode(self):
        self.mode = not self.mode
        tmp = ["elapsed", "remaining"][self.mode]
        app.status("Counting %s time" % tmp, 1)
        self.update()

# ------------------------------------------
class RootWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        keymap = Keymap()
        app.keymapstack.push(keymap)
        self.win_progress = ProgressWindow(self)
        self.win_status = StatusWindow(self)
        self.win_counter = CounterWindow(self)
        self.win_tab = TabWindow(self)
        keymap.bind(12, self.update, ()) # C-l
        keymap.bind([curses.KEY_LEFT, 2], app.seek, (-1, 1)) # C-b
        keymap.bind([curses.KEY_RIGHT, 6], app.seek, (1, 1)) # C-f
        keymap.bind([1, '^'], app.seek, (0, 0)) # C-a
        keymap.bind([5, '$'], app.seek, (-1, 0)) # C-e
        keymap.bind(range(48,58), app.key_volume) # 0123456789
        keymap.bind(['+', '='], app.mixer, ("cue", 1))
        keymap.bind('-', app.mixer, ("cue", -1))
        keymap.bind('n', app.next_song, ())
        keymap.bind('p', app.prev_song, ())
        keymap.bind('z', app.toggle_pause, ())
        keymap.bind('x', app.toggle_stop, ())
        keymap.bind('c', self.win_counter.toggle_mode, ())
        keymap.bind('Q', app.quit, ())
        keymap.bind('q', self.command_quit, ())
        keymap.bind('v', app.mixer, ("toggle",))
        keymap.bind(',', app.command_macro, ())

    def command_quit(self):
        app.do_input_hook = self.do_quit
        app.start_input("Quit? (y/N)")

    def do_quit(self, ch):
        if chr(ch) == 'y': app.quit()
        app.stop_input()

# ------------------------------------------
class TabWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.active_child = 0

        self.win_filelist = self.add(FilelistWindow)
        self.win_playlist = self.add(PlaylistWindow)
        self.win_help     = self.add(HelpWindow)

        keymap = Keymap()
        keymap.bind('\t', self.change_window, ()) # tab
        keymap.bind('h', self.help, ())
        app.keymapstack.push(keymap)
        app.keymapstack.push(self.children[self.active_child].keymap)

    def newwin(self):
        return curses.newwin(self.parent.rows-2, self.parent.cols, 0, 0)

    def update(self):
        self.update_title()
        self.move(1, 0)
        self.hline(ord('-'), self.cols)
        self.move(2, 0)
        self.clrtobot()
        self.refresh()
        child = self.children[self.active_child]
        child.visible = 1
        child.update()

    def update_title(self, refresh = 1):
        child = self.children[self.active_child]
        self.move(0, 0)
        self.clrtoeol()
        # Color of the window titlebar text
        self.attron(curses.color_pair(2))
        self.insstr(child.get_title())
        self.attroff(curses.color_pair(2))
        if refresh: self.refresh()

    def add(self, Class):
        win = Class(self)
        win.visible = 0
        return win

    def change_window(self, window = None):
        app.keymapstack.pop()
        self.children[self.active_child].visible = 0
        if window:
            self.active_child = self.children.index(window)
        else:
            # toggle windows 0 and 1
            self.active_child = not self.active_child
        app.keymapstack.push(self.children[self.active_child].keymap)
        self.update()

    def help(self):
        if self.children[self.active_child] == self.win_help:
            self.change_window(self.win_last)
        else:
            self.win_last = self.children[self.active_child]
            self.change_window(self.win_help)
            app.status(__version__, 2)

# ------------------------------------------
class ListWindow(Window):
    def __init__(self, parent):
        Window.__init__(self, parent)
        self.buffer = []
        self.bufptr = self.scrptr = 0
        self.search_direction = 0
        self.last_search = ""
        self.hoffset = 0
        self.keymap = Keymap()
        self.keymap.bind(['k', curses.KEY_UP, 16], self.cursor_move, (-1,))
        self.keymap.bind(['j', curses.KEY_DOWN, 14], self.cursor_move, (1,))
        self.keymap.bind(['K', curses.KEY_PPAGE], self.cursor_ppage, ())
        self.keymap.bind(['J', curses.KEY_NPAGE], self.cursor_npage, ())
        self.keymap.bind(['g', curses.KEY_HOME], self.cursor_home, ())
        self.keymap.bind(['G', curses.KEY_END], self.cursor_end, ())
        self.keymap.bind(['?', 18], self.start_search,
                         ("backward-isearch", -1))
        self.keymap.bind(['/', 19], self.start_search,
                         ("forward-isearch", 1))
        self.keymap.bind(['>'], self.hscroll, (8,))
        self.keymap.bind(['<'], self.hscroll, (-8,))

    def newwin(self):
        return curses.newwin(self.parent.rows-2, self.parent.cols,
                             self.parent.ypos+2, self.parent.xpos)

    def update(self, force = 1):
        self.bufptr = max(0, min(self.bufptr, len(self.buffer) - 1))
        first, last = self.scrptr, self.scrptr + self.rows - 1
        if (self.bufptr < first): first = self.bufptr
        if (self.bufptr > last): first = self.bufptr - self.rows + 1
        if force or self.scrptr != first:
            self.scrptr = first
            self.move(0, 0)
            self.clrtobot()
            i = 0
            for entry in self.buffer[first:first+self.rows]:
                self.move(i, 0)
                i = i + 1
                self.putstr(entry)
            if self.visible:
                self.refresh()
                self.parent.update_title()
        # Color of a selected list item
        self.update_line(curses.color_pair(4))

    def update_line(self, attr = None, refresh = 1):
        if not self.buffer: return
        ypos = self.bufptr - self.scrptr
        if attr: self.attron(attr)
        self.move(ypos, 0)
        self.hline(ord(' '), self.cols)
        self.putstr(self.current())
        if attr: self.attroff(attr)
        if self.visible and refresh: self.refresh()

    def get_title(self, data=""):
        pos = "%s-%s/%s" % (self.scrptr+min(1, len(self.buffer)),
                            min(self.scrptr+self.rows, len(self.buffer)),
                            len(self.buffer))
        width = self.cols-len(pos)-2
        data = cut(data, width-len(self.name), 1)
        return "%-*s  %s" % (width, cut(self.name+data, width), pos)

    def putstr(self, entry, *pos):
        s = str(entry)
        pos and self.move(*pos)
        if self.hoffset: s = "<%s" % s[self.hoffset+1:]
        self.insstr(cut(s, self.cols))

    def current(self):
        if len(self.buffer) == 0: return None
        if self.bufptr >= len(self.buffer): self.bufptr = len(self.buffer) - 1
        return self.buffer[self.bufptr]

    def cursor_move(self, ydiff):
        if app.input_mode: app.cancel_input()
        if not self.buffer: return
        self.update_line(refresh = 0)
        self.bufptr = (self.bufptr + ydiff) % len(self.buffer)
        self.update(force = 0)

    def cursor_ppage(self):
        self.bufptr = self.scrptr - 1
        if self.bufptr < 0: self.bufptr = len(self.buffer) - 1
        self.scrptr = max(0, self.bufptr - self.rows)
        self.update()

    def cursor_npage(self):
        self.bufptr = self.scrptr + self.rows
        if self.bufptr > len(self.buffer) - 1: self.bufptr = 0
        self.scrptr = self.bufptr
        self.update()

    def cursor_home(self): self.cursor_move(-self.bufptr)

    def cursor_end(self): self.cursor_move(-self.bufptr - 1)

    def start_search(self, type, direction):
        self.search_direction = direction
        self.not_found = 0
        if app.input_mode:
            app.input_prompt = "%s: " % type
            self.do_search(advance = direction)
        else:
            app.do_input_hook = self.do_search
            app.stop_input_hook = self.stop_search
            app.start_input(type)

    def stop_search(self):
        self.last_search = app.input_string
        app.status("ok", 1)

    def do_search(self, ch = None, advance = 0):
        if ch in [8, 127]: app.input_string = app.input_string[:-1]
        elif ch: app.input_string = "%s%c" % (app.input_string, ch)
        else: app.input_string = app.input_string or self.last_search
        index = self.bufptr + advance
        while 1:
            if not 0 <= index < len(self.buffer):
                app.status("Not found: %s " % app.input_string)
                self.not_found = 1
                break
            line = str(self.buffer[index]).lower()
            if line.find(str(app.input_string).lower()) != -1:
                app.show_input()
                self.update_line(refresh = 0)
                self.bufptr = index
                self.update(force = 0)
                self.not_found = 0
                break
            if self.not_found:
                app.status("Not found: %s " % app.input_string)
                break
            index = index + self.search_direction

    def hscroll(self, value):
        self.hoffset = max(0, self.hoffset + value)
        self.update()

# ------------------------------------------
class HelpWindow(ListWindow):
    def __init__(self, parent):
        ListWindow.__init__(self, parent)
        self.name = "Help"
        self.keymap.bind('q', self.parent.help, ())
        self.buffer = str.split("""\
 * Global                              * Filelist
 Up, Down, k, j, C-p, C-n, PgUp, PgDn, a     : add (tagged) to playlist
 K, J, Home, End : movement            s     : recursive search
 g, G            : first/last item     BS, o : goto parent/specified dir
 Enter           : chdir or play       m, '  : set/get bookmark
 Tab             : filelist/playlist
 n, p            : next/prev track
 z, x            : toggle pause/stop

 * Playback                            * Playlist
 Left, Right,                          d, D  : delete (tagged) tracks/playlist
 C-f, C-b    : seek forward/backward   m, M  : move tagged tracks after/before
 C-a, C-e    : restart/end track       r, R  : toggle repeat/Random mode
 C-s, C-r, / : isearch                 s, S  : shuffle/Sort playlist
 C-g, Esc    : cancel                  w, @  : write playlist, jump to active
 1..9, +, -  : volume control          X     : stop playlist after each track
 c, v        : counter/volume mode     t, T  : tag current/regex
 <, >        : horizontal scrolling    u, U  : untag current/regex
 C-l, l      : refresh, list mode      Sp, i : invert current/all
 h, q, Q     : help, quit?, Quit!      !, ,  : shell, macro
""", "\n")

# ------------------------------------------
class ListEntry:
    def __init__(self, pathname, dir=0):
        self.filename = os.path.basename(pathname)
        self.pathname = pathname
        self.slash = dir and "/" or ""
        self.tagged = 0

    def set_tagged(self, value):
        self.tagged = value

    def is_tagged(self):
        return self.tagged == 1

    def __str__(self):
        mark = self.is_tagged() and "*" or " "
        return "%s %s%s" % (mark, self.vp(), self.slash)

    def vp(self):
        return self.vps[0][1](self)

    def vp_filename(self):
        return self.filename or self.pathname

    def vp_pathname(self):
        return self.pathname

    vps = [["filename", vp_filename],
           ["pathname", vp_pathname]]

# ------------------------------------------
class PlaylistEntry(ListEntry):
    def __init__(self, pathname):
        ListEntry.__init__(self, pathname)
        self.metadata = None
        self.active = 0

    def set_active(self, value):
        self.active = value

    def is_active(self):
        return self.active == 1

    def vp_metadata(self):
        return self.metadata or self.read_metadata()

    def read_metadata(self):
        self.metadata = get_tag(self.pathname)
        return self.metadata

    vps = ListEntry.vps[:] + [["metadata", vp_metadata]]

# ------------------------------------------
class TagListWindow(ListWindow):
    def __init__(self, parent):
        ListWindow.__init__(self, parent)
        self.keymap.bind(' ', self.command_tag_untag, ())
        self.keymap.bind('i', self.command_invert_tags, ())
        self.keymap.bind('t', self.command_tag, (1,))
        self.keymap.bind('u', self.command_tag, (0,))
        self.keymap.bind('T', self.command_tag_regexp, (1,))
        self.keymap.bind('U', self.command_tag_regexp, (0,))
        self.keymap.bind('l', self.command_change_viewpoint, ())
        self.keymap.bind('!', self.command_shell, ())

    def command_shell(self):
        if app.restricted: return
        app.stop_input_hook = self.stop_shell
        app.complete_input_hook = self.complete_shell
        app.start_input("shell$ ", colon=0)

    def stop_shell(self):
        s = app.input_string
        curses.endwin()
        sys.stderr.write("\n")
        argv = map(lambda x: x.pathname, self.get_tagged())
        argv or self.current() and argv.append(self.current().pathname)
        r = subprocess.call(" ".join([s] + argv), shell=True)
        sys.stderr.write("\nshell returned %s, press return!\n" % r)
        sys.stdin.readline()
        app.win_root.update()
        app.restore_default_status()
        app.cursor(0)

    def complete_shell(self, line):
        return self.complete_generic(line, quote=1)

    def complete_generic(self, line, quote=0):
        import glob
        if quote:
            s = re.sub('.*[^\\\\][ \'"()\[\]{}$`]', '', line)
            s, part = re.sub('\\\\', '', s), line[:len(line)-len(s)]
        else:
            s, part = line, ""
        results = glob.glob(os.path.expanduser(s)+"*")
        if len(results) == 0:
            return line
        if len(results) == 1:
            lm = results[0]
            lm = lm + (os.path.isdir(lm) and "/" or "")
        else:
            lm = results[0]
            for result in results:
                for i in range(min(len(result), len(lm))):
                   if result[i] != lm[i]:
                        lm = lm[:i]
                        break
        if quote: lm = re.sub('([ \'"()\[\]{}$`])', '\\\\\\1', lm)
        return part + lm

    def command_change_viewpoint(self, klass=ListEntry):
        klass.vps.append(klass.vps.pop(0))
        app.status("Listing %s" % klass.vps[0][0], 1)
        app.player.update_status()
        self.update()

    def command_invert_tags(self):
        for i in self.buffer:
            i.set_tagged(not i.is_tagged())
        self.update()

    def command_tag_untag(self):
        if not self.buffer: return
        tmp = self.buffer[self.bufptr]
        tmp.set_tagged(not tmp.is_tagged())
        self.cursor_move(1)

    def command_tag(self, value):
        if not self.buffer: return
        self.buffer[self.bufptr].set_tagged(value)
        self.cursor_move(1)

    def command_tag_regexp(self, value):
        self.tag_value = value
        app.stop_input_hook = self.stop_tag_regexp
        app.start_input(value and "Tag regexp" or "Untag regexp")

    def stop_tag_regexp(self):
        try:
            r = re.compile(app.input_string, re.I)
            for entry in self.buffer:
                if r.search(str(entry)):
                    entry.set_tagged(self.tag_value)
            self.update()
            app.status("ok", 1)
        except Exception as e:
            app.status(e, 2)

    def get_tagged(self):
        return (list(filter(lambda x: x.is_tagged(), self.buffer)))

    def not_tagged(self, l):
        return (list(filter(lambda x: not x.is_tagged(), l)))

# ------------------------------------------
class FilelistWindow(TagListWindow):
    def __init__(self, parent):
        TagListWindow.__init__(self, parent)
        self.oldposition = {}
        try: self.chdir(os.getcwd())
        except OSError: self.chdir(os.environ['HOME'])
        self.startdir = self.cwd
        self.mtime_when = 0
        self.mtime = None
        self.keymap.bind(['\n', curses.KEY_ENTER],
                         self.command_chdir_or_play, ())
        self.keymap.bind(['.', 127, curses.KEY_BACKSPACE],
                         self.command_chparentdir, ())
        self.keymap.bind('a', self.command_add_recursively, ())
        self.keymap.bind('o', self.command_goto, ())
        self.keymap.bind('s', self.command_search_recursively, ())
        self.keymap.bind('m', self.command_set_bookmark, ())
        self.keymap.bind("'", self.command_get_bookmark, ())
        self.bookmarks = { 39: [self.cwd, 0] }

    def command_get_bookmark(self):
        app.do_input_hook = self.do_get_bookmark
        app.start_input("bookmark")

    def do_get_bookmark(self, ch):
        app.input_string = ch
        bookmark = self.bookmarks.get(ch)
        if bookmark:
            self.bookmarks[39] = [self.cwd, self.bufptr]
            dir, pos = bookmark
            self.chdir(dir)
            self.listdir()
            self.bufptr = pos
            self.update()
            app.status("ok", 1)
        else:
            app.status("Not found!", 1)
        app.stop_input()

    def command_set_bookmark(self):
        app.do_input_hook = self.do_set_bookmark
        app.start_input("set bookmark")

    def do_set_bookmark(self, ch):
        app.input_string = ch
        self.bookmarks[ch] = [self.cwd, self.bufptr]
        ch and app.status("ok", 1) or app.stop_input()

    def command_search_recursively(self):
        app.stop_input_hook = self.stop_search_recursively
        app.start_input("search")

    def stop_search_recursively(self):
        try: re_tmp = re.compile(app.input_string, re.I)
        except Exception as e:
            app.status(e, 2)
            return
        app.status("Searching...")
        results = []
        for entry in self.buffer:
            if entry.filename == "..":
                continue
            if re_tmp.search(entry.filename):
                results.append(entry)
            elif os.path.isdir(entry.pathname):
                try: self.search_recursively(re_tmp, entry.pathname, results)
                except: pass
        if not self.search_mode:
            self.chdir(os.path.join(self.cwd, "search results"))
            self.search_mode = 1
        self.buffer = results
        self.bufptr = 0
        self.parent.update_title()
        self.update()
        app.restore_default_status()

    def search_recursively(self, re_tmp, dir, results):
        for filename in os.listdir(dir):
            pathname = os.path.join(dir, filename)
            if re_tmp.search(filename):
                if os.path.isdir(pathname):
                    results.append(ListEntry(pathname, 1))
                elif VALID_PLAYLIST(filename) or VALID_SONG(filename):
                    results.append(ListEntry(pathname))
            elif os.path.isdir(pathname):
                self.search_recursively(re_tmp, pathname, results)

    def get_title(self):
        self.name = "Filelist: "
        return ListWindow.get_title(self, re.sub("/?$", "/", self.cwd))

    def listdir_maybe(self, now=0):
        if now < self.mtime_when+2: return
        self.mtime_when = now
        self.oldposition[self.cwd] = self.bufptr
        try: self.mtime == os.stat(self.cwd)[8] or self.listdir(quiet=1)
        except os.error: pass

    def listdir(self, quiet=0, prevdir=None):
        quiet or app.status("Reading directory...")
        self.search_mode = 0
        dirs = []
        files = []
        try:
            self.mtime = os.stat(self.cwd)[8]
            self.mtime_when = time.time()
            filenames = os.listdir(self.cwd)
            filenames.sort()
            for filename in filenames:
                if filename[0] == ".": continue
                pathname = os.path.join(self.cwd, filename)
                if os.path.isdir(pathname): dirs.append(pathname)
                elif VALID_SONG(filename): files.append(pathname)
                elif VALID_PLAYLIST(filename): files.append(pathname)
        except os.error: pass
        dots = ListEntry(os.path.join(self.cwd, ".."), 1)
        self.buffer = [[dots], []][self.cwd == "/"]
        for i in dirs: self.buffer.append(ListEntry(i, 1))
        for i in files: self.buffer.append(ListEntry(i))
        if prevdir:
            for self.bufptr in range(len(self.buffer)):
                if self.buffer[self.bufptr].filename == prevdir: break
            else: self.bufptr = 0
        elif self.cwd in self.oldposition:
            self.bufptr = self.oldposition[self.cwd]
        else: self.bufptr = 0
        self.parent.update_title()
        self.update()
        quiet or app.restore_default_status()

    def chdir(self, dir):
        if hasattr(self, "cwd"): self.oldposition[self.cwd] = self.bufptr
        self.cwd = os.path.normpath(dir)
        try: os.chdir(self.cwd)
        except: pass

    def command_chdir_or_play(self):
        if not self.buffer: return
        if self.current().filename == "..":
            self.command_chparentdir()
        elif os.path.isdir(self.current().pathname):
            self.chdir(self.current().pathname)
            self.listdir()
        elif VALID_SONG(self.current().filename):
            app.play(self.current())

    def command_chparentdir(self):
        if app.restricted and self.cwd == self.startdir: return
        dir = os.path.basename(self.cwd)
        self.chdir(os.path.dirname(self.cwd))
        self.listdir(prevdir=dir)

    def command_goto(self):
        if app.restricted: return
        app.stop_input_hook = self.stop_goto
        app.complete_input_hook = self.complete_generic
        app.start_input("goto")

    def stop_goto(self):
        dir = os.path.expanduser(app.input_string)
        if dir[0] != '/': dir = os.path.join(self.cwd, dir)
        if not os.path.isdir(dir):
            app.status("Not a directory!", 1)
            return
        self.chdir(dir)
        self.listdir()

    def command_add_recursively(self):
        l = self.get_tagged()
        if not l:
            app.win_playlist.add(self.current().pathname)
            self.cursor_move(1)
            return
        app.status("Adding tagged files", 1)
        for entry in l:
            app.win_playlist.add(entry.pathname, quiet=1)
            entry.set_tagged(0)
        self.update()

# ------------------------------------------
class PlaylistWindow(TagListWindow):
    def __init__(self, parent):
        TagListWindow.__init__(self, parent)
        self.pathname = None
        self.repeat = 0
        self.random = 0
        self.random_prev = []
        self.random_next = []
        self.random_left = []
        self.stop = 0
        self.keymap.bind(['\n', curses.KEY_ENTER],
                         self.command_play, ())
        self.keymap.bind('d', self.command_delete, ())
        self.keymap.bind('D', self.command_delete_all, ())
        self.keymap.bind('m', self.command_move, (1,))
        self.keymap.bind('M', self.command_move, (0,))
        self.keymap.bind('s', self.command_shuffle, ())
        self.keymap.bind('S', self.command_sort, ())
        self.keymap.bind('r', self.command_toggle_repeat, ())
        self.keymap.bind('R', self.command_toggle_random, ())
        self.keymap.bind('X', self.command_toggle_stop, ())
        self.keymap.bind('w', self.command_save_playlist, ())
        self.keymap.bind('@', self.command_jump_to_active, ())

    def command_change_viewpoint(self, klass=PlaylistEntry):
       TagListWindow.command_change_viewpoint(self, klass)

    def get_title(self):
        space_out = lambda value, s: value and s or " "*len(s)
        self.name = "Playlist %s %s %s" % (
            space_out(self.repeat, "[repeat]"),
            space_out(self.random, "[random]"),
            space_out(self.stop, "[stop]"))
        return ListWindow.get_title(self)

    def append(self, item):
        self.buffer.append(item)
        if self.random: self.random_left.append(item)

    def add_dir(self, dir):
        try:
            filenames = os.listdir(dir)
            filenames.sort()
            subdirs = []
            for filename in filenames:
                pathname = os.path.join(dir, filename)
                if VALID_SONG(filename):
                    self.append(PlaylistEntry(pathname))
                elif VALID_PLAYLIST(filename):
                    self.add_playlist(pathname)
                if os.path.isdir(pathname):
                    subdirs.append(pathname)
            map(self.add_dir, subdirs)
        except Exception as e:
            app.status(e, 2)

    def add_m3u(self, line):
        try:
            if re.match("^(#.*)?$", line): return
            if re.match("^(/|http://)", line):
                self.append(PlaylistEntry(self.fix_url(line)))
            else:
                dirname = os.path.dirname(self.pathname)
                self.append(PlaylistEntry(os.path.join(dirname, line)))
        except Exception as e:
            app.status(e, 2)

    def add_pls(self, line):
        # todo - support title & length
        m = re.match("File(\d+)=(.*)", line)
        if m: self.append(PlaylistEntry(self.fix_url(m.group(2))))

    def add_playlist(self, pathname):
        try:
            self.pathname = pathname
            if re.search("\.m3u$", pathname, re.I): f = self.add_m3u
            if re.search("\.pls$", pathname, re.I): f = self.add_pls
            file = open(pathname)
            #map(f, map(str.strip, file.readlines()))
            f = file.read()
            f = f.splitlines()
            file.close()
            for item in f:
              self.add_m3u(item)
            #print(str(f))
        except Exception as e:
            app.status(e, 2)

    def add(self, pathname, quiet=0):
        try:
            if os.path.isdir(pathname):
                app.status("Working...", 10)
                self.add_dir(pathname)
            elif VALID_PLAYLIST(pathname):
                self.add_playlist(pathname)
            else:
                pathname = self.fix_url(pathname)
                self.append(PlaylistEntry(pathname))
            # todo - refactor
            filename = os.path.basename(pathname) or pathname
            quiet or self.update()
            #quiet or app.status("Added: %s" % filename, 1)
        except Exception as e:
            app.status(e, 2)

    def fix_url(self, url):
        return re.sub("(http://[^/]+)/?(.*)", "\\1/\\2", url)

    def putstr(self, entry, *pos):
        # Color of an active *playlist* item
        if entry.is_active(): self.attron(curses.color_pair(3))
        ListWindow.putstr(self, entry, *pos)
        if entry.is_active(): self.attroff(curses.color_pair(3))

    def change_active_entry(self, direction):
        if not self.buffer: return
        old = self.get_active_entry()
        new = None
        if self.random:
            if direction > 0:
                if self.random_next: new = self.random_next.pop()
                elif self.random_left: pass
                elif self.repeat: self.random_left = self.buffer[:]
                else: return
                if not new:
                    import random
                    new = random.choice(self.random_left)
                    self.random_left.remove(new)
                try: self.random_prev.remove(new)
                except ValueError: pass
                self.random_prev.append(new)
            else:
                if len(self.random_prev) > 1:
                    self.random_next.append(self.random_prev.pop())
                    new = self.random_prev[-1]
                else: return
            old and old.set_active(0)
        elif old:
            index = self.buffer.index(old)+direction
            if not (0 <= index < len(self.buffer) or self.repeat): return
            old.set_active(0)
            new = self.buffer[index % len(self.buffer)]
        else:
            new = self.buffer[0]
        new.set_active(1)
        self.update()
        return new

    def get_active_entry(self):
        for entry in self.buffer:
            if entry.is_active(): return entry

    def command_jump_to_active(self):
        entry = self.get_active_entry()
        if not entry: return
        self.bufptr = self.buffer.index(entry)
        self.update()

    def command_play(self):
        if not self.buffer: return
        entry = self.get_active_entry()
        entry and entry.set_active(0)
        entry = self.current()
        entry.set_active(1)
        self.update()
        app.play(entry)

    def command_delete(self):
        if not self.buffer: return
        current_entry, n = self.current(), len(self.buffer)
        self.buffer = self.not_tagged(self.buffer)
        if n > len(self.buffer):
            try: self.bufptr = self.buffer.index(current_entry)
            except ValueError: pass
        else:
            current_entry.set_tagged(1)
            del self.buffer[self.bufptr]
        if self.random:
            self.random_prev = self.not_tagged(self.random_prev)
            self.random_next = self.not_tagged(self.random_next)
            self.random_left = self.not_tagged(self.random_left)
        self.update()

    def command_delete_all(self):
        self.buffer = []
        self.random_prev = []
        self.random_next = []
        self.random_left = []
        app.status("Deleted playlist", 1)
        self.update()

    def command_move(self, after):
        if not self.buffer: return
        current_entry, l = self.current(), self.get_tagged()
        if not l or current_entry.is_tagged(): return
        self.buffer = self.not_tagged(self.buffer)
        self.bufptr = self.buffer.index(current_entry)+after
        self.buffer[self.bufptr:self.bufptr] = l
        self.update()

    def command_shuffle(self):
        import random
        l = []
        n = len(self.buffer)
        while n > 0:
            n = n-1
            r = random.randint(0, n)
            l.append(self.buffer[r])
            del self.buffer[r]
        self.buffer = l
        self.bufptr = 0
        self.update()
        app.status("Shuffled playlist... Oops?", 1)

    def command_sort(self):
        app.status("Working...")
        self.buffer = sorted(self.buffer, key=lambda x: x.vp() or -1)
        self.bufptr = 0
        self.update()
        app.status("Sorted playlist", 1)

    def command_toggle_repeat(self):
        self.toggle("repeat", "Repeat: %s")

    def command_toggle_random(self):
        self.toggle("random", "Random: %s")
        self.random_prev = []
        self.random_next = []
        self.random_left = self.buffer[:]

    def command_toggle_stop(self):
        self.toggle("stop", "Stop playlist: %s")

    def toggle(self, attr, format):
        setattr(self, attr, not getattr(self, attr))
        app.status(format % (getattr(self, attr) and "on" or "off"), 1)
        self.parent.update_title()

    def command_save_playlist(self):
        if app.restricted: return
        default = self.pathname or "%s/" % app.win_filelist.cwd
        app.stop_input_hook = self.stop_save_playlist
        app.start_input("Save playlist", default)

    def stop_save_playlist(self):
        pathname = app.input_string
        if pathname[0] != '/':
            pathname = os.path.join(app.win_filelist.cwd, pathname)
        if not re.search("\.m3u$", pathname, re.I):
            pathname = "%s%s" % (pathname, ".m3u")
        try:
            file = open(pathname, "w")
            for entry in self.buffer:
                file.write("%s\n" % entry.pathname)
            file.close()
            self.pathname = pathname
            app.status("ok", 1)
        except Exception as e:
            app.status(e, 2)

# ------------------------------------------
def get_tag(pathname):
    if re.compile("^http://").match(pathname) or not os.path.exists(pathname):
        return pathname

    tags = {}
    tagb = "N/A - " + os.path.basename(pathname)

    if re.compile(".*\.ogg$", re.I).match(pathname):
        try:
            import ogg.vorbis
            vf = ogg.vorbis.VorbisFile(pathname)
            vc = vf.comment()
            tags = vc.as_dict()
        except: return tagb
    elif re.compile(".*\.mp3$", re.I).match(pathname):
        try:
            import ID3
            vc = ID3.ID3(pathname, as_tuple=1)
            tags = vc.as_dict()
        except ImportError:
            try:
                from pyid3lib import tag as ID3
                vc = ID3(pathname)
                tagtoframeid = {
                    'ALBUM' : 'TALB', 'ARTIST' : 'TPE1', 'TITLE' : 'TIT2',
                    'YEAR'  : 'TYER', 'GENRE'  : 'TCON', 'TRACKNUMBER' : 'TRCK'
                }
                for tag, fid in tagtoframeid.items():
                    try:
                        index = vc.index(fid)
                        tags[tag] = (vc[index]["text"],)
                    except ValueError:
                        if tag in ["ARTIST", "TITLE"]: tags[tag] = ("",)
                        else: tags[tag] = ("N/A",)
            except: pass
        except: return tagb
    else:
        return tagb

    artist = tags.get("ARTIST", [""])[0]
    title = tags.get("TITLE", [""])[0]
    try:
        import codecs
        if artist and title:
            tagb = codecs.latin_1_encode(artist)[0] + " - " + codecs.latin_1_encode(title)[0]
        elif artist:
            tagb = artist
        elif title:
            tagb = title
        return codecs.latin_1_encode(tagb)[0]
    except: return tagb

# ------------------------------------------
class Player:

    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()

    def __init__(self, commandline, files, fps=1):
        self.commandline = commandline
        self.re_files = re.compile(files, re.I)
        self.fps = fps
        self.entry = None
        self.stopped = 0
        self.paused = 0
        self.time_setup = None
        self.buf = ''
        self.tid = None

    def setup(self, entry, offset):
        self.argv = str.split(self.commandline)
        self.argv[0] = which(self.argv[0])
        for i in range(len(self.argv)):
            if self.argv[i] in ["%s", "{file}"]:
                self.argv[i] = entry.pathname
            if self.argv[i] in ["%d", "{offset}"]:
                self.argv[i] = str(offset*self.fps)
        self.entry = entry
        self.offset = offset
        if offset == 0:
            app.progress(0)
            self.offset = 0
            self.length = 0
        self.time_setup = time.time()
        return self.argv[0]

    def play(self):
        self.pid = os.fork()
        if self.pid == 0:
            os.dup2(self.stdin_w, sys.stdin.fileno())
            os.dup2(self.stdout_w, sys.stdout.fileno())
            os.dup2(self.stderr_w, sys.stderr.fileno())
            os.setpgrp()
            try: os.execv(self.argv[0], self.argv)
            except: os._exit(1)
        self.stopped = 0
        self.paused = 0
        self.step = 0
        self.update_status()

    def stop(self, quiet=0):
        self.paused and self.toggle_pause(quiet)
        try:
            while 1:
                try: os.kill(-self.pid, signal.SIGINT)
                except os.error: pass
                os.waitpid(self.pid, os.WNOHANG)
        except Exception: pass
        self.stopped = 1
        quiet or self.update_status()

    def toggle_pause(self, quiet=0):
        try: os.kill(-self.pid, [signal.SIGSTOP, signal.SIGCONT][self.paused])
        except os.error: return
        self.paused = not self.paused
        quiet or self.update_status()

    def parse_progress(self):
        if self.stopped or self.step: self.tid = None
        else:
            self.parse_buf()
            self.tid = app.timeout.add(1.0, self.parse_progress)

    def read_fd(self, fd):
        self.buf = os.read(fd, 512)
        self.tid or self.parse_progress()

    def poll(self):
        try: os.waitpid(self.pid, os.WNOHANG)
        except:
            # something broken? try again
            if self.time_setup and (time.time() - self.time_setup) < 2.0:
                self.play()
                return 0
            app.set_default_status("")
            app.counter([0,0])
            app.progress(0)
            return 1

    def seek(self, offset, relative):
        if relative:
            d = offset * self.length * 0.002
            self.step = self.step * (self.step * d > 0) + d
            self.offset = min(self.length, max(0, self.offset+self.step))
        else:
            self.step = 1
            self.offset = (offset < 0) and self.length+offset or offset
        self.show_position()

    def set_position(self, offset, length):
        self.offset = offset
        self.length = length
        self.show_position()

    def show_position(self):
        app.counter((self.offset, self.length-self.offset))
        app.progress(self.length and (float(self.offset) / self.length))

    def update_status(self):
        if not self.entry:
            app.set_default_status("")
        elif self.stopped:
            app.set_default_status("Stopped: %s" % self.entry.vp())
        elif self.paused:
            app.set_default_status("Paused: %s" % self.entry.vp())
        else:
            app.set_default_status("Playing: %s" % self.entry.vp())

# ------------------------------------------
class FrameOffsetPlayer(Player):
    re_progress = re.compile("Time.*\s(\d+):(\d+).*\[(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            m1, s1, m2, s2 = map(string.atoi, match.groups())
            head, tail = m1*60+s1, m2*60+s2
            self.set_position(head, head+tail)

# ------------------------------------------
class FrameOffsetPlayerMpp(Player):
    re_progress = re.compile(".*\s(\d+):(\d+).*\s(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            m1, s1, m2, s2 = map(string.atoi, match.groups())
            head = m1*60+s1
            tail = (m2*60+s2) - head
            self.set_position(head, head+tail)

# ------------------------------------------
class TimeOffsetPlayer(Player):
    re_progress = re.compile("(\d+):(\d+):(\d+)")

    def parse_buf(self):
        match = self.re_progress.search(self.buf)
        if match:
            h, m, s = map(string.atoi, match.groups())
            tail = h*3600+m*60+s
            head = max(self.length, tail) - tail
            self.set_position(head, head+tail)

# ------------------------------------------
class TimeOffsetPlayerMplayer(Player):
    re_progress = re.compile("^A:.*?(\d+)\.\d \([^)]+\) of (\d+)\.\d")

    def play(self):
        self.fd = None
        try:
            if os.path.exists(CONTROL_FIFO + "-mplayer"):
                os.unlink(CONTROL_FIFO + "-mplayer")
            os.mkfifo(CONTROL_FIFO + "-mplayer", 0o600)
            Player.play(self)
            self.fd = open(CONTROL_FIFO + "-mplayer", "w")
            self.fd.write("seek %d\n" % self.offset)
            self.fd.flush()
            self.fd.close()
        except IOError:
            return

    def parse_buf(self):
        match = self.re_progress.search(self.buf.decode('utf-8'))
        if match:
            curS, totS = map(int, match.groups())
            position, length = curS, totS
            self.set_position(position, length)

# ------------------------------------------
class NoOffsetPlayer(Player):

    def parse_buf(self):
        head = self.offset+1
        self.set_position(head, head*2)

    def seek(self, *dummy):
        return 1

# ------------------------------------------
class Timeout:
    def __init__(self):
        self.next = 0
        self.dict = {}

    def add(self, timeout, func, args=()):
        tid = self.next = self.next + 1
        self.dict[tid] = (func, args, time.time() + timeout)
        return tid

    def remove(self, tid):
        del self.dict[tid]

    def check(self, now):
        for tid, (func, args, timeout) in list(self.dict.items()):
            if now >= timeout:
                self.remove(tid)
                func(*args)
        return len(self.dict) and 0.2 or None

# ------------------------------------------
class FIFOControl:
    def __init__(self):
        self.commands = {
            "pause" : [app.toggle_pause, []],
            "next" : [app.next_song, []],
            "prev" : [app.prev_song, []],
            "forward" : [app.seek, [1, 1]],
            "backward" : [app.seek, [-1, 1]],
            "play" : [app.toggle_stop, []],
            "stop" : [app.toggle_stop, []],
            "volume" : [self.volume, None],
            "macro" : [app.run_macro, None],
            "add" : [app.win_playlist.add, None],
            "empty" : [app.win_playlist.command_delete_all, []],
            "quit" : [app.quit, []]
        }
        self.fd = None
        try:
            if os.path.exists(CONTROL_FIFO):
                os.unlink(CONTROL_FIFO)
            os.mkfifo(CONTROL_FIFO, 0o600)
            self.fd = open(CONTROL_FIFO, "rb+", 0)
        except IOError:
            return

    def handle_command(self):
        argv = self.fd.readline().strip().split(" ", 1)
        if argv[0] in self.commands.keys():
            f, a = self.commands[argv[0]]
            if a is None: a = argv[1:]
            f(*a)

    def volume(self, s):
        argv = s.split()
        app.mixer(argv[0], int(argv[1]))

# ------------------------------------------
class Application:
    def __init__(self):
        self.keymapstack = KeymapStack()
        self.input_mode = 0
        self.input_prompt = ""
        self.input_string = ""
        self.do_input_hook = None
        self.stop_input_hook = None
        self.complete_input_hook = None
        self.channels = []
        self.restricted = 0
        self.input_keymap = Keymap()
        self.input_keymap.bind(list(Window.chars), self.do_input)
        self.input_keymap.bind([127, curses.KEY_BACKSPACE], self.do_input, (8,))
        self.input_keymap.bind([21, 23], self.do_input)
        self.input_keymap.bind(['\a', 27], self.cancel_input, ())
        self.input_keymap.bind(['\n', curses.KEY_ENTER], self.stop_input, ())

    def command_macro(self):
        app.do_input_hook = self.do_macro
        app.start_input("macro")

    def do_macro(self, ch):
        app.stop_input()
        self.run_macro(chr(ch))

    def run_macro(self, c):
        for i in MACRO.get(c, ""):
            self.keymapstack.process(ord(i))

    def setup(self):
        if tty:
            self.tcattr = tty.tcgetattr(sys.stdin.fileno())
            tcattr = tty.tcgetattr(sys.stdin.fileno())
            tcattr[0] = tcattr[0] & ~(tty.IXON)
            tty.tcsetattr(sys.stdin.fileno(), tty.TCSANOW, tcattr)
        self.w = curses.initscr()
        # Function start_color() called after initscr
        curses.start_color()
        # Added to support transparency
        curses.use_default_colors()
        # Custom color pairs
        #   pair 0 is always white on black
        #   color -1 is the default color if use_default_colors() is called
        curses.init_pair(1, curses.COLOR_RED, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        # black color explicitly causes display problems on GNU SCREEN
        #curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        #curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)
        # If you'd like it more colorful, you can play with:
        #   BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN and WHITE
        #
        curses.cbreak()
        curses.noecho()
        try: curses.meta(1)
        except: pass
        self.cursor(0)
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, self.handler_quit)
        signal.signal(signal.SIGINT, self.handler_quit)
        signal.signal(signal.SIGTERM, self.handler_quit)
        signal.signal(signal.SIGWINCH, self.handler_resize)
        self.win_root = RootWindow(None)
        self.win_root.update()
        self.win_tab = self.win_root.win_tab
        self.win_filelist = self.win_root.win_tab.win_filelist
        self.win_playlist = self.win_root.win_tab.win_playlist
        self.win_status = self.win_root.win_status
        self.status = self.win_status.status
        self.set_default_status = self.win_status.set_default_status
        self.restore_default_status = self.win_status.restore_default_status
        self.counter = self.win_root.win_counter.counter
        self.progress = self.win_root.win_progress.progress
        self.player = PLAYERS[0]
        self.timeout = Timeout()
        self.play_tid = None
        self.kludge = 0
        self.win_filelist.listdir()
        self.control = FIFOControl()

    def cleanup(self):
        try: curses.endwin()
        except curses.error: return
        XTERM and sys.stderr.write("\033]0;%s\a" % "xterm")
        tty and tty.tcsetattr(sys.stdin.fileno(), tty.TCSADRAIN, self.tcattr)
        print
        try:
            if os.path.exists(CONTROL_FIFO):
                os.unlink(CONTROL_FIFO)
            if os.path.exists(CONTROL_FIFO + "-mplayer"):
                os.unlink(CONTROL_FIFO + "-mplayer")
        except IOError:
            pass

    def run(self):
        while 1:
            now = time.time()
            timeout = self.timeout.check(now)
            self.win_filelist.listdir_maybe(now)
            if not self.player.stopped:
                timeout = 0.5
                if self.kludge and self.player.poll():
                    self.player.stopped = 1  # end of playlist hack
                    if not self.win_playlist.stop:
                        entry = self.win_playlist.change_active_entry(1)
                        entry and self.play(entry)
            R = [sys.stdin, self.player.stdout_r, self.player.stderr_r]
            self.control.fd and R.append(self.control.fd)
            try: r, w, e = select.select(R, [], [], timeout)
            except select.error: continue
            self.kludge = 1
            # user
            if sys.stdin in r:
                c = self.win_root.getch()
                self.keymapstack.process(c)
            # player
            if self.player.stderr_r in r:
                self.player.read_fd(self.player.stderr_r)
            # player
            if self.player.stdout_r in r:
                self.player.read_fd(self.player.stdout_r)
            # remote
            if self.control.fd in r:
                self.control.handle_command()

    def play(self, entry, offset = 0):
        self.kludge = 0
        self.play_tid = None
        if entry is None or offset is None: return
        self.player.stop(quiet=1)
        for self.player in PLAYERS:
            if self.player.re_files.search(entry.pathname):
                if self.player.setup(entry, offset): break
        else:
            app.status("Player not found!", 1)
            self.player.stopped = 0  # keep going
            return
        self.player.play()

    def delayed_play(self, entry, offset):
        if self.play_tid: self.timeout.remove(self.play_tid)
        self.play_tid = self.timeout.add(0.5, self.play, (entry, offset))

    def next_song(self):
        self.delayed_play(self.win_playlist.change_active_entry(1), 0)

    def prev_song(self):
        self.delayed_play(self.win_playlist.change_active_entry(-1), 0)

    def seek(self, offset, relative):
        if not self.player.entry: return
        self.player.seek(offset, relative)
        self.delayed_play(self.player.entry, self.player.offset)

    def toggle_pause(self):
        if not self.player.entry: return
        if not self.player.stopped: self.player.toggle_pause()

    def toggle_stop(self):
        if not self.player.entry: return
        if not self.player.stopped: self.player.stop()
        else: self.play(self.player.entry, self.player.offset)

    def key_volume(self, ch):
        self.mixer("set", (ch & 0x0f)*10)

    def mixer(self, cmd=None, arg=None):
        try: self._mixer(cmd, arg)
        except Exception as e: app.status(e, 2)

    def _mixer(self, cmd, arg):
        try:
            import ossaudiodev
            mixer = ossaudiodev.openmixer()
            get, set = mixer.get, mixer.set
            self.channels = self.channels or \
                [['MASTER', ossaudiodev.SOUND_MIXER_VOLUME],
                 ['PCM', ossaudiodev.SOUND_MIXER_PCM]]
        except ImportError:
            import oss
            mixer = oss.open_mixer()
            get, set = mixer.read_channel, mixer.write_channel
            self.channels = self.channels or \
                [['MASTER', oss.SOUND_MIXER_VOLUME],
                 ['PCM', oss.SOUND_MIXER_PCM]]
        if cmd == "toggle": self.channels.insert(0, self.channels.pop())
        name, channel = self.channels[0]
        if cmd == "cue": arg = min(100, max(0, get(channel)[0] + arg))
        if cmd in ["set", "cue"]: set(channel, (arg, arg))
        app.status("%s volume %s%%" % (name, get(channel)[0]), 1)
        mixer.close()

    def show_input(self):
        n = len(self.input_prompt)+1
        s = cut(self.input_string, self.win_status.cols-n, left=1)
        app.status("%s%s " % (self.input_prompt, s))

    def start_input(self, prompt="", data="", colon=1):
        self.input_mode = 1
        self.cursor(1)
        app.keymapstack.push(self.input_keymap)
        self.input_prompt = prompt + (colon and ": " or "")
        self.input_string = data
        self.show_input()

    def do_input(self, *args):
        if self.do_input_hook:
            return self.do_input_hook(*args)
        ch = args and args[0] or None
        if ch in [8, 127]: # backspace
            self.input_string = self.input_string[:-1]
        elif ch == 9 and self.complete_input_hook:
            self.input_string = self.complete_input_hook(self.input_string)
        elif ch == 21: # C-u
            self.input_string = ""
        elif ch == 23: # C-w
            self.input_string = re.sub("((.* )?)\w.*", "\\1", self.input_string)
        elif ch:
            self.input_string = "%s%c" % (self.input_string, ch)
        self.show_input()

    def stop_input(self, *args):
        self.input_mode = 0
        self.cursor(0)
        app.keymapstack.pop()
        if not self.input_string:
            app.status("cancel", 1)
        elif self.stop_input_hook:
            self.stop_input_hook(*args)
        self.do_input_hook = None
        self.stop_input_hook = None
        self.complete_input_hook = None

    def cancel_input(self):
        self.input_string = ""
        self.stop_input()

    def cursor(self, visibility):
        try: curses.curs_set(visibility)
        except: pass

    def quit(self, status=0):
        self.player.stop(quiet=1)
        sys.exit(status)

    def handler_resize(self, sig, frame):
        # curses trickery
        while 1:
            try: curses.endwin(); break
            except: time.sleep(1)
        self.w.refresh()
        self.win_root.resize()
        self.win_root.update()

    def handler_quit(self, sig, frame):
        self.quit(1)

# ------------------------------------------
def main():
    try:
        opts, args = getopt.getopt(sys.argv[1:], "nrRv")
    except:
        usage = "Usage: %s [-nrRv] [ file | dir | playlist ] ...\n"
        sys.stderr.write(usage % sys.argv[0])
        sys.exit(1)

    global app
    app = Application()

    playlist = []
    if not sys.stdin.isatty():
        playlist = map(str.strip, sys.stdin.readlines())
        os.close(0)
        os.open("/dev/tty", 0)
    try:
        app.setup()
        for opt, optarg in opts:
            if opt == "-n": app.restricted = 1
            if opt == "-r": app.win_playlist.command_toggle_repeat()
            if opt == "-R": app.win_playlist.command_toggle_random()
            if opt == "-v": app.mixer("toggle")
        if args or playlist:
            for i in args or playlist:
                i = os.path.exists(i) and os.path.abspath(i) or i
                app.win_playlist.add(i)
            app.win_tab.change_window()
        app.run()
    except SystemExit:
        app.cleanup()
        # Can we display colors?
        #print curses.has_colors()
    except Exception:
        app.cleanup()
        import traceback
        traceback.print_exc()

# ------------------------------------------
PLAYERS = [
#    FrameOffsetPlayer("ogg123 -q -v -k %d %s", "\.(ogg)$"),
#    FrameOffsetPlayer("splay -f -k %d %s", "(^http://|\.mp[123]$)", 38.28),
#    FrameOffsetPlayer("mpg123 -q -v -k %d %s", "(^http://|\.mp[123]$)", 38.28),
#    FrameOffsetPlayer("mpg321 -q -v -k %d %s", "(^http://|\.mp[123]$)", 38.28),
#    FrameOffsetPlayerMpp("mppdec --gain 2 --start %d %s", "\.mp[cp+]$"),
    TimeOffsetPlayerMplayer("mplayer -fs -input file=%s {file}" %
                            (CONTROL_FIFO + "-mplayer"),
                            "^http://|\.(mp[1234]|ogg|oga|flac|spx|mp[cp+]|mod|xm|fm|s3m|" +
                            "med|col|669|it|mtm|stm|aiff|aif|au|cdr|wav|wma|m4a|m4b|flv)$"),
#    TimeOffsetPlayer("madplay -v --display-time=remaining -s %d %s", "\.mp[123]$"),
#    FrameOffsetPlayer("ogg123 -q -v -k %d %s", "\.(oga|flac|spx)$"),
#    NoOffsetPlayer("mikmod -q -p0 %s", "\.(mod|xm|fm|s3m|med|col|669|it|mtm)$"),
#    NoOffsetPlayer("xmp -q %s", "\.(mod|xm|fm|s3m|med|col|669|it|mtm|stm)$"),
#    NoOffsetPlayer("play %s", "\.(aiff|aif|au|cdr|mp3|ogg|wav)$"),
#    NoOffsetPlayer("speexdec %s", "\.spx$"),
    ]

MACRO = {}

def VALID_SONG(name):
    for player in PLAYERS:
        if player.re_files.search(name):
            return(1)

def VALID_PLAYLIST(name):
    if re.search("\.(m3u|pls)$", name, re.I):
        return(1)

# ------------------------------------------
if __name__ == "__main__":
    main()
