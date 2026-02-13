" autoload/vime/table.vim - Table content buffer for VIME
" Displays table data and provides plot/navigation keybindings.

scriptencoding utf-8

function! vime#table#open(name, head, ...) abort
    let l:split = a:0 >= 1 ? a:1 : ''
    let l:resp = vime#http#send({'cmd': 'table', 'name': a:name, 'head': a:head})

    if !vime#http#check_response(l:resp, 'Failed to read table')
        return
    endif

    call vime#buffer#create_scratch('VIME:' . a:name, 'table', l:split)
    let b:vime_table_name = a:name
    let b:vime_columns = get(l:resp, 'columns', [])

    call vime#buffer#render_content(l:resp['content'])
    call s:set_keybindings()
    setlocal laststatus=2
    setlocal statusline=%#VimeFooter#\ \ ,p\ Plot\ \ │\ \ ,pv\ V-Plot\ \ │\ \ ,ph\ H-Plot\ \ │\ \ ,pq\ Close\ Plot\ \ │\ \ ,b\ Back\ \ │\ \ ,h\ Head\ \ │\ \ ,a\ All\ \ │\ \ ,i\ Info\ \ │\ \ ,pdb\ Debug\ \ │\ \ ,q\ Close%=
    call vime#colors#apply()
endfunction

" ======================================================================
" Private helpers
" ======================================================================

function! s:set_keybindings() abort
    nnoremap <buffer> <silent> ,p :call <SID>plot_prompt()<CR>
    nnoremap <buffer> <silent> ,pv :call <SID>plot_prompt('v')<CR>
    nnoremap <buffer> <silent> ,ph :call <SID>plot_prompt('h')<CR>
    nnoremap <buffer> <silent> ,pq :call vime#nav#close_plot_buffers()<CR>
    nnoremap <buffer> <silent> ,b :call vime#nav#back_to_list()<CR>
    nnoremap <buffer> <silent> ,q :call vime#nav#close_buf()<CR>
    nnoremap <buffer> <silent> ,h :call <SID>head()<CR>
    nnoremap <buffer> <silent> ,a :call <SID>all()<CR>
    nnoremap <buffer> <silent> ,i :call <SID>info_current()<CR>
    nnoremap <buffer> <silent> ,pdb :call vime#debug#toggle()<CR>
endfunction

function! s:plot_prompt(...) abort
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
    call vime#plot#do_plot(l:col1, l:col2, l:ptype, l:split)
endfunction

function! s:head() abort
    let l:n = input('Show first N rows (default 100): ')
    if l:n ==# ''
        let l:n = 100
    else
        let l:n = str2nr(l:n)
    endif
    if exists('b:vime_table_name')
        call vime#table#open(b:vime_table_name, l:n)
    endif
endfunction

function! s:all() abort
    if exists('b:vime_table_name')
        call vime#table#open(b:vime_table_name, 0)
    endif
endfunction

function! s:info_current() abort
    if exists('b:vime_table_name')
        call vime#info#show(b:vime_table_name)
    endif
endfunction
