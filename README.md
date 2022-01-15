
                            .__
        _____   ____ ______ |  | _____  ___.__.
       /     \_/ ___\\____ \|  | \__  \<   |  |
      |  Y Y  \  \___|  |_> >  |__/ __ \\___  |
      |__|_|  /\___  >   __/|____(____  / ____|
            \/     \/|__|             \/\/
      
      ---== mcplayer ==---
      music console player 
        mmxxii edition 
      ---== ------- ===---
        
![MCPLAYER SCREEN](https://raw.githubusercontent.com/mtatton/mcplay/master/mcplay.png)

Description:

        play is a curses front-end for various audio players, based on
        cplay. It aims to provide a power-user-friendly interface with
        simple filelist and playlist control. play is written in
        Python and can use either pyncurses or the standard curses
        module.

Usage:

        play [-nrRv] [ file | dir | playlist | url ] ...

        When in doubt, press 'h' for a friendly help page.

Keyboard controls:

        Global                               t, T  : tag current/regex
        ------                               u, U  : untag current/regex
        Up, Down, k, j, C-p, C-n,            Sp, i : invert current/all
        PgUp, PgDn, K, J,                    !, ,  : shell, macro
        Home, End, g, G : movement
        Enter           : chdir or play      Filelist
        Tab             : filelist/playlist  --------
        n, p            : next/prev track    a     : add (tagged) to playlist
        z, x            : toggle pause/stop  s     : recursive search
                                             BS, o : goto parent/specified dir
        Left, Right,                         m, '  : set/get bookmark
        C-f, C-b    : seek forward/backward
        C-a, C-e    : restart/end track      Playlist
        C-s, C-r, / : isearch                --------
        C-g, Esc    : cancel                 d, D  : delete (tagged) tracks/playlist
        1..9, +, -  : volume control         m, M  : move tagged tracks after/before
        c, v        : counter/volume mode    r, R  : toggle repeat/Random mode
        <, >        : horizontal scrolling   s, S  : shuffle/Sort playlist
        C-l, l      : refresh, list mode     w, @  : write playlist, jump to active
        h, q, Q     : help, quit?, Quit!     X     : stop playlist after each track

Miscellaneous:

        In order for either mp3info (ID3) or ogginfo to work, both
        corresponding python modules have to be installed. Play can
        use 'id3-py' or 'pyid3lib' for MP3 tags, and 'pyvorbis' for
        OGG tags.

        A playlist can contain URLs, but the playlist itself will have
        to be local. For mpeg streaming use splay or mplayer.

        It is also possible to pipe a playlist to play, as stdin will
        be reopened on startup unless it is attached to a tty.

        Remote control is available through /tmp/play-control-$USER.

Authors:

        play is being maintained by Adrian C. After waiting over 2
        years for cplay's home, and code, to resurface it was finally
        forked. This exceptional software, originally written by Ulf
        Betlehem, should not be forgotten.

        MPlayer support was added to play from the excellent work done
        by Tom Adams and Tomi Pievilainen on GitHub.com.

        I basically just ripped it from
        http://git.sysphere.org/play/
        and modified it and put it on my github for convenience. I got tired
        of it not working the way I wanted it to. That is using only mplayer
        and then maybe to add gif/jpeg support again which I had working in
        a copy somewhere .... no credit to me what so ever.
        gusten@dindinoh.net

        20220115 Some basic conversion to python3 has been done. I tested
        just several functions on Python 3.7. It does read the directory,
        it does play the m3u. More testing to be done. (dev@null)
