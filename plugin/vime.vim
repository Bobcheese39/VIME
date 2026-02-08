" vime.vim - Vim H5 file viewer plugin
" Requires Vim 8+ for job/channel support
"
" Usage: vim file.h5   -> opens table list
"        :VimeOpen <file>   -> open an H5 file
"        :VimePlot <col1> <col2> [type]  -> plot columns
"        :VimeInfo          -> info about current table

scriptencoding utf-8

if exists('g:loaded_vime')
    finish
endif
let g:loaded_vime = 1

" ======================================================================
" Configuration
" ======================================================================

" Path to the python server script (relative to this plugin file)
let s:script_dir = expand('<sfile>:p:h:h')
let s:server_script = s:script_dir . '/python/vime_server.py'
"
" Optional: override python command for the server.
" Examples:
"   let g:vime_python_cmd = ['python', 'C:/path/to/venv/Scripts/python.exe']
"   let g:vime_python_cmd = 'C:/path/to/venv/Scripts/python.exe'

" State
let s:job = v:null
let s:channel = v:null
let s:current_file = ''
let s:compute_timer = -1
let s:list_bufnr = -1
let s:debug_bufnr = -1
let s:debug_lines = []
let s:debug_max_lines = 500

" ======================================================================
" Job / Channel management
" ======================================================================

function! s:StartServer() abort
    if s:job isnot v:null && job_status(s:job) ==# 'run'
        return 1
    endif

    if !filereadable(s:server_script)
        echoerr 'VIME: Server script not found: ' . s:server_script
        return 0
    endif

    let l:cmd = get(g:, 'vime_python_cmd', ['python3'])
    if type(l:cmd) == v:t_list
        if empty(l:cmd)
            let l:cmd = ['python3']
        endif
        if l:cmd[-1] !=# s:server_script
            call add(l:cmd, s:server_script)
        endif
    elseif type(l:cmd) == v:t_string
        let l:cmd = l:cmd . ' ' . shellescape(s:server_script)
    else
        let l:cmd = ['python3', s:server_script]
    endif
    let s:job = job_start(l:cmd, {
        \ 'mode': 'json',
        \ 'err_mode': 'nl',
        \ 'err_cb': function('s:OnServerError'),
        \ 'exit_cb': function('s:OnServerExit'),
        \ })

    if job_status(s:job) !=# 'run'
        echoerr 'VIME: Failed to start server'
        let s:job = v:null
        return 0
    endif

    let s:channel = job_getchannel(s:job)
    return 1
endfunction

function! s:StopServer() abort
    if s:channel isnot v:null
        try
            call s:Send({'cmd': 'close'})
        catch
        endtry
    endif
    if s:job isnot v:null
        try
            call job_stop(s:job)
        catch
        endtry
    endif
    let s:job = v:null
    let s:channel = v:null
endfunction

function! s:Send(payload) abort
    if s:channel is v:null
        echoerr 'VIME: Server not running'
        return v:null
    endif
    " ch_evalexpr sends [msgid, payload] and waits for [msgid, response]
    let l:resp = ch_evalexpr(s:channel, a:payload, {'timeout': 30000})
    return l:resp
endfunction

function! s:OnServerError(channel, msg) abort
    " Log server stderr to messages
    if type(a:msg) == v:t_list
        for l:line in a:msg
            if l:line !=# ''
                call s:AppendDebugLog(l:line)
            endif
        endfor
    elseif a:msg !=# ''
        call s:AppendDebugLog(a:msg)
    endif
    echohl WarningMsg
    echom 'VIME server: ' . a:msg
    echohl None
endfunction

function! s:OnServerExit(job, status) abort
    let s:job = v:null
    let s:channel = v:null
    if a:status != 0
        echohl ErrorMsg
        echom 'VIME server exited with status ' . a:status
        echohl None
    endif
endfunction

" ======================================================================
" Buffer helpers
" ======================================================================

function! s:CreateScratchBuffer(name, type, ...) abort
    " Open a new scratch buffer, optionally in a split.
    " Optional arg: 'v' for vertical split, 'h' for horizontal split,
    "               '' or omitted for replacing the current buffer.
    let l:split = a:0 >= 1 ? a:1 : ''
    if l:split ==# 'v'
        execute 'vnew'
    elseif l:split ==# 'h'
        execute 'new'
    else
        execute 'enew'
    endif
    setlocal buftype=nofile
    setlocal bufhidden=wipe
    setlocal noswapfile
    setlocal nowrap
    setlocal nobuflisted
    execute 'file ' . fnameescape(a:name)
    let b:vime_type = a:type
    let b:vime_file = s:current_file
    nnoremap <buffer> <silent> ,pdb :call <SID>ToggleDebugBuffer()<CR>
