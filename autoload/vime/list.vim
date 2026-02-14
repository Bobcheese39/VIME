" autoload/vime/list.vim - Table list buffer for VIME
" Displays the list of tables in an H5 file and handles compute operations.

scriptencoding utf-8

" Module-private state
let s:list_bufnr = -1
let s:compute_timer = -1

" ======================================================================
" Public functions
" ======================================================================

function! vime#list#open(filepath) abort
    if !vime#http#start_server()
        return
    endif

    let l:resp = vime#http#send({'cmd': 'open', 'file': a:filepath})

    if !vime#http#check_response(l:resp, 'Failed to open file')
        return
    endif

    call vime#state#set('current_file', a:filepath)
    call s:render_table_list(a:filepath, l:resp['tables'])
endfunction

function! vime#list#refresh(...) abort
    let l:file = a:0 >= 1 ? a:1 : vime#state#get('current_file')
    if l:file ==# ''
        echo 'VIME: No file open'
        return
    endif
    if !vime#http#start_server()
        return
    endif

    let l:resp = vime#http#send({'cmd': 'list_tables', 'file': l:file})
    if type(l:resp) == v:t_dict && get(l:resp, 'ok', 0)
        call s:render_table_list(l:file, l:resp['tables'])
        return
    endif

    let l:code = type(l:resp) == v:t_dict ? get(l:resp, 'code', '') : ''
    if l:code ==# 'no_file_open' || l:code ==# 'file_mismatch'
        call vime#list#open(l:file)
        return
    endif
    call vime#http#check_response(l:resp, 'Failed to refresh table list')
endfunction

function! s:render_table_list(filepath, tables) abort
    " Create the table list buffer
    call vime#buffer#create_scratch('VIME:' . fnamemodify(a:filepath, ':t'), 'list')

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
    for l:tbl in a:tables
        let l:name = l:tbl['name']
        let l:rows = l:tbl['rows']
        let l:cols = l:tbl['cols']
        let l:display = printf('   %-40s (%s rows x %s cols)', l:name, l:rows, l:cols)
        call add(l:lines, l:display)
        " Map line number to table name (1-indexed; 1 border + 4 header lines = offset 6)
        let b:vime_table_map[l:idx + 6] = l:name
        let l:idx += 1
    endfor

    let l:lines = vime#buffer#wrap_with_border(l:lines)
    call vime#buffer#set_content(l:lines)
    call s:set_keybindings()
    setlocal laststatus=2
    let b:vime_status = ''
    let s:list_bufnr = bufnr('%')
    setlocal statusline=%#VimeFooter#\ \ ⏎\ Open\ \ │\ \ ,gv\ V-Open\ \ │\ \ ,gh\ H-Open\ \ │\ \ ,s\ Config\ \ │\ \ ,i\ Info\ \ │\ \ ,r\ Refresh\ \ │\ \ ,c\ Compute\ \ │\ \ ,pdb\ Debug\ \ │\ \ ,q\ Quit%=%{get(b:,'vime_status','')}
    call vime#colors#apply()
endfunction

" ======================================================================
" Private helpers
" ======================================================================

function! s:set_keybindings() abort
    nnoremap <buffer> <silent> <CR> :call <SID>select_table()<CR>
    nnoremap <buffer> <silent> ,gv :call <SID>select_table('v')<CR>
    nnoremap <buffer> <silent> ,gh :call <SID>select_table('h')<CR>
    nnoremap <buffer> <silent> ,s :call <SID>open_config()<CR>
    nnoremap <buffer> <silent> ,i :call <SID>table_info()<CR>
    nnoremap <buffer> <silent> ,r :call vime#list#refresh()<CR>
    nnoremap <buffer> <silent> ,c :call <SID>compute_start()<CR>
    nnoremap <buffer> <silent> ,pdb :call vime#debug#toggle()<CR>
    nnoremap <buffer> <silent> ,q :call vime#nav#quit()<CR>
endfunction

function! s:get_table_name_under_cursor() abort
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

function! s:select_table(...) abort
    let l:split = a:0 >= 1 ? a:1 : ''
    let l:name = s:get_table_name_under_cursor()
    if l:name ==# ''
        echo 'VIME: No table under cursor'
        return
    endif
    call vime#table#open(l:name, 100, l:split)
endfunction

function! s:open_config() abort
    let l:plugin_dir = vime#state#get('plugin_dir')
    let l:candidates = [
        \ fnamemodify(l:plugin_dir, ':h') . '/config.cfg',
        \ getcwd() . '/config.cfg'
        \ ]
    for l:path in l:candidates
        if filereadable(l:path)
            execute 'edit ' . fnameescape(fnamemodify(l:path, ':p'))
            return
        endif
    endfor
    echo 'VIME: config.cfg not found'
endfunction

function! s:table_info() abort
    let l:name = s:get_table_name_under_cursor()
    if l:name ==# ''
        echo 'VIME: No table under cursor'
        return
    endif
    call vime#info#show(l:name)
endfunction

function! s:set_status(msg) abort
    if s:list_bufnr > 0 && bufexists(s:list_bufnr)
        call setbufvar(s:list_bufnr, 'vime_status', a:msg)
    endif
    redrawstatus
endfunction

function! s:stop_compute_timer() abort
    if s:compute_timer != -1
        call timer_stop(s:compute_timer)
        let s:compute_timer = -1
    endif
endfunction

function! s:compute_start() abort
    let l:file = vime#state#get('current_file')
    if l:file ==# ''
        echo 'VIME: No file open'
        return
    endif
    let l:resp = vime#http#send({'cmd': 'compute_start'})
    if type(l:resp) != v:t_dict
        echoerr 'VIME: Failed to start compute (server returned unexpected response)'
        return
    endif
    if !get(l:resp, 'ok', 0)
        call s:set_status(get(l:resp, 'error', 'Compute already running'))
        return
    endif
    call s:set_status(get(l:resp, 'message', 'Computing...'))
    call s:stop_compute_timer()
    let s:compute_timer = timer_start(1000, function('s:compute_poll'), {'repeat': -1})
endfunction

function! s:compute_poll(timer_id) abort
    let l:resp = vime#http#send({'cmd': 'compute_status'})
    if type(l:resp) != v:t_dict || !get(l:resp, 'ok', 0)
        call s:set_status('Compute status error')
        call s:stop_compute_timer()
        return
    endif
    let l:status = get(l:resp, 'status', '')
    if l:status ==# 'running'
        call s:set_status(get(l:resp, 'message', 'Computing...'))
        return
    endif
    if l:status ==# 'done'
        let l:name = get(l:resp, 'table', '')
        call s:stop_compute_timer()
        call vime#list#refresh(vime#state#get('current_file'))
        let l:msg = l:name ==# '' ? 'Compute done' : ('Compute done: ' . l:name)
        call s:set_status(l:msg)
        return
    endif
    if l:status ==# 'error'
        let l:err = get(l:resp, 'error', 'Compute failed')
        call s:set_status('Compute failed: ' . l:err)
        call s:stop_compute_timer()
        return
    endif
endfunction
