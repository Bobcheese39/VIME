" autoload/vime/debug.vim - Debug buffer for VIME
" Manages the debug log buffer and its keybindings.

scriptencoding utf-8

" Module-private state
let s:debug_bufnr = -1
let s:debug_lines = []
let s:debug_max_lines = 500

" ======================================================================
" Public functions
" ======================================================================

function! vime#debug#open() abort
    if s:debug_bufnr > 0 && bufexists(s:debug_bufnr)
        let l:winid = bufwinid(s:debug_bufnr)
        if l:winid != -1
            call win_gotoid(l:winid)
            return
        endif
        execute 'new'
        execute 'buffer ' . s:debug_bufnr
        execute 'resize 5'
        call s:set_keybindings()
        setlocal laststatus=2
        setlocal statusline=%#VimeFooter#\ \ ,pdb\ Toggle\ Debug\ \ │\ \ ,q\ Close%=
        call vime#colors#apply()
        return
    endif

    call vime#buffer#create_scratch('VIME:debug', 'debug', 'h')
    let s:debug_bufnr = bufnr('%')
    execute 'resize 5'
    call s:set_keybindings()
    setlocal laststatus=2
    setlocal statusline=%#VimeFooter#\ \ ,pdb\ Toggle\ Debug\ \ │\ \ ,q\ Close%=
    call vime#colors#apply()
    if !empty(s:debug_lines)
        call vime#buffer#set_content(s:debug_lines)
    endif
endfunction

function! vime#debug#toggle() abort
    if s:debug_bufnr > 0 && bufexists(s:debug_bufnr) && bufwinid(s:debug_bufnr) != -1
        execute 'bwipeout ' . s:debug_bufnr
        let s:debug_bufnr = -1
        return
    endif
    call vime#debug#open()
endfunction

function! vime#debug#log(line) abort
    let l:stamp = strftime('%H:%M:%S')
    call add(s:debug_lines, l:stamp . ' ' . a:line)
    let l:overflow = len(s:debug_lines) - s:debug_max_lines
    if l:overflow > 0
        call remove(s:debug_lines, 0, l:overflow - 1)
    endif
    if s:debug_bufnr > 0 && bufexists(s:debug_bufnr)
        call s:append_to_buffer(s:debug_bufnr, l:stamp . ' ' . a:line)
    endif
endfunction

function! vime#debug#reset_bufnr() abort
    let s:debug_bufnr = -1
endfunction

function! vime#debug#bufnr() abort
    return s:debug_bufnr
endfunction

" ======================================================================
" Private helpers
" ======================================================================

function! s:set_keybindings() abort
    nnoremap <buffer> <silent> ,pdb :call vime#debug#toggle()<CR>
    nnoremap <buffer> <silent> ,q :call vime#nav#close_buf()<CR>
endfunction

function! s:append_to_buffer(bufnr, line) abort
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
