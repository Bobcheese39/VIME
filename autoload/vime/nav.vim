" autoload/vime/nav.vim - Navigation functions for VIME
" Buffer switching, closing, and quit logic.

scriptencoding utf-8

function! vime#nav#back_to_list() abort
    let l:file = vime#state#get('current_file')
    if l:file !=# ''
        call vime#list#refresh(l:file)
    endif
endfunction

function! vime#nav#back_to_table() abort
    " Try to find the table buffer to go back to
    for l:buf in getbufinfo()
        if getbufvar(l:buf.bufnr, 'vime_type', '') ==# 'table'
            execute 'buffer ' . l:buf.bufnr
            return
        endif
    endfor
    " Fallback: go back to list
    call vime#nav#back_to_list()
endfunction

function! vime#nav#close_buf() abort
    " Close current VIME buffer
    let l:type = get(b:, 'vime_type', '')
    if l:type ==# 'list'
        call vime#nav#quit()
    elseif l:type ==# 'debug'
        call vime#debug#reset_bufnr()
        bwipeout
    else
        bwipeout
    endif
endfunction

function! vime#nav#close_plot_buffers() abort
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

function! vime#nav#quit() abort
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
    call vime#debug#reset_bufnr()
    call vime#http#stop_server()
    " Open a new empty buffer so we don't exit vim
    enew
endfunction