endfunction

function! s:SetBufferContent(lines) abort
    setlocal modifiable
    silent! %delete _
    call setline(1, a:lines)
    setlocal nomodifiable
    normal! gg
endfunction

function! s:WrapWithBorder(lines) abort
    " Calculate maximum display width across all content lines
    let l:maxw = 0
    for l:line in a:lines
        let l:w = strdisplaywidth(l:line)
        if l:w > l:maxw
            let l:maxw = l:w
        endif
    endfor
    let l:maxw = max([l:maxw, 40])

    let l:result = []
    " Top border
    call add(l:result, '┏' . repeat('━', l:maxw + 2) . '┓')
    " Wrap each content line
    for l:line in a:lines
        let l:pad = l:maxw - strdisplaywidth(l:line)
        call add(l:result, '┃ ' . l:line . repeat(' ', l:pad) . ' ┃')
    endfor
    " Bottom border
    call add(l:result, '┗' . repeat('━', l:maxw + 2) . '┛')
    return l:result
endfunction

" ======================================================================
" Debug buffer
" ======================================================================

function! s:AppendToBuffer(bufnr, line) abort
    if !bufexists(a:bufnr)
        return
    endif
    call setbufvar(a:bufnr, '&modifiable', 1)
    let l:last = len(getbufline(a:bufnr, 1, '$'))
    call setbufline(a:bufnr, l:last + 1, a:line)
    call setbufvar(a:bufnr, '&modifiable', 0)
    let l:winid = bufwinid(a:bufnr)
    if l:winid != -1
        call win_execute(l:winid, 'normal! G')
    endif
endfunction

function! s:AppendDebugLog(line) abort
    let l:stamp = strftime('%H:%M:%S')
    call add(s:debug_lines, l:stamp . ' ' . a:line)
    let l:overflow = len(s:debug_lines) - s:debug_max_lines
    if l:overflow > 0
        call remove(s:debug_lines, 0, l:overflow - 1)
    endif
    if s:debug_bufnr > 0 && bufexists(s:debug_bufnr)
        call s:AppendToBuffer(s:debug_bufnr, l:stamp . ' ' . a:line)
    endif
endfunction

function! s:SetDebugKeybindings() abort
    nnoremap <buffer> <silent> ,pdb :call <SID>ToggleDebugBuffer()<CR>
    nnoremap <buffer> <silent> ,q :call <SID>CloseBuf()<CR>
endfunction

function! s:OpenDebugBuffer() abort
    if s:debug_bufnr > 0 && bufexists(s:debug_bufnr)
        let l:winid = bufwinid(s:debug_bufnr)
        if l:winid != -1
            call win_gotoid(l:winid)
            return
        endif
        execute 'new'
        execute 'buffer ' . s:debug_bufnr
        execute 'resize 5'
        call s:SetDebugKeybindings()
        setlocal laststatus=2
        setlocal statusline=%#VimeFooter#\ \ ,pdb\ Toggle\ Debug\ \ │\ \ ,q\ Close%=
        call s:ApplyVimeColors()
        return
    endif

    call s:CreateScratchBuffer('VIME:debug', 'debug', 'h')
    let s:debug_bufnr = bufnr('%')
    execute 'resize 5'
    call s:SetDebugKeybindings()
    setlocal laststatus=2
    setlocal statusline=%#VimeFooter#\ \ ,pdb\ Toggle\ Debug\ \ │\ \ ,q\ Close%=
    call s:ApplyVimeColors()
    if !empty(s:debug_lines)
        call s:SetBufferContent(s:debug_lines)
    endif
endfunction

function! s:ToggleDebugBuffer() abort
    if s:debug_bufnr > 0 && bufexists(s:debug_bufnr) && bufwinid(s:debug_bufnr) != -1
        execute 'bwipeout ' . s:debug_bufnr
        let s:debug_bufnr = -1
        return
    endif
    call s:OpenDebugBuffer()
