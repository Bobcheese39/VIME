" autoload/vime/plot.vim - Plot buffer for VIME
" Generates and displays terminal-based plots.

scriptencoding utf-8

function! vime#plot#do_plot(col1, col2, plot_type, ...) abort
    " Optional arg: split direction ('v', 'h', or '' for no split)
    let l:split = a:0 >= 1 ? a:1 : ''

    let l:orig_winid = win_getid()
    let l:orig_bufnr = bufnr('%')

    " Create the buffer first so we can measure the window size
    call vime#buffer#create_scratch('VIME:plot', 'plot', l:split)

    " Measure the new window and compute plot dimensions
    " Border wrapping adds 4 chars width (| + |) and 2 lines (top + bottom)
    " Plot content adds: 2 title lines + 4 axis lines = 6 extra lines
    let l:plot_width = max([20, winwidth(0) - 4])
    let l:plot_height = max([5, winheight(0) - 8])

    let l:resp = vime#http#send({
        \ 'cmd': 'plot',
        \ 'cols': [a:col1, a:col2],
        \ 'type': a:plot_type,
        \ 'width': l:plot_width,
        \ 'height': l:plot_height,
        \ })

    if !vime#http#check_response(l:resp, 'Failed to generate plot')
        return
    endif

    call vime#buffer#render_content(l:resp['content'])
    call s:set_keybindings()
    let b:vime_prev_laststatus = &laststatus
    setlocal statusline=
    let &laststatus = 0
    augroup vime_plot_statusline
        autocmd! * <buffer>
        autocmd BufWinLeave,BufWipeout <buffer> let &laststatus = get(b:, 'vime_prev_laststatus', 2)
    augroup END
    call vime#colors#apply()

    if win_gotoid(l:orig_winid)
        execute 'buffer ' . l:orig_bufnr
    endif
endfunction

" ======================================================================
" Private helpers
" ======================================================================

function! s:set_keybindings() abort
    nnoremap <buffer> <silent> ,b :call vime#nav#back_to_table()<CR>
    nnoremap <buffer> <silent> ,q :call vime#nav#close_buf()<CR>
    nnoremap <buffer> <silent> ,pq :call vime#nav#close_buf()<CR>
    nnoremap <buffer> <silent> ,pdb :call vime#debug#toggle()<CR>
endfunction
