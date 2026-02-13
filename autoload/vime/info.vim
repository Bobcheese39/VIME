" autoload/vime/info.vim - Info buffer for VIME
" Displays table metadata and column statistics.

scriptencoding utf-8

function! vime#info#show(name) abort
    let l:resp = vime#http#send({'cmd': 'info', 'name': a:name})

    if !vime#http#check_response(l:resp, 'Failed to get info')
        return
    endif

    call vime#buffer#create_scratch('VIME:info:' . a:name, 'info')
    call vime#buffer#render_content(l:resp['content'])
    nnoremap <buffer> <silent> ,b :call vime#nav#back_to_list()<CR>
    nnoremap <buffer> <silent> ,pdb :call vime#debug#toggle()<CR>
    nnoremap <buffer> <silent> ,q :call vime#nav#close_buf()<CR>
    setlocal laststatus=2
    setlocal statusline=%#VimeFooter#\ \ ,b\ Back\ \ │\ \ ,pdb\ Debug\ \ │\ \ ,q\ Close%=
    call vime#colors#apply()
endfunction