endfunction

" ======================================================================
" Nord color theme
" ======================================================================

function! s:DefineVimeHighlights() abort
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

function! s:ApplyVimeColors() abort
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

" ======================================================================
" Table list buffer
" ======================================================================

function! s:OpenTableList(filepath) abort
    if !s:StartServer()
        return
    endif

    let s:current_file = a:filepath
    let l:resp = s:Send({'cmd': 'open', 'file': a:filepath})

    if type(l:resp) != v:t_dict
        echoerr 'VIME: Failed to open file (server returned unexpected response)'
        return
    endif
    if !get(l:resp, 'ok', 0)
        echoerr 'VIME: ' . get(l:resp, 'error', 'Failed to open file')
        return
    endif

    " Create the table list buffer
    call s:CreateScratchBuffer('VIME:' . fnamemodify(a:filepath, ':t'), 'list')

    " Build display lines
    let l:lines = []
    let l:header = 'VIME - ' . fnamemodify(a:filepath, ':t')
    " Pad header to window width so the solid highlight fills the bar
    let l:padwidth = max([60, winwidth(0) - 4])
    let l:header .= repeat(' ', max([0, l:padwidth - strdisplaywidth(l:header)]))
    call add(l:lines, l:header)
    call add(l:lines, '')
    call add(l:lines, ' Tables:')
    call add(l:lines, '')

    let b:vime_table_map = {}
    let l:idx = 0
    for l:tbl in l:resp['tables']
        let l:name = l:tbl['name']
        let l:rows = l:tbl['rows']
        let l:cols = l:tbl['cols']
        let l:display = printf('   %-40s (%s rows x %s cols)', l:name, l:rows, l:cols)
        call add(l:lines, l:display)
        " Map line number to table name (1-indexed; 1 border + 4 header lines = offset 6)
        let b:vime_table_map[l:idx + 6] = l:name
        let l:idx += 1
    endfor

    let l:lines = s:WrapWithBorder(l:lines)
    call s:SetBufferContent(l:lines)
    call s:SetListKeybindings()
    setlocal laststatus=2
    let b:vime_status = ''
    let s:list_bufnr = bufnr('%')
    setlocal statusline=%#VimeFooter#\ \ ⏎\ Open\ \ │\ \ ,i\ Info\ \ │\ \ ,r\ Refresh\ \ │\ \ ,c\ Compute\ \ │\ \ ,pdb\ Debug\ \ │\ \ ,q\ Quit%=%{get(b:,'vime_status','')}
    call s:ApplyVimeColors()
endfunction

function! s:SetListKeybindings() abort
    nnoremap <buffer> <silent> <CR> :call <SID>ListSelectTable()<CR>
    nnoremap <buffer> <silent> ,i :call <SID>ListTableInfo()<CR>
    nnoremap <buffer> <silent> ,r :call <SID>ListRefresh()<CR>
    nnoremap <buffer> <silent> ,c :call <SID>ListComputeStart()<CR>
    nnoremap <buffer> <silent> ,q :call <SID>VimeQuit()<CR>
endfunction

function! s:GetTableNameUnderCursor() abort
    let l:lnum = line('.')
    if exists('b:vime_table_map') && has_key(b:vime_table_map, l:lnum)
        return b:vime_table_map[l:lnum]
    endif
    " Fallback: try to extract table name from line text
    let l:line = getline('.')
    let l:match = matchstr(l:line, '\s\+\zs/\S\+')
    if l:match !=# ''
        return l:match
    endif
    return ''
endfunction

function! s:ListSelectTable() abort
    let l:name = s:GetTableNameUnderCursor()
    if l:name ==# ''
        echo 'VIME: No table under cursor'
        return
    endif
    call s:OpenTable(l:name, 100)
endfunction

function! s:ListTableInfo() abort
    let l:name = s:GetTableNameUnderCursor()
    if l:name ==# ''
        echo 'VIME: No table under cursor'
        return
    endif
    call s:ShowInfo(l:name)
endfunction

function! s:ListRefresh() abort
    if s:current_file !=# ''
        call s:OpenTableList(s:current_file)
    endif
endfunction

function! s:SetListStatus(msg) abort
    if s:list_bufnr > 0 && bufexists(s:list_bufnr)
        call setbufvar(s:list_bufnr, 'vime_status', a:msg)
    endif
    redrawstatus
