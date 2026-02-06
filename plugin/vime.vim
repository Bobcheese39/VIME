" vime.vim - Vim H5 file viewer plugin
" Requires Vim 8+ for job/channel support
"
" Usage: vim file.h5   -> opens table list
"        :VimeOpen <file>   -> open an H5 file
"        :VimePlot <col1> <col2> [type]  -> plot columns
"        :VimeInfo          -> info about current table

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

" State
let s:job = v:null
let s:channel = v:null
let s:current_file = ''

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

    let l:cmd = ['python3', s:server_script]
    let s:job = job_start(l:cmd, {
        \ 'mode': 'json',
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

function! s:CreateScratchBuffer(name, type) abort
    " Open a new scratch buffer in a vertical split
    execute 'enew'
    setlocal buftype=nofile
    setlocal bufhidden=wipe
    setlocal noswapfile
    setlocal nowrap
    setlocal nobuflisted
    execute 'file ' . fnameescape(a:name)
    let b:vime_type = a:type
    let b:vime_file = s:current_file
endfunction

function! s:SetBufferContent(lines) abort
    setlocal modifiable
    silent! %delete _
    call setline(1, a:lines)
    setlocal nomodifiable
    normal! gg
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

    if type(l:resp) != v:t_dict || !get(l:resp, 'ok', 0)
        echoerr 'VIME: ' . get(l:resp, 'error', 'Failed to open file')
        return
    endif

    " Create the table list buffer
    call s:CreateScratchBuffer('VIME:' . fnamemodify(a:filepath, ':t'), 'list')

    " Build display lines
    let l:lines = []
    call add(l:lines, ' VIME - ' . fnamemodify(a:filepath, ':t'))
    call add(l:lines, ' ' . repeat('=', 60))
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
        " Map line number to table name (lines are 1-indexed, header takes 5 lines)
        let b:vime_table_map[l:idx + 6] = l:name
        let l:idx += 1
    endfor

    call add(l:lines, '')
    call add(l:lines, ' Keybindings:')
    call add(l:lines, '   <Enter>  Open table    ,i  Table info')
    call add(l:lines, '   ,r       Refresh        ,q  Quit VIME')

    call s:SetBufferContent(l:lines)
    call s:SetListKeybindings()
endfunction

function! s:SetListKeybindings() abort
    nnoremap <buffer> <silent> <CR> :call <SID>ListSelectTable()<CR>
    nnoremap <buffer> <silent> ,i :call <SID>ListTableInfo()<CR>
    nnoremap <buffer> <silent> ,r :call <SID>ListRefresh()<CR>
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

" ======================================================================
" Table content buffer
" ======================================================================

function! s:OpenTable(name, head) abort
    let l:resp = s:Send({'cmd': 'table', 'name': a:name, 'head': a:head})

    if type(l:resp) != v:t_dict || !get(l:resp, 'ok', 0)
        echoerr 'VIME: ' . get(l:resp, 'error', 'Failed to read table')
        return
    endif

    call s:CreateScratchBuffer('VIME:' . a:name, 'table')
    let b:vime_table_name = a:name
    let b:vime_columns = get(l:resp, 'columns', [])

    let l:lines = split(l:resp['content'], "\n")

    " Add keybinding hints
    call add(l:lines, '')
    call add(l:lines, ' Keybindings:')
    call add(l:lines, '   :VimePlot <col1> <col2> [scatter]  - Plot columns')
    call add(l:lines, '   ,p  Plot prompt    ,b  Back to list    ,q  Close')
    call add(l:lines, '   ,h  Change row limit    ,a  Show all rows')

    call s:SetBufferContent(l:lines)
    call s:SetTableKeybindings()
endfunction

function! s:SetTableKeybindings() abort
    nnoremap <buffer> <silent> ,p :call <SID>TablePlotPrompt()<CR>
    nnoremap <buffer> <silent> ,b :call <SID>BackToList()<CR>
    nnoremap <buffer> <silent> ,q :call <SID>CloseBuf()<CR>
    nnoremap <buffer> <silent> ,h :call <SID>TableHead()<CR>
    nnoremap <buffer> <silent> ,a :call <SID>TableAll()<CR>
    nnoremap <buffer> <silent> ,i :call <SID>TableInfoCurrent()<CR>
endfunction

function! s:TablePlotPrompt() abort
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
    call s:DoPlot(l:col1, l:col2, l:ptype)
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

function! s:DoPlot(col1, col2, plot_type) abort
    " Try to convert to integers if they look numeric
    let l:c1 = a:col1 =~# '^\d\+$' ? str2nr(a:col1) : a:col1
    let l:c2 = a:col2 =~# '^\d\+$' ? str2nr(a:col2) : a:col2

    let l:resp = s:Send({
        \ 'cmd': 'plot',
        \ 'cols': [l:c1, l:c2],
        \ 'type': a:plot_type,
        \ })

    if type(l:resp) != v:t_dict || !get(l:resp, 'ok', 0)
        echoerr 'VIME: ' . get(l:resp, 'error', 'Failed to generate plot')
        return
    endif

    call s:CreateScratchBuffer('VIME:plot', 'plot')
    let l:lines = split(l:resp['content'], "\n")
    call add(l:lines, '')
    call add(l:lines, ' Keybindings:  ,b  Back to table    ,q  Close')

    call s:SetBufferContent(l:lines)
    call s:SetPlotKeybindings()
endfunction

function! s:SetPlotKeybindings() abort
    nnoremap <buffer> <silent> ,b :call <SID>BackToTable()<CR>
    nnoremap <buffer> <silent> ,q :call <SID>CloseBuf()<CR>
endfunction

" ======================================================================
" Info buffer
" ======================================================================

function! s:ShowInfo(name) abort
    let l:resp = s:Send({'cmd': 'info', 'name': a:name})

    if type(l:resp) != v:t_dict || !get(l:resp, 'ok', 0)
        echoerr 'VIME: ' . get(l:resp, 'error', 'Failed to get info')
        return
    endif

    call s:CreateScratchBuffer('VIME:info:' . a:name, 'info')
    let l:lines = split(l:resp['content'], "\n")
    call add(l:lines, '')
    call add(l:lines, ' Keybindings:  ,b  Back    ,q  Close')

    call s:SetBufferContent(l:lines)
    nnoremap <buffer> <silent> ,b :call <SID>BackToList()<CR>
    nnoremap <buffer> <silent> ,q :call <SID>CloseBuf()<CR>
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
    else
        bwipeout
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
