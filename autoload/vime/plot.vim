" autoload/vime/plot.vim - Plot buffer for VIME
" Generates and displays terminal-based plots (async).

scriptencoding utf-8

let s:plot_timer = -1
let s:plot_bufnr = -1
let s:poll_count = 0
let s:orig_winid = -1
let s:orig_bufnr = -1

function! vime#plot#do_plot(col1, col2, plot_type, ...) abort
    let l:split = a:0 >= 1 ? a:1 : ''

    let s:orig_winid = win_getid()
    let s:orig_bufnr = bufnr('%')

    call vime#buffer#create_scratch('VIME:plot', 'plot', l:split)

    let l:plot_width = max([20, winwidth(0) - 4])
    let l:plot_height = max([5, winheight(0) - 8])

    call s:show_loading(0)
    call s:setup_plot_buffer()
    let s:plot_bufnr = bufnr('%')

    let l:resp = vime#http#send({
        \ 'cmd': 'plot_start',
        \ 'cols': [a:col1, a:col2],
        \ 'type': a:plot_type,
        \ 'width': l:plot_width,
        \ 'height': l:plot_height,
        \ })

    if !vime#http#check_response(l:resp, 'Failed to start plot')
        return
    endif

    let s:poll_count = 0
    call s:stop_plot_timer()
    let s:plot_timer = timer_start(500, function('s:plot_poll'), {'repeat': -1})

    if win_gotoid(s:orig_winid)
        execute 'buffer ' . s:orig_bufnr
    endif
endfunction

" ======================================================================
" Private helpers
" ======================================================================

function! s:stop_plot_timer() abort
    if s:plot_timer != -1
        call timer_stop(s:plot_timer)
        let s:plot_timer = -1
    endif
endfunction

function! s:plot_poll(timer_id) abort
    if s:plot_bufnr == -1 || !bufexists(s:plot_bufnr)
        call s:stop_plot_timer()
        return
    endif

    let l:resp = vime#http#send({'cmd': 'plot_status'})
    if type(l:resp) != v:t_dict || !get(l:resp, 'ok', 0)
        call s:stop_plot_timer()
        call s:show_error_in_buf('Plot status error')
        return
    endif

    let l:status = get(l:resp, 'status', '')

    if l:status ==# 'running'
        let s:poll_count += 1
        call s:show_loading(s:poll_count)
        return
    endif

    if l:status ==# 'done'
        call s:stop_plot_timer()
        call s:render_plot_result(l:resp)
        return
    endif

    if l:status ==# 'error'
        call s:stop_plot_timer()
        call s:show_error_in_buf(get(l:resp, 'error', 'Plot failed'))
        return
    endif
endfunction

function! s:show_loading(tick) abort
    let l:dots = repeat('.', (a:tick % 3) + 1)
    let l:pad  = repeat(' ', 3 - ((a:tick % 3) + 1))
    let l:text = 'Loading' . l:dots . l:pad
    let l:lines = vime#buffer#wrap_with_border([l:text])
    call s:set_buf_content(l:lines)
endfunction

function! s:show_error_in_buf(msg) abort
    let l:lines = vime#buffer#wrap_with_border(['Error: ' . a:msg])
    call s:set_buf_content(l:lines)
endfunction

function! s:set_buf_content(lines) abort
    if s:plot_bufnr == -1 || !bufexists(s:plot_bufnr)
        return
    endif
    let l:cur_winid = win_getid()
    let l:plot_win = bufwinid(s:plot_bufnr)
    if l:plot_win == -1
        return
    endif
    call win_gotoid(l:plot_win)
    call vime#buffer#set_content(a:lines)
    call win_gotoid(l:cur_winid)
endfunction

function! s:render_plot_result(resp) abort
    let l:plot_win = bufwinid(s:plot_bufnr)
    if l:plot_win == -1
        return
    endif
    let l:cur_winid = win_getid()
    call win_gotoid(l:plot_win)
    call vime#buffer#render_content(a:resp['content'])
    call s:set_keybindings()
    call vime#colors#apply()
    call win_gotoid(l:cur_winid)
endfunction

function! s:setup_plot_buffer() abort
    call s:set_keybindings()
    let b:vime_prev_laststatus = &laststatus
    setlocal statusline=
    let &laststatus = 0
    augroup vime_plot_statusline
        autocmd! * <buffer>
        autocmd BufWinLeave,BufWipeout <buffer> let &laststatus = get(b:, 'vime_prev_laststatus', 2)
    augroup END
    call vime#colors#apply()
endfunction

function! s:set_keybindings() abort
    nnoremap <buffer> <silent> ,b :call vime#nav#back_to_table()<CR>
    nnoremap <buffer> <silent> ,q :call vime#nav#close_buf()<CR>
    nnoremap <buffer> <silent> ,pq :call vime#nav#close_buf()<CR>
endfunction