endfunction

function! s:StopComputeTimer() abort
    if s:compute_timer != -1
        call timer_stop(s:compute_timer)
        let s:compute_timer = -1
    endif
endfunction

function! s:ListComputeStart() abort
    if s:current_file ==# ''
        echo 'VIME: No file open'
        return
    endif
    let l:resp = s:Send({'cmd': 'compute_start'})
    if type(l:resp) != v:t_dict
        echoerr 'VIME: Failed to start compute (server returned unexpected response)'
        return
    endif
    if !get(l:resp, 'ok', 0)
        call s:SetListStatus(get(l:resp, 'error', 'Compute already running'))
        return
    endif
    call s:SetListStatus(get(l:resp, 'message', 'Computing...'))
    call s:StopComputeTimer()
    let s:compute_timer = timer_start(1000, function('s:ComputePoll'), {'repeat': -1})
endfunction

function! s:ComputePoll(timer_id) abort
    let l:resp = s:Send({'cmd': 'compute_status'})
    if type(l:resp) != v:t_dict || !get(l:resp, 'ok', 0)
        call s:SetListStatus('Compute status error')
        call s:StopComputeTimer()
        return
    endif
    let l:status = get(l:resp, 'status', '')
    if l:status ==# 'running'
        call s:SetListStatus(get(l:resp, 'message', 'Computing...'))
        return
    endif
    if l:status ==# 'done'
        let l:name = get(l:resp, 'table', '')
        call s:StopComputeTimer()
        call s:OpenTableList(s:current_file)
        let l:msg = l:name ==# '' ? 'Compute done' : ('Compute done: ' . l:name)
        call s:SetListStatus(l:msg)
        return
    endif
    if l:status ==# 'error'
        let l:err = get(l:resp, 'error', 'Compute failed')
        call s:SetListStatus('Compute failed: ' . l:err)
        call s:StopComputeTimer()
        return
    endif
endfunction

" ======================================================================
" Table content buffer
" ======================================================================

function! s:OpenTable(name, head) abort
    let l:resp = s:Send({'cmd': 'table', 'name': a:name, 'head': a:head})

    if type(l:resp) != v:t_dict
        echoerr 'VIME: Failed to read table (server returned unexpected response)'
        return
    endif
    if !get(l:resp, 'ok', 0)
        echoerr 'VIME: ' . get(l:resp, 'error', 'Failed to read table')
        return
    endif

    call s:CreateScratchBuffer('VIME:' . a:name, 'table')
    let b:vime_table_name = a:name
    let b:vime_columns = get(l:resp, 'columns', [])

    let l:lines = split(l:resp['content'], "\n")
    let l:lines = s:WrapWithBorder(l:lines)

    call s:SetBufferContent(l:lines)
    call s:SetTableKeybindings()
    setlocal laststatus=2
    setlocal statusline=%#VimeFooter#\ \ ,p\ Plot\ \ │\ \ ,pv\ V-Plot\ \ │\ \ ,ph\ H-Plot\ \ │\ \ ,pq\ Close\ Plot\ \ │\ \ ,b\ Back\ \ │\ \ ,h\ Head\ \ │\ \ ,a\ All\ \ │\ \ ,i\ Info\ \ │\ \ ,pdb\ Debug\ \ │\ \ ,q\ Close%=
    call s:ApplyVimeColors()
endfunction

function! s:SetTableKeybindings() abort
    nnoremap <buffer> <silent> ,p :call <SID>TablePlotPrompt()<CR>
    nnoremap <buffer> <silent> ,pv :call <SID>TablePlotPrompt('v')<CR>
    nnoremap <buffer> <silent> ,ph :call <SID>TablePlotPrompt('h')<CR>
    nnoremap <buffer> <silent> ,pq :call <SID>ClosePlotBuffers()<CR>
    nnoremap <buffer> <silent> ,b :call <SID>BackToList()<CR>
    nnoremap <buffer> <silent> ,q :call <SID>CloseBuf()<CR>
    nnoremap <buffer> <silent> ,h :call <SID>TableHead()<CR>
    nnoremap <buffer> <silent> ,a :call <SID>TableAll()<CR>
    nnoremap <buffer> <silent> ,i :call <SID>TableInfoCurrent()<CR>
endfunction

