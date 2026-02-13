" autoload/vime/colors.vim - Nord color theme for VIME
" Highlight definitions and syntax application.

scriptencoding utf-8

function! vime#colors#define() abort
    highlight VimeNormal     guifg=#D8DEE9 guibg=#2E3440 ctermfg=253 ctermbg=236
    highlight VimeHeader     guifg=#2E3440 guibg=#81A1C1 ctermfg=236 ctermbg=109 gui=bold cterm=bold
    highlight VimeBorder     guifg=#4C566A guibg=NONE    ctermfg=60  ctermbg=NONE
    highlight VimeTableName  guifg=#88C0D0 guibg=NONE    ctermfg=110 ctermbg=NONE
    highlight VimeTableDims  guifg=#4C566A guibg=NONE    ctermfg=60  ctermbg=NONE
    highlight VimePlotAxis   guifg=#81A1C1 guibg=NONE    ctermfg=109 ctermbg=NONE
    highlight VimePlotData   guifg=#A3BE8C guibg=NONE    ctermfg=144 ctermbg=NONE
    highlight VimeFooter     guifg=#D8DEE9 guibg=#3B4252 ctermfg=253 ctermbg=238 gui=bold cterm=bold
    highlight VimeTitle      guifg=#EBCB8B guibg=NONE    ctermfg=222 ctermbg=NONE
    highlight VimeMuted      guifg=#616E88 guibg=NONE    ctermfg=60  ctermbg=NONE
    highlight VimeGridLine   guifg=#4C566A guibg=NONE    ctermfg=60  ctermbg=NONE
endfunction

function! vime#colors#apply() abort
    " Set buffer-local window highlight if supported
    if exists('+winhighlight')
        setlocal winhighlight=Normal:VimeNormal
    endif

    syntax clear
    " Border characters (outer frame)
    syntax match VimeBorder /[┏┓┗┛┣┫┳┻╋━┃]/
    syntax match VimeBorder /^┃/
    syntax match VimeBorder /┃$/

    " Header bar (solid color, entire line)
    syntax match VimeHeader /^│\? *VIME - .*$/

    " Table names (paths starting with /)
    syntax match VimeTableName /\/\S\+/

    " Dimensions
    syntax match VimeTableDims /(\S\+ rows x \S\+ cols)/

    " Section titles
    syntax match VimeTitle /\<Tables:\>/
    syntax match VimeTitle /\<Table:\>/
    syntax match VimeTitle /\<Columns:\>/
    syntax match VimeTitle /\<Shape:\>/
    syntax match VimeTitle /\<Numeric Summary:\>/
    syntax match VimeTitle /^│\? *Plot:.*$/

    " Braille plot data (U+2801 through U+28FF)
    syntax match VimePlotData /[⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏⠐⠑⠒⠓⠔⠕⠖⠗⠘⠙⠚⠛⠜⠝⠞⠟⠠⠡⠢⠣⠤⠥⠦⠧⠨⠩⠪⠫⠬⠭⠮⠯⠰⠱⠲⠳⠴⠵⠶⠷⠸⠹⠺⠻⠼⠽⠾⠿⡀⡁⡂⡃⡄⡅⡆⡇⡈⡉⡊⡋⡌⡍⡎⡏⡐⡑⡒⡓⡔⡕⡖⡗⡘⡙⡚⡛⡜⡝⡞⡟⡠⡡⡢⡣⡤⡥⡦⡧⡨⡩⡪⡫⡬⡭⡮⡯⡰⡱⡲⡳⡴⡵⡶⡷⡸⡹⡺⡻⡼⡽⡾⡿⢀⢁⢂⢃⢄⢅⢆⢇⢈⢉⢊⢋⢌⢍⢎⢏⢐⢑⢒⢓⢔⢕⢖⢗⢘⢙⢚⢛⢜⢝⢞⢟⢠⢡⢢⢣⢤⢥⢦⢧⢨⢩⢪⢫⢬⢭⢮⢯⢰⢱⢲⢳⢴⢵⢶⢷⢸⢹⢺⢻⢼⢽⢾⢿⣀⣁⣂⣃⣄⣅⣆⣇⣈⣉⣊⣋⣌⣍⣎⣏⣐⣑⣒⣓⣔⣕⣖⣗⣘⣙⣚⣛⣜⣝⣞⣟⣠⣡⣢⣣⣤⣥⣦⣧⣨⣩⣪⣫⣬⣭⣮⣯⣰⣱⣲⣳⣴⣵⣶⣷⣸⣹⣺⣻⣼⣽⣾⣿]/

    " Table grid lines (heavy box-drawing from tabulate heavy_grid)
    syntax match VimeGridLine  /^[┌└][─]*[┐┘]$/
endfunction
