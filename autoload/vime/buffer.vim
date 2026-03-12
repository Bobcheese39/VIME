" autoload/vime/buffer.vim - Buffer utilities for VIME
" Shared buffer creation, content setting, and border wrapping.

scriptencoding utf-8

function! vime#buffer#create_scratch(name, type, ...) abort
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
    let b:vime_file = vime#state#get('current_file')
endfunction

function! vime#buffer#set_content(lines) abort
    setlocal modifiable
    silent! %delete _
    call setline(1, a:lines)
    setlocal nomodifiable
    normal! gg
endfunction

function! vime#buffer#render_content(content) abort
    let l:lines = split(a:content, "\n")
    let l:lines = vime#buffer#wrap_with_border(l:lines)
    call vime#buffer#set_content(l:lines)
endfunction

function! vime#buffer#wrap_with_border(lines) abort
    let l:maxw = 0
    for l:line in a:lines
        let l:w = strdisplaywidth(l:line)
        if l:w > l:maxw
            let l:maxw = l:w
        endif
    endfor
    let l:maxw = max([l:maxw, 40])

    let l:result = []
    call add(l:result, '┏' . repeat('━', l:maxw + 2) . '┓')
    for l:line in a:lines
        let l:pad = l:maxw - strdisplaywidth(l:line)
        call add(l:result, '┃ ' . l:line . repeat(' ', l:pad) . ' ┃')
    endfor
    call add(l:result, '┗' . repeat('━', l:maxw + 2) . '┛')
    return l:result
endfunction