function! s:TablePlotPrompt(...) abort
    " Optional arg: split direction ('v', 'h', or '' for no split)
    let l:split = a:0 >= 1 ? a:1 : ''

    let l:cols_str = ''
    if exists('b:vime_columns') && len(b:vime_columns) > 0
        let l:cols_str = '  Columns: '
        let l:idx = 0
        for l:c in b:vime_columns
            let l:cols_str .= printf('[%d]%s ', l:idx, l:c)
            let l:idx += 1
        endfor
        echo l:cols_str
    endif
    let l:input = input('Plot (x_col y_col [scatter]): ')
    if l:input ==# ''
        return
    endif
    let l:parts = split(l:input)
    if len(l:parts) < 2
        echo "\nVIME: Need at least 2 column references"
        return
    endif
    let l:col1 = l:parts[0]
    let l:col2 = l:parts[1]
    let l:ptype = len(l:parts) >= 3 ? l:parts[2] : 'line'
    call s:DoPlot(l:col1, l:col2, l:ptype, l:split)
endfunction

function! s:TableHead() abort
    let l:n = input('Show first N rows (default 100): ')
    if l:n ==# ''
        let l:n = 100
    else
        let l:n = str2nr(l:n)
    endif
    if exists('b:vime_table_name')
        call s:OpenTable(b:vime_table_name, l:n)
    endif
endfunction

function! s:TableAll() abort
    if exists('b:vime_table_name')
        call s:OpenTable(b:vime_table_name, 0)
    endif
endfunction

function! s:TableInfoCurrent() abort
    if exists('b:vime_table_name')
        call s:ShowInfo(b:vime_table_name)
    endif
endfunction

" ======================================================================
" Plot buffer
" ======================================================================

function! s:DoPlot(col1, col2, plot_type, ...) abort
    " Optional arg: split direction ('v', 'h', or '' for no split)
    let l:split = a:0 >= 1 ? a:1 : ''

    let l:orig_winid = win_getid()
    let l:orig_bufnr = bufnr('%')

    " Try to convert to integers if they look numeric
    let l:c1 = a:col1 =~# '^\d\+$' ? str2nr(a:col1) : a:col1
    let l:c2 = a:col2 =~# '^\d\+$' ? str2nr(a:col2) : a:col2

    " Create the buffer first so we can measure the window size
    call s:CreateScratchBuffer('VIME:plot', 'plot', l:split)

    " Measure the new window and compute plot dimensions
    " Border wrapping adds 4 chars width (│ + │) and 2 lines (top + bottom)
    " Plot content adds: 2 title lines + 4 axis lines = 6 extra lines
    let l:plot_width = max([20, winwidth(0) - 4])
    let l:plot_height = max([5, winheight(0) - 8])

    let l:resp = s:Send({
        \ 'cmd': 'plot',
        \ 'cols': [l:c1, l:c2],
        \ 'type': a:plot_type,
        \ 'width': l:plot_width,
        \ 'height': l:plot_height,
        \ })

    if type(l:resp) != v:t_dict
        echoerr 'VIME: Failed to generate plot (server returned unexpected response)'
        return
    endif
    if !get(l:resp, 'ok', 0)
        echoerr 'VIME: ' . get(l:resp, 'error', 'Failed to generate plot')
        return
    endif

    let l:lines = split(l:resp['content'], "\n")
    let l:lines = s:WrapWithBorder(l:lines)

    call s:SetBufferContent(l:lines)
    call s:SetPlotKeybindings()
    let b:vime_prev_laststatus = &laststatus
    setlocal statusline=
    let &laststatus = 0
    augroup vime_plot_statusline
        autocmd! * <buffer>
        autocmd BufWinLeave,BufWipeout <buffer> let &laststatus = get(b:, 'vime_prev_laststatus', 2)
    augroup END
    call s:ApplyVimeColors()

    if win_gotoid(l:orig_winid)
        execute 'buffer ' . l:orig_bufnr
    endif
endfunction

function! s:SetPlotKeybindings() abort
    nnoremap <buffer> <silent> ,b :call <SID>BackToTable()<CR>
    nnoremap <buffer> <silent> ,q :call <SID>CloseBuf()<CR>
    nnoremap <buffer> <silent> ,pq :call <SID>CloseBuf()<CR>
endfunction

" ======================================================================
" Info buffer
" ======================================================================

function! s:ShowInfo(name) abort
    let l:resp = s:Send({'cmd': 'info', 'name': a:name})

    if type(l:resp) != v:t_dict
        echoerr 'VIME: Failed to get info (server returned unexpected response)'
        return
    endif
    if !get(l:resp, 'ok', 0)
        echoerr 'VIME: ' . get(l:resp, 'error', 'Failed to get info')
        return
    endif

    call s:CreateScratchBuffer('VIME:info:' . a:name, 'info')
    let l:lines = split(l:resp['content'], "\n")
    let l:lines = s:WrapWithBorder(l:lines)

    call s:SetBufferContent(l:lines)
    nnoremap <buffer> <silent> ,b :call <SID>BackToList()<CR>
    nnoremap <buffer> <silent> ,q :call <SID>CloseBuf()<CR>
    setlocal laststatus=2
    setlocal statusline=%#VimeFooter#\ \ ,b\ Back\ \ │\ \ ,pdb\ Debug\ \ │\ \ ,q\ Close%=
    call s:ApplyVimeColors()
endfunction

" ======================================================================
" Navigation
" ======================================================================

function! s:BackToList() abort
    if s:current_file !=# ''
        call s:OpenTableList(s:current_file)
    endif
endfunction

function! s:BackToTable() abort
    " Try to find the table buffer to go back to
    for l:buf in getbufinfo()
        if getbufvar(l:buf.bufnr, 'vime_type', '') ==# 'table'
            execute 'buffer ' . l:buf.bufnr
            return
        endif
    endfor
    " Fallback: go back to list
    call s:BackToList()
endfunction

function! s:CloseBuf() abort
    " Close current VIME buffer
    let l:type = get(b:, 'vime_type', '')
    if l:type ==# 'list'
        call s:VimeQuit()
    elseif l:type ==# 'debug'
        let s:debug_bufnr = -1
        bwipeout
    else
        bwipeout
    endif
endfunction

function! s:ClosePlotBuffers() abort
    " Close any open plot buffers without leaving the table
    let l:closed = 0
    for l:buf in getbufinfo()
        if getbufvar(l:buf.bufnr, 'vime_type', '') ==# 'plot'
            try
                execute 'bwipeout ' . l:buf.bufnr
                let l:closed = 1
            catch
            endtry
        endif
    endfor
    if !l:closed
        echo 'VIME: No plot buffer to close'
    endif
endfunction

function! s:VimeQuit() abort
    " Close all VIME buffers and stop server
    let l:bufs = []
    for l:buf in getbufinfo()
        if getbufvar(l:buf.bufnr, 'vime_type', '') !=# ''
            call add(l:bufs, l:buf.bufnr)
        endif
    endfor
    for l:b in l:bufs
        try
            execute 'bwipeout ' . l:b
        catch
        endtry
    endfor
    let s:debug_bufnr = -1
    call s:StopServer()
    " Open a new empty buffer so we don't exit vim
    enew
endfunction

" ======================================================================
" Commands
" ======================================================================

command! -nargs=1 -complete=file VimeOpen call s:OpenTableList(fnamemodify(<q-args>, ':p'))

command! -nargs=+ VimePlot call s:VimePlotCmd(<f-args>)
function! s:VimePlotCmd(...) abort
    if a:0 < 2
        echoerr 'Usage: :VimePlot <col1> <col2> [scatter|line]'
        return
    endif
    let l:ptype = a:0 >= 3 ? a:3 : 'line'
    call s:DoPlot(a:1, a:2, l:ptype)
endfunction

command! -nargs=0 VimeInfo call s:VimeInfoCmd()
function! s:VimeInfoCmd() abort
    if exists('b:vime_table_name')
        call s:ShowInfo(b:vime_table_name)
    else
        echo 'VIME: No table loaded in this buffer'
    endif
endfunction

" Initialize Nord highlight groups
call s:DefineVimeHighlights()

" ======================================================================
" Autocommand - intercept opening .h5 / .hdf5 files
" ======================================================================

augroup vime_filetype
    autocmd!
    autocmd BufReadCmd *.h5,*.hdf5 call s:OnOpenH5(expand('<afile>:p'))
augroup END

function! s:OnOpenH5(filepath) abort
    " Prevent Vim from trying to read the binary file
    " Instead, start VIME and show the table list
    call s:OpenTableList(a:filepath)
endfunction
